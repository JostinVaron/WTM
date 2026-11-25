#!/usr/bin/env python3
"""Handler pd/pm/pu/ph: S Pen y multi-touch con presion real (F5, 2026-11-25).

server.py despacha aqui con 2 lineas en su dispatch WS:
    elif data.get("t") in ("pd", "pm", "pu", "ph"):
        import pen_handler
        await pen_handler.handle(state, data)

Mensaje del viewer (uno por puntero por muestra, hasta ~120 Hz por las
muestras historicas):
    {"t":"pd|pm|pu|ph", "a":<accion scrcpy>, "pid":0, "x":1234, "y":567,
     "p":0.6250, "pt":"stylus|finger", "tl":0.3, "or":1.2}

"a" usa las acciones del protocolo scrcpy (las que su server ya conoce,
las mismas con que inject_scrcpy_input manda el mouse):
    0=DOWN (nace un puntero, con su pid)   1=UP   2=MOVE   3=CANCEL
    7=HOVER_MOVE (stylus levantado cerca del panel)
El multi-touch no usa POINTER_DOWN/UP: cada puntero nace/muere con su
propio DOWN/UP y su pointer_id estable — exactamente como hace el cliente
scrcpy original al partir un MotionEvent en mensajes por puntero.

"p" viaja al scrcpy tal cual: el control channel la codifica en 16-bit
fixed point, los 4096 niveles del S Pen caben enteros. "tl"/"or"
(tilt/orientacion del stylus) viajan para el backend X11 futuro (hoy el
touch del scrcpy no las inyecta).

Handler STATELESS: la accion la calcula el viewer, aqui no hay estado que
pueda quedar sucio tras una reconexion. En modo X11 scrcpy_control no
tiene writer: send() es no-op seguro.
"""

# Acciones scrcpy permitidas (las que envia PenForwarder.kt).
_ALLOWED_ACT = (0, 1, 2, 3, 7)

# Diagnostico acotado: los 3 primeros mensajes de la sesion, nada mas.
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


def _handle(state, data):
    _srv = _srv_module()
    act = int(data.get("a", -1))
    if act not in _ALLOWED_ACT:
        return
    w, h = state.scrcpy_w, state.scrcpy_h
    if w <= 0 or h <= 0:
        return
    pid = max(0, min(9, int(data.get("pid", 0))))   # ids de puntero virtual del scrcpy
    x = max(0, min(int(w) - 1, int(data.get("x", 0))))
    y = max(0, min(int(h) - 1, int(data.get("y", 0))))
    p = float(data.get("p", 1.0))
    if act == 7:          # hover: como el path del mouse existente
        p = 1.0
    elif act in (1, 3):    # up/cancel: presion 0 (como el path existente)
        p = 0.0
    else:
        if p != p or p < 0.0:      # NaN o negativo -> valor seguro
            p = 1.0
        p = min(1.0, p)
    _srv.scrcpy_control.send(_srv.ctrl_touch(act, x, y, w, h, p, pid))


async def handle(state, data):
    """Entry point del dispatch de server.py para pd/pm/pu/ph."""
    global _seen
    if _seen < 3:
        _seen += 1
        print(f"[pen] msg {data.get('t')} pid={data.get('pid')} "
              f"x={data.get('x')} y={data.get('y')}", flush=True)
    try:
        _handle(state, data)
    except Exception as e:
        print(f"[pen] error: {e}", flush=True)
