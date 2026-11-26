#!/usr/bin/env python3
"""Handler pd/pm/pu/ph: S Pen UHID real + multi-touch por INJECT_TOUCH (F5/F6).

server.py despacha aqui con 3 lineas en su dispatch WS:
    elif data.get("t") in ("pd", "pm", "pu", "ph"):
        import pen_handler
        await pen_handler.handle(state, data)

Mensaje del viewer (uno por puntero por muestra, hasta ~120 Hz por las
muestras historicas):
    {"t":"pd|pm|pu|ph", "a":<accion scrcpy>, "pid":0, "x":1234, "y":567,
     "p":0.6250, "pt":"stylus|finger", "tool":"stylus|eraser|finger",
     "tl":0.3, "or":1.2}

Dos caminos de inyeccion, a proposito:

  * DEDOS -> TYPE_INJECT_TOUCH (el protocolo clasico de scrcpy). El server
    fija toolType=FINGER; es el camino probado y conserva el multi-touch.

  * S PEN -> dispositivo HID virtual por UHID (TYPE_UHID_CREATE/INPUT).
    El protocolo de scrcpy v5 ya transporta HID arbitrario y en Android
    >= 15 el propio UhidManager asocia el dispositivo al display virtual
    por el puerto "scrcpy:<pid>". Android lo ve como un digitizador Pen
    real (INPUT_PROP_DIRECT, BTN_TOOL_PEN, ABS_PRESSURE, ABS_TILT_X/Y):
    toolType=STYLUS, presion de 4096 niveles, tilt y orientacion reales,
    hover y eraser. Esto NO requiere parchear scrcpy ni root: /dev/uhid
    ya lo abre el server con uid shell.

Descriptor HID (Usage Page 0x0D, Usage Pen 0x02) — el kernel lo mapea a:
    Tip Switch -> BTN_TOUCH, In Range -> BTN_TOOL_PEN,
    Eraser -> BTN_TOOL_RUBBER, Barrel Switch -> BTN_STYLUS,
    Tip Pressure -> ABS_PRESSURE, X/Y Tilt -> ABS_TILT_X/Y.
El campo application=Pen es lo que hace que hid-input marque
INPUT_PROP_DIRECT; sin eso Android lo trataria como touchpad (POINTER) y
los eventos no llegarian al display virtual.

Rechazo de palma (cinturon y tirantes):
  - el viewer descarta TOOL_TYPE_PALM y FLAG_CANCELED (Android 13+);
  - aqui, con WTM_PALM_REJECT=1 (default), se descarta cualquier dedo
    mientras haya stylus activo en WTM_PALM_WINDOW_MS (250 ms default).
    Si ese dedo ya estaba abajo, se le manda un UP para no dejarlo pegado.

Ciclo de vida del hover (clave para que el tactil vuelva):
  - HOVER_EXIT del viewer (act 10) saca la pluma de rango al instante;
  - cada hover/UP arma un respaldo de WTM_PEN_OOR_MS (250 ms default)
    por si ese evento se pierde. Sin esto la pluma quedaba en rango para
    siempre y Android suprimia tactil y mouse en el display virtual.

Handler casi stateless: la accion la calcula el viewer. El unico estado
es el dispositivo UHID (se recrea por sesion scrcpy) y el timeout de
fuera-de-rango del hover. En modo X11 scrcpy_control no tiene writer:
send() es no-op seguro.
"""

import asyncio
import math
import os
import struct
import time

# Acciones scrcpy permitidas (las que envia PenForwarder.kt).
# 7 = HOVER_MOVE/ENTER, 10 = HOVER_EXIT (sacada de rango inmediata).
_ALLOWED_ACT = (0, 1, 2, 3, 7, 10)

# --- protocolo de control scrcpy v5: UHID ---------------------------------
CTRL_TYPE_UHID_CREATE = 12
CTRL_TYPE_UHID_INPUT = 13

UHID_ID = 0x50            # id propio; scrcpy usa ids bajos para teclado/mouse
UHID_VID = 0x1209         # pid.codes: vendor neutro, sin quirks de fabricante
UHID_PID = 0x0001
UHID_NAME = b"WTM Pen"

_MAX_PRESSURE = 4095
_MAX_TILT = 90

# Estado del dispositivo y del hover.
_uhid_ready = False
_uhid_session = -1
_uhid_last = (0, 0, 0, 0, False)   # x, y, tilt_x, tilt_y, eraser
_oor_task = None
_stylus_active_until = 0.0
_finger_pids = set()
_seen = 0


def _srv_module():
    """Modulo server REAL del proceso.

    server.py corre como `python3 server.py`, asi que sus globales viven en
    __main__. Un `import server` cargaria una SEGUNDA instancia del modulo con
    su propio scrcpy_control (writer=None): send() descartaria el trazo en
    silencio. Preferimos __main__ y caemos a `server` solo si se importa como
    libreria (tests).
    """
    import sys
    mod = sys.modules.get("__main__")
    if mod is not None and hasattr(mod, "scrcpy_control"):
        return mod
    import server
    return server


def _palm_reject():
    return os.environ.get("WTM_PALM_REJECT", "1").strip().lower() in (
        "1", "true", "yes", "on")


def _palm_window():
    """Ventana (s) en la que un dedo se rechaza tras tocar con el stylus.

    250 ms: cubre la palma que llega con el trazo, pero no bloquea un toque
    deliberado poco despues de levantar el lapiz. La palma explicita la
    filtran TOOL_TYPE_PALM y FLAG_CANCELED.
    """
    try:
        ms = int(os.environ.get("WTM_PALM_WINDOW_MS", "250"))
    except (TypeError, ValueError):
        ms = 250
    return max(0, min(5000, ms)) / 1000.0


def _oor_delay():
    """Respaldo de fuera-de-rango si el viewer no manda HOVER_EXIT.

    250 ms: al alejar el lapiz el tactil vuelve casi de inmediato. El camino
    rapido sigue siendo HOVER_EXIT (inmediato); esto solo cubre el evento
    perdido. Mientras el hover se mueve, cada evento refresca el timer.
    """
    try:
        ms = int(os.environ.get("WTM_PEN_OOR_MS", "250"))
    except (TypeError, ValueError):
        ms = 250
    return max(50, min(5000, ms)) / 1000.0


def _report_desc(w, h):
    """Descriptor HID de un digitizador Pen absoluto.

    X e Y llevan Logical Max independiente: si compartieran el de X, el eje
    Y quedaria comprimido (2000 mapeado a 1200 px). Los 16 bits de presion
    cubren los 4096 niveles del S Pen; el tilt va en grados -90..90.
    """
    xmax = max(1, min(65535, int(w) - 1))
    ymax = max(1, min(65535, int(h) - 1))
    d = bytearray()
    # Usage Page (Digitizers), Usage (Pen), Collection (Application)
    d += bytes((0x05, 0x0D, 0x09, 0x02, 0xA1, 0x01))
    # Tip Switch | In Range | Eraser | Barrel Switch (1 bit cada uno) + padding
    d += bytes((0x09, 0x42, 0x09, 0x32, 0x09, 0x45, 0x09, 0x44,
                0x15, 0x00, 0x25, 0x01, 0x75, 0x01, 0x95, 0x04, 0x81, 0x02,
                0x75, 0x04, 0x95, 0x01, 0x81, 0x03))
    # X absoluta
    d += bytes((0x05, 0x01, 0x09, 0x30, 0x15, 0x00,
                0x26, xmax & 0xFF, (xmax >> 8) & 0xFF,
                0x75, 0x10, 0x95, 0x01, 0x81, 0x02))
    # Y absoluta
    d += bytes((0x09, 0x31, 0x15, 0x00,
                0x26, ymax & 0xFF, (ymax >> 8) & 0xFF,
                0x75, 0x10, 0x95, 0x01, 0x81, 0x02))
    # Tip Pressure 0..4095
    d += bytes((0x05, 0x0D, 0x09, 0x30, 0x15, 0x00, 0x26, 0xFF, 0x0F,
                0x75, 0x10, 0x95, 0x01, 0x81, 0x02))
    # X Tilt / Y Tilt en grados (-90..90), 8 bits con signo
    d += bytes((0x09, 0x3D, 0x09, 0x3E, 0x15, 0xA6, 0x25, 0x5A,
                0x75, 0x08, 0x95, 0x02, 0x81, 0x02))
    d += bytes((0xC0,))
    return bytes(d)


def _report(x, y, pressure, tilt_x, tilt_y, tip, in_range, eraser=False):
    """9 bytes: flags | X u16 | Y u16 | presion u16 | tiltX i8 | tiltY i8.

    HID usa little-endian para los campos multibyte (lo parsea hid-input).
    """
    flags = (1 if tip else 0) | (2 if in_range else 0) | (4 if eraser else 0)
    return struct.pack("<BHHHbb", flags, int(x), int(y), int(pressure),
                       int(tilt_x), int(tilt_y))


def _uhid_create(srv, w, h):
    global _uhid_ready, _uhid_session
    desc = _report_desc(w, h)
    msg = struct.pack(">BHHHB", CTRL_TYPE_UHID_CREATE, UHID_ID,
                      UHID_VID, UHID_PID, len(UHID_NAME)) + UHID_NAME
    msg += struct.pack(">H", len(desc)) + desc
    srv.scrcpy_control.send(msg)
    _uhid_ready = True
    _uhid_session = getattr(srv, "_scrcpy_session", -1)
    print(f"[pen] UHID pen creado {w}x{h} id={UHID_ID} "
          f"desc={len(desc)}B (sesion {_uhid_session})", flush=True)


def _ensure(srv, state):
    """Crea el dispositivo UHID si falta (o si cambio la sesion scrcpy)."""
    if srv.scrcpy_control.writer is None:
        return False
    w = int(getattr(state, "scrcpy_w", 0))
    h = int(getattr(state, "scrcpy_h", 0))
    if w <= 0 or h <= 0:
        return False
    session = getattr(srv, "_scrcpy_session", -1)
    if _uhid_ready and _uhid_session == session:
        return True
    _uhid_create(srv, w, h)
    return True


def _send_report(srv, report):
    srv.scrcpy_control.send(
        struct.pack(">BHH", CTRL_TYPE_UHID_INPUT, UHID_ID, len(report)) + report)


def _tilt_xy(tilt, orientation):
    """(tilt, orientacion) de Android -> X/Y Tilt HID en grados.

    Android entrega tilt (0..pi/2 desde la perpendicular) y orientation
    (azimut). El HID define X/Y Tilt como el angulo de la pluma en cada
    plano. Android los reconstruye con (TouchInputMapper.cpp):
        orientation = atan2(-sin(tiltX), sin(tiltY))
        tilt        = acos(cos(tiltX) * cos(tiltY))

    La inversa exacta: con a=sin(tiltX), b=sin(tiltY) y R^2 = a^2+b^2,
        a = -R*sin(orientacion), b = R*cos(orientacion)
    y la condicion de tilt da  denom*R^4 - R^2 + sin^2(tilt) = 0,
    denom = sin^2(orientacion)*cos^2(orientacion). Se toma la raiz
    pequena (la otra no cabe en |a|,|b| <= 1). Para tilt=0 o azimuth
    recto (denom=0) la solucion es R=sin(tilt). Si la raiz pide R>1
    (tilt casi plano en diagonal), se satura: los dos ejes HID no
    pueden representar ese punto y se conserva la orientacion.
    """
    try:
        t = float(tilt)
    except (TypeError, ValueError):
        t = 0.0
    try:
        o = float(orientation)
    except (TypeError, ValueError):
        o = 0.0
    if not math.isfinite(t) or t < 0.0:
        t = 0.0
    t = min(t, math.pi / 2)
    if not math.isfinite(o):
        o = 0.0
    st = math.sin(t)
    if st < 1e-9:
        return 0, 0
    sphi = math.sin(o)
    cphi = math.cos(o)
    denom = (sphi * cphi) ** 2
    if denom < 1e-12:
        r2 = st * st
    else:
        disc = max(0.0, 1.0 - 4.0 * denom * st * st)
        r2 = (1.0 - math.sqrt(disc)) / (2.0 * denom)
    r2 = min(1.0, max(0.0, r2))
    r = math.sqrt(r2)
    a = max(-1.0, min(1.0, -r * sphi))
    b = max(-1.0, min(1.0, r * cphi))
    tx = int(round(math.degrees(math.asin(a))))
    ty = int(round(math.degrees(math.asin(b))))
    return max(-_MAX_TILT, min(_MAX_TILT, tx)), max(-_MAX_TILT, min(_MAX_TILT, ty))


def _cancel_oor():
    global _oor_task
    if _oor_task is not None and not _oor_task.done():
        _oor_task.cancel()
    _oor_task = None


def _schedule_oor(srv):
    """Arma el fuera-de-rango de respaldo (hover o UP).

    El camino rapido es HOVER_EXIT del viewer (act 10): llega apenas el S Pen
    sale del digitizador y saca la pluma de rango al instante. Este timer
    cubre el caso en que ese evento se pierda. En tests sincronicos no hay
    loop: se omite sin romper.
    """
    global _oor_task
    _cancel_oor()
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _oor_task = loop.create_task(
        _out_of_range_later(srv, _uhid_session, _oor_delay()))


async def _out_of_range_later(srv, session, delay):
    """Saca la pluma de rango si no llega otro evento.

    Sin esto, el hover del telefono se queda congelado en la ultima posicion
    cuando el S Pen se aleja de la tablet (la tablet deja de mandar ph), y
    Android suprime tactil y mouse mientras la pluma esta en rango.
    """
    try:
        await asyncio.sleep(delay)
    except asyncio.CancelledError:
        return
    if not _uhid_ready or _uhid_session != session:
        return
    x, y, tx, ty, eraser = _uhid_last
    _send_report(srv, _report(x, y, 0, tx, ty, False, False, eraser))


def _release_finger(srv, pid, x, y, w, h):
    """Suelta un dedo que ya estaba abajo cuando empezo el rechazo de palma."""
    try:
        srv.scrcpy_control.send(srv.ctrl_touch(1, x, y, w, h, 0.0, pid))
    except Exception:
        pass


def _handle(state, data):
    global _stylus_active_until, _uhid_last
    srv = _srv_module()
    act = int(data.get("a", -1))
    if act not in _ALLOWED_ACT:
        return
    w, h = state.scrcpy_w, state.scrcpy_h
    if w <= 0 or h <= 0:
        return
    pid = max(0, min(9, int(data.get("pid", 0))))   # ids de puntero virtual del scrcpy
    x = max(0, min(int(w) - 1, int(data.get("x", 0))))
    y = max(0, min(int(h) - 1, int(data.get("y", 0))))
    tool = str(data.get("tool") or data.get("pt") or "finger")
    if tool == "palm":                 # el viewer ya lo descarta; cinturon
        return
    is_pen = tool in ("stylus", "eraser")

    if is_pen:
        # HOVER_EXIT (act 10): el S Pen salio del digitizador. Sacarlo de
        # rango YA: mientras siga en rango, Android suprime tactil y mouse
        # en el display virtual (igual que una Galaxy Tab real con EMR).
        if act == 10:
            # La pluma salio del digitizador: se acaba el bloqueo de dedos y
            # la pluma sale de rango al instante.
            _stylus_active_until = 0.0
            _cancel_oor()
            if _uhid_ready and _uhid_session == getattr(
                    srv, "_scrcpy_session", -1):
                x0, y0, tx0, ty0, er0 = _uhid_last
                _send_report(srv, _report(x0, y0, 0, tx0, ty0,
                                          False, False, er0))
            return
        if not _ensure(srv, state):
            return
        # Down/move tocan: no sacar de rango. Tras UP/CANCEL o hover, armar
        # el respaldo: si el viewer no manda HOVER_EXIT, el tactil vuelve.
        if act in (0, 2):
            _cancel_oor()
        elif act in (1, 3, 7):
            _schedule_oor(srv)
        # La pluma EN RANGO (contacto o hover) bloquea dedos; el HOVER_EXIT
        # (act 10) lo libera. Asi la palma apoyada entre trazos no pasa.
        if act in (0, 1, 2, 3, 7):
            _stylus_active_until = time.monotonic() + _palm_window()
        p = float(data.get("p", 1.0))
        if not math.isfinite(p) or p < 0.0:
            p = 0.0
        p = min(1.0, p)
        tip = act in (0, 2)            # down/move tocan; up y hover no
        pressure = int(round(p * _MAX_PRESSURE)) if tip else 0
        tx, ty = _tilt_xy(data.get("tl", 0.0), data.get("or", 0.0))
        eraser = tool == "eraser"
        _send_report(srv, _report(x, y, pressure, tx, ty, tip, True, eraser))
        _uhid_last = (x, y, tx, ty, eraser)
        return

    # --- dedo ------------------------------------------------------------
    if _palm_reject() and time.monotonic() < _stylus_active_until:
        if pid in _finger_pids:
            # estaba abajo antes del stylus: soltarlo para no dejarlo pegado
            _release_finger(srv, pid, x, y, w, h)
            _finger_pids.discard(pid)
        return
    if act == 0:
        _finger_pids.add(pid)
    elif act in (1, 3):
        _finger_pids.discard(pid)

    if act == 7:          # hover: como el path del mouse existente
        p = 1.0
    elif act in (1, 3):   # up/cancel: presion 0 (como el path existente)
        p = 0.0
    else:
        p = float(data.get("p", 1.0))
        if p != p or p < 0.0:      # NaN o negativo -> valor seguro
            p = 1.0
        p = min(1.0, p)
    srv.scrcpy_control.send(srv.ctrl_touch(act, x, y, w, h, p, pid))


async def synthetic_test(state, seconds=3.0):
    """Trazo sintetico por el MISMO camino UHID (para getevent/PenProbe).

    Se dispara con {"t":"pen_test"} por el WebSocket autenticado. Permite
    verificar el dispositivo y la presion sin depender de que alguien dibuje.
    """
    srv = _srv_module()
    if not _ensure(srv, state):
        print("[pen] test: display o control no listos", flush=True)
        return
    w, h = state.scrcpy_w, state.scrcpy_h
    t0 = time.monotonic()
    n = 0
    x = y = 0
    while time.monotonic() - t0 < seconds:
        u = (time.monotonic() - t0) / seconds
        x = int(w * (0.10 + 0.80 * u))
        y = int(h * (0.50 + 0.30 * math.sin(u * math.pi * 6)))
        p = int(round((0.05 + 0.95 * abs(math.sin(u * math.pi * 4)))
                      * _MAX_PRESSURE))
        tip = u < 0.90
        _send_report(srv, _report(x, y, p if tip else 0, 30, 0, tip, True))
        n += 1
        await asyncio.sleep(1.0 / 60.0)
    _send_report(srv, _report(x, y, 0, 30, 0, False, False))
    print(f"[pen] test sintetico: {n} reportes UHID", flush=True)


async def handle(state, data):
    """Entry point del dispatch de server.py para pd/pm/pu/ph."""
    global _seen
    if _seen < 60:
        _seen += 1
        print(f"[pen] msg {data.get('t')} pid={data.get('pid')} "
              f"x={data.get('x')} y={data.get('y')} tool={data.get('tool') or data.get('pt')}",
              flush=True)
    try:
        _handle(state, data)
    except Exception as e:
        print(f"[pen] error: {e}", flush=True)
