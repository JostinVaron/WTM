#!/usr/bin/env python3
"""
mouse-route.py — enruta el mouse Bluetooth físico del teléfono al display virtual del DeX.

Android dirige los periféricos emparejados con el teléfono hacia su pantalla física
y no existe ningún comando para asociarlos dinámicamente a un display virtual (las
asociaciones input->display de /vendor/etc/input-port-associations.xml son estáticas;
AOSP: "dynamic associations are not yet enabled"). Este enrutador lo resuelve sin
root y sin APIs ocultas:

  1. Detecta los nodos /dev/input/eventN tipo mouse via `adb shell getevent -lp`
     (los nodos con BTN_LEFT y REL_X; los teclados y el touch no cumplen eso).
  2. Lee sus eventos en bruto con `adb shell getevent -lt <nodo>`.
  3. Los reenvia (movimiento relativo, botones, rueda) al endpoint local del
     bridge (POST /api/mouse), que los inyecta al display virtual a traves del
     socket de control de scrcpy — el mismo camino que el touch de la tablet.

Notas:
- Los eventos NO se consumen: el puntero seguira moviéndose también en la
  pantalla física del teléfono (inofensivo e inevitable sin root).
- Si no hay ningún mouse conectado, espera y arranca solo cuando lo enciendas.
- El bridge debe estar parcheado con el endpoint /api/mouse (tools/patch-mouse-endpoint.py).

Config (env o ~/.config/wtm/server.env): PORT, WTM_BIND_HOST, MOUSE_SENS,
MOUSE_FLUSH_MS, WTM_PHONE_MODEL. Detenlo con Ctrl+C o con el widget stop-dex.
"""
import http.client
import json
import os
import re
import ssl
import subprocess
import sys
import threading
import time

PREFIX = "/data/data/com.termux/files/usr"
ADB = f"{PREFIX}/bin/adb"


def log(msg):
    print(f"[mouse-route] {msg}", flush=True)


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


def find_mouse_nodes(serial):
    """Nodos tipo mouse: bloques de getevent -lp con BTN_LEFT y REL_X (un teclado
    Bluetooth no tiene REL_X; la pantalla táctil no tiene BTN_LEFT ni REL)."""
    out = subprocess.run([ADB, "-s", serial, "shell", "getevent", "-lp"],
                          capture_output=True, text=True).stdout
    nodes, node, has_btn, has_rel = [], None, False, False
    for line in out.splitlines():
        match = re.match(r"add device.*?:\s+(/dev/input/event\d+)", line.strip())
        if match:
            if node and has_btn and has_rel:
                nodes.append(node)
            node, has_btn, has_rel = match.group(1), False, False
        elif node:
            if "BTN_LEFT" in line:
                has_btn = True
            if "REL_X" in line:
                has_rel = True
    if node and has_btn and has_rel:
        nodes.append(node)
    return nodes


def signed16(hexval):
    value = int(hexval, 16)
    return value - 0x10000 if value > 0x7FFF else value


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
        # convención del cliente web: 1=izquierdo, 2=central, 3=derecho
        self.btn = {"BTN_LEFT": 1, "BTN_MIDDLE": 2, "BTN_RIGHT": 3}

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

    def watch_node(self, node):
        pattern = re.compile(
            r"\[\s*[\d.]+\]\s+\S+:\s+(EV_REL|EV_KEY)\s+"
            r"(REL_X|REL_Y|REL_WHEEL|BTN_LEFT|BTN_RIGHT|BTN_MIDDLE)\s+"
            r"([0-9a-fA-F]+|DOWN|UP)")
        while True:
            proc = subprocess.Popen(
                [ADB, "-s", self.serial, "shell", "getevent", "-lt", node],
                stdout=subprocess.PIPE, text=True, bufsize=1)
            log(f"escuchando {node}")
            for raw in proc.stdout:
                match = pattern.match(raw.strip())
                if not match:
                    continue
                kind, code, val = match.group(1), match.group(2), match.group(3)
                if kind == "EV_REL":
                    value = signed16(val)
                    if code == "REL_X":
                        with self.moves:
                            self.dx += value
                    elif code == "REL_Y":
                        with self.moves:
                            self.dy += value
                    elif code == "REL_WHEEL":
                        # REL_WHEEL positivo = rueda hacia arriba (AXIS_VSCROLL positivo)
                        self.bridge.post({"t": "wheel",
                                          "d": max(-1, min(1, value)), "n": 1})
                else:
                    if val == "DOWN":
                        down = True
                    elif val == "UP":
                        down = False
                    else:
                        down = val != "00000000"
                    self.bridge.post({"t": "mbtn", "b": self.btn[code], "down": down})
            code = proc.wait()
            log(f"{node} se cerró (exit {code}); reintento en 2 s")
            time.sleep(2)


def main():
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

    nodes = find_mouse_nodes(serial)
    while not nodes:
        log("no hay ningún mouse conectado al teléfono; esperando (enciende el mouse)…")
        time.sleep(5)
        nodes = find_mouse_nodes(serial)

    log(f"mouse detectado en {', '.join(nodes)}")
    router = Router(serial, Bridge(host, port), sens, flush_ms)
    threading.Thread(target=router.flush_moves, daemon=True).start()
    for node in nodes:
        threading.Thread(target=router.watch_node, args=(node,), daemon=True).start()
    log("enrutando: mueve el mouse Bluetooth y verás el cursor en el DeX")
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("detenido")
