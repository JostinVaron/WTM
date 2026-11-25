#!/usr/bin/env python3
"""Handler pd/pm/pu/ph: S Pen y multi-touch con presion real (F5, 2026-11-25).

server.py despacha aqui con 2 lineas en su dispatch WS:
    elif data.get("t") in ("pd", "pm", "pu", "ph"):
        import pen_handler
        await pen_handler.handle(state, data)

Mensaje del viewer (uno por pointer por muestra, ~120 Hz):
    {"t":"pd|pm|pu|ph", "a":0, "pid":0, "x":1234, "y":567, "p":0.6250,
     "pt":"stylus|finger", "tl":0.3, "or":1.2}

x,y son pixeles de la pantalla del emisor (el viewer ya normalizo con el
config del stream). "a" es la accion enmascarada de MotionEvent que el
viewer calcula (0=DOWN, 1=UP, 2=MOVE, 3=CANCEL, 5=POINTER_DOWN, 6=POINTER_UP,
10=HOVER_MOVE): el handler es STATELESS — sin estado compartido que pueda
quedar sucio tras una reconexion.

"p" viaja al scrcpy tal cual: el control channel la codifica en 16-bit
fixed point (los 4096 niveles del S Pen caben enteros). "tl"/"or"
(tilt/orientacion del stylus) viajan para futuros backends; el touch del
scrcpy todavia no las inyecta.

Import de server: lazy — este modulo se importa con server.py ya corriendo,
así comparte ctrl_touch y scrcpy_control con inject_scrcpy_input. En modo X11
scrcpy_control no tiene writer: los mensajes se descartan solos (no-op).
"""

# Acciones enmascaradas permitidas (las que envia PenForwarder.kt).
_ALLOWED_ACT = (0, 1, 2, 3, 5, 6, 10)


def _handle(state, data):
    import server as _srv
    act = int(data.get("a", -1))
    if act not in _ALLOWED_ACT:
        return
    w, h = state.scrcpy_w, state.scrcpy_h
    pid = max(0, min(9, int(data.get("pid", 0))))   # ids de dedo virtual del scrcpy
    x = int(data.get("x", 0))
    y = int(data.get("y", 0))
    p = float(data.get("p", 1.0))
    if p != p or p < 0.0:                            # NaN o negativo -> valor seguro
        p = 0.0 if act in (1, 3) else 1.0
    p = min(1.0, p)
    if act in (1, 3):                                # up/cancel: presion 0 (como el path existente)
        p = 0.0
    elif act == 10:                                  # hover: como el hover del mouse existente
        p = 1.0
    elif act in (0, 5) and p <= 0.0:                 # un down con p=0 puede ignorarse arriba
        p = 1.0
    _srv.scrcpy_control.send(_srv.ctrl_touch(act, x, y, w, h, p, pid))


async def handle(state, data):
    """Entry point del dispatch de server.py para pd/pm/pu/ph."""
    try:
        _handle(state, data)
    except Exception as e:
        print(f"[pen] error: {e}", flush=True)
