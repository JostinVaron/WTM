#!/usr/bin/env python3
"""
mouse-route.py — enruta el mouse Bluetooth físico del teléfono al display
virtual del DeX SIN movimiento fantasma en la pantalla del teléfono.

Dos modos:

  GRAB (por defecto): ejecuta native/mouse-grab vía adb shell. Ese helper hace
  EVIOCGRAB sobre el nodo /dev/input/eventN del mouse: el kernel entrega los
  eventos EN EXCLUSIVA al enrutador — Android deja de verlos, así que el
  puntero del S25 NO se mueve, no hay clics perdidos en el teléfono y la
  pantalla no despierta sola. Al detenerse el DeX, el grab se libera solo y
  el mouse vuelve a su comportamiento normal automáticamente.

  LEGACY (fallback): `adb shell getevent -lt` COPIA los eventos sin
  consumirlos; en este modo el puntero TAMBIÉN se mueve en la pantalla física
  del teléfono (fantasma). Solo se usa si EVIOCGRAB es denegado por el
  dispositivo, y el log lo avisa claramente.

Formatos verificados en un S25 Android 16 (S931B):
  - getevent -lp etiqueta el botón izquierdo como BTN_MOUSE (no BTN_LEFT).
  - Un mouse real expone BTN_MOUSE/BTN_RIGHT/BTN_MIDDLE junto a REL_X/REL_Y;
    su mitad "Keyboard" de un combo BT es un nodo separado sin REL_X.
  - getevent -lt imprime valores hex de 32 bits con signo (ffffffff = -1) y
    la línea NO lleva nombre de dispositivo:
      "[   73280.294576] EV_REL       REL_X                00000027"
  - El grabber emite registros binarios canónicos de 8 bytes (type/code/value),
    sin ambigüedad de texto; heartbeat con type=0xFFFF.

Ciclo de vida: lo arranca dex.sh al iniciar el DeX y muere solo cuando el DeX
se detiene (marca rd-dex-stopped que escribe stop-dex.sh). Si el mouse está
apagado, espera y arranca cuando lo enciendas.

El bridge debe tener el endpoint /api/mouse (tools/patch-mouse-endpoint.py;
el instalador lo aplica automáticamente).

Config (env o ~/.config/wtm/server.env): PORT, WTM_BIND_HOST, MOUSE_SENS,
MOUSE_FLUSH_MS, WTM_PHONE_MODEL. Desactiva el enrutado con MOUSE_ROUTE=0.
"""
import atexit
import http.client
import json
import os
import re
import signal
import ssl
import struct
import subprocess
import sys
import threading
import time

PREFIX = "/data/data/com.termux/files/usr"
ADB = f"{PREFIX}/bin/adb"
STOP_FLAG = f"{PREFIX}/tmp/rd-dex-stopped"
PID_FILE = f"{PREFIX}/tmp/rd-mouse-route.pid"
HERE = os.path.dirname(os.path.abspath(__file__))
GRAB_LOCAL = os.path.join(HERE, "native", "mouse-grab")
GRAB_REMOTE = "/data/local/tmp/wtm-mouse-grab"

# Códigos del kernel para el modo GRAB (registros binarios del grabber).
EV_KEY, EV_REL = 1, 2
REL_X, REL_Y, REL_WHEEL = 0, 1, 8
BTN_MOUSE, BTN_RIGHT, BTN_MIDDLE = 272, 273, 274   # 0x110, 0x111, 0x112
GRAB_BTN_TO_WEB = {BTN_MOUSE: 1, BTN_MIDDLE: 2, BTN_RIGHT: 3}  # 1=izq 2=central 3=der


def log(msg):
    print(f"[mouse-route] {msg}", flush=True)


def dex_stopped():
    return os.path.exists(STOP_FLAG)


def load_env():
    cfg = {}
    env_file = os.path.join(os.environ.get("HOME", ""), ".config", "wtm", "server.env")
    try:
        with open(env_file, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    cfg[key.strip()] = value.strip()
    except OSError:
        pass
    # el entorno real gana a server.env
    for key in ("PORT", "WTM_BIND_HOST", "WTM_PHONE_MODEL", "MOUSE_SENS", "MOUSE_FLUSH_MS"):
        if key in os.environ:
            cfg[key] = os.environ[key]
    return cfg


def find_phone(phone_model):
    out = subprocess.run([ADB, "devices"], capture_output=True, text=True).stdout
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            serial = parts[0]
            model = subprocess.run(
                [ADB, "-s", serial, "shell", "getprop", "ro.product.model"],
                capture_output=True, text=True).stdout.strip()
            if model == phone_model:
                return serial
    return None


MOUSE_CODES = {"BTN_MOUSE", "BTN_LEFT", "BTN_RIGHT", "BTN_MIDDLE",
               "BTN_SIDE", "BTN_EXTRA", "BTN_FORWARD", "BTN_BACK", "BTN_TASK"}


def find_mouse_nodes(serial):
    """Nodos tipo mouse: bloques de getevent -lp con un botón de mouse
    (BTN_MOUSE/BTN_LEFT/BTN_RIGHT/...) y ejes relativos REL_X y REL_Y."""
    out = subprocess.run([ADB, "-s", serial, "shell", "getevent", "-lp"],
                          capture_output=True, text=True).stdout
    nodes, node, has_btn, has_relx, has_rely = [], None, False, False, False
    for line in out.splitlines():
        match = re.match(r"add device.*?:\s+(/dev/input/event\d+)", line.strip())
        if match:
            if node and has_btn and has_relx and has_rely:
                nodes.append(node)
            node = match.group(1)
            has_btn = has_relx = has_rely = False
        elif node:
            for code in MOUSE_CODES:
                if re.search(rf"\b{code}\b", line):
                    has_btn = True
                    break
            if re.search(r"\bREL_X\b", line):
                has_relx = True
            if re.search(r"\bREL_Y\b", line):
                has_rely = True
    if node and has_btn and has_relx and has_rely:
        nodes.append(node)
    return nodes


def push_grabber(serial):
    """Sube el helper compilado al teléfono (via /data/local/tmp)."""
    if not os.path.isfile(GRAB_LOCAL):
        log(f"no hay grabber compilado ({GRAB_LOCAL}); se usará el modo legacy")
        return False
    result = subprocess.run([ADB, "-s", serial, "push", GRAB_LOCAL, GRAB_REMOTE],
                            capture_output=True, text=True)
    if result.returncode != 0:
        log(f"no se pudo subir el grabber: {result.stderr.strip()}")
        return False
    subprocess.run([ADB, "-s", serial, "shell", "chmod", "755", GRAB_REMOTE],
                    capture_output=True)
    return True


def signed32(hexval):
    """getevent imprime valores hexadecimales de 32 bits con signo (legacy)."""
    value = int(hexval, 16)
    return value - 0x100000000 if value > 0x7FFFFFFF else value


class Bridge:
    """Cliente HTTPS persistente del endpoint local /api/mouse del bridge."""

    def __init__(self, host, port):
        self.host, self.port = host, port
        self.ctx = ssl._create_unverified_context()
        self.conn = None
        self.lock = threading.Lock()

    def post(self, obj):
        body = json.dumps(obj).encode()
        with self.lock:
            for attempt in (0, 1):
                try:
                    if self.conn is None:
                        self.conn = http.client.HTTPSConnection(
                            self.host, self.port, context=self.ctx, timeout=4)
                    self.conn.request("POST", "/api/mouse", body,
                                      {"Content-Type": "application/json"})
                    resp = self.conn.getresponse()
                    resp.read()
                    if resp.status == 200:
                        return
                    log(f"bridge rechazó el evento (HTTP {resp.status}); "
                        "¿server.py tiene el endpoint /api/mouse (parche)?")
                    self.conn.close()
                    self.conn = None
                    return
                except OSError as exc:
                    self.conn = None
                    if attempt == 0:
                        continue
                    log(f"bridge no accesible ({self.host}:{self.port}): {exc}")


class Router:
    def __init__(self, serial, bridge, sens, flush_ms):
        self.serial = serial
        self.bridge = bridge
        self.sens = sens
        self.flush_ms = flush_ms
        self.dx = 0
        self.dy = 0
        self.moves = threading.Lock()
        self.grab_procs = []
        # convención del cliente web: 1=izquierdo, 2=central, 3=derecho
        self.btn = {"BTN_MOUSE": 1, "BTN_LEFT": 1, "BTN_MIDDLE": 2, "BTN_RIGHT": 3}

    # --- común ------------------------------------------------------------
    def apply_move(self, dx, dy):
        with self.moves:
            self.dx += dx
            self.dy += dy

    def flush_moves(self):
        while True:
            time.sleep(self.flush_ms / 1000.0)
            with self.moves:
                dx, dy = self.dx, self.dy
                if dx or dy:
                    self.dx = self.dy = 0
            if dx or dy:
                self.bridge.post({"t": "mdx",
                                  "dx": round(dx * self.sens),
                                  "dy": round(dy * self.sens)})

    # --- modo GRAB (sin fantasma) ------------------------------------------
    def _drain_stderr(self, stream, node):
        try:
            for line in stream:
                text = line.decode("utf-8", "replace").strip()
                if text:
                    log(f"grabber[{node}]: {text}")
        except Exception:
            pass

    def _dispatch(self, typ, code, val):
        if typ == EV_REL:
            if code == REL_X:
                self.apply_move(val, 0)
            elif code == REL_Y:
                self.apply_move(0, val)
            elif code == REL_WHEEL:
                # REL_WHEEL positivo = rueda hacia arriba (AXIS_VSCROLL positivo)
                self.bridge.post({"t": "wheel", "d": max(-1, min(1, val)), "n": 1})
        elif typ == EV_KEY and code in GRAB_BTN_TO_WEB:
            self.bridge.post({"t": "mbtn", "b": GRAB_BTN_TO_WEB[code], "down": bool(val)})

    def watch_grab(self, node):
        """Eventos en EXCLUSIVA (EVIOCGRAB): el puntero del teléfono NO se
        mueve. Si el kernel/SELinux niega el grab (exit 3), cae al modo legacy
        avisando del fantasma."""
        while True:
            if dex_stopped():
                return
            proc = subprocess.Popen(
                [ADB, "-s", self.serial, "shell", f"{GRAB_REMOTE} {node}"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.grab_procs.append(proc)
            threading.Thread(target=self._drain_stderr, args=(proc.stderr, node),
                             daemon=True).start()
            log(f"grab exclusivo activo en {node}: el mouse ya no mueve la pantalla del teléfono")
            buf = b""
            while True:
                chunk = proc.stdout.read(4096)
                if not chunk:
                    break
                buf += chunk
                while len(buf) >= 8:
                    rec, buf = buf[:8], buf[8:]
                    typ, code, val = struct.unpack("<HHi", rec)
                    if typ == 0xFFFF:      # heartbeat del grabber
                        continue
                    self._dispatch(typ, code, val)
            code = proc.wait()
            try:
                self.grab_procs.remove(proc)
            except ValueError:
                pass
            if code == 3:
                log(f"EVIOCGRAB denegado en {node} (permisos del dispositivo); "
                    "cayendo al modo LEGACY: el puntero TAMBIÉN se moverá en la "
                    "pantalla del teléfono.")
                self.watch_node(node)   # legacy: bucle infinito
                return
            if dex_stopped():
                return
            log(f"grabber de {node} terminó (exit {code}); reintentando en 2 s")
            time.sleep(2)

    # --- modo LEGACY (getevent, con fantasma) -------------------------------
    def watch_node(self, node):
        # Formato verificado en Android 16 (S25): la línea NO lleva nombre de
        # dispositivo; va "[ timestamp] EV_REL       REL_X                valor".
        pattern = re.compile(
            r"\[\s*[\d.]+\]\s+(EV_REL|EV_KEY)\s+"
            r"(REL_X|REL_Y|REL_WHEEL|REL_HWHEEL|BTN_MOUSE|BTN_LEFT|BTN_RIGHT|BTN_MIDDLE|"
            r"BTN_SIDE|BTN_EXTRA|BTN_FORWARD|BTN_BACK|BTN_TASK)\s+"
            r"([0-9a-fA-F]+|DOWN|UP)")
        while True:
            proc = subprocess.Popen(
                [ADB, "-s", self.serial, "shell", "getevent", "-lt", node],
                stdout=subprocess.PIPE, text=True, bufsize=1)
            log(f"escuchando {node} (legacy)")
            unmatched = 0
            for raw in proc.stdout:
                line = raw.strip()
                match = pattern.match(line)
                if not match:
                    if unmatched < 3 and ("EV_REL" in line or "EV_KEY" in line):
                        log(f"evento no reconocido (muestra): {line}")
                        unmatched += 1
                    continue
                kind, code, val = match.group(1), match.group(2), match.group(3)
                if kind == "EV_REL":
                    if code == "REL_X":
                        self.apply_move(signed32(val), 0)
                    elif code == "REL_Y":
                        self.apply_move(0, signed32(val))
                    elif code == "REL_WHEEL":
                        self.bridge.post({"t": "wheel",
                                          "d": max(-1, min(1, signed32(val))), "n": 1})
                else:
                    if code not in self.btn:
                        continue
                    if val == "DOWN":
                        down = True
                    elif val == "UP":
                        down = False
                    else:
                        down = val != "00000000"
                    self.bridge.post({"t": "mbtn", "b": self.btn[code], "down": down})
            code = proc.wait()
            if dex_stopped():
                return
            log(f"{node} se cerró (exit {code}); reintento en 2 s")
            time.sleep(2)


def main():
    try:
        with open(PID_FILE, "w", encoding="ascii") as handle:
            handle.write(str(os.getpid()))
    except OSError:
        pass

    cfg = load_env()
    host = cfg.get("WTM_BIND_HOST") or "127.0.0.1"
    port = int(cfg.get("PORT", "8443"))
    phone_model = cfg.get("WTM_PHONE_MODEL", "SM-S931B")
    sens = float(cfg.get("MOUSE_SENS", "1.0"))
    flush_ms = float(cfg.get("MOUSE_FLUSH_MS", "12"))

    serial = find_phone(phone_model)
    if not serial:
        log(f"no se encontró el teléfono ({phone_model}) por adb")
        sys.exit(1)
    log(f"teléfono {serial}; bridge https://{host}:{port}")

    grab_ok = push_grabber(serial)

    nodes = []
    while not nodes:
        if dex_stopped():
            log("el DeX se detuvo antes de arrancar el enrutado; saliendo.")
            sys.exit(0)
        nodes = find_mouse_nodes(serial)
        if not nodes:
            log("no hay ningún mouse conectado al teléfono; esperando (enciende el mouse)…")
            time.sleep(5)

    log(f"mouse detectado en {', '.join(nodes)}")
    router = Router(serial, Bridge(host, port), sens, flush_ms)

    # Al morir este proceso (stop-dex o marca de parada), terminar los adb del
    # grabber: cerrar su fd libera el EVIOCGRAB y el mouse vuelve al teléfono.
    def release_grabs():
        for proc in list(router.grab_procs):
            try:
                proc.terminate()
            except Exception:
                pass
    atexit.register(release_grabs)

    def on_term(_signum, _frame):
        log("deteniendo el enrutador: liberando el grab del mouse")
        release_grabs()
        sys.exit(0)
    signal.signal(signal.SIGTERM, on_term)
    signal.signal(signal.SIGINT, on_term)

    threading.Thread(target=router.flush_moves, daemon=True).start()
    for node in nodes:
        if grab_ok:
            threading.Thread(target=router.watch_grab, args=(node,), daemon=True).start()
        else:
            log("modo LEGACY (getevent): el puntero TAMBIÉN se moverá en la pantalla del teléfono.")
            threading.Thread(target=router.watch_node, args=(node,), daemon=True).start()
    log("enrutando: mueve el mouse Bluetooth y verás el cursor en el DeX")
    while True:
        time.sleep(5)
        if dex_stopped():
            log("el DeX se detuvo; liberando el grab (el mouse vuelve al teléfono).")
            release_grabs()
            sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("detenido")
