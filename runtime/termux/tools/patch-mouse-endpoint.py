#!/usr/bin/env python3
"""
patch-mouse-endpoint.py — parche IDEMPOTENTE que añade a server.py el soporte de
enrutado del mouse físico del teléfono (feature restaurada del viejo "DeX Tahoe").

Añade, con anclas exactas (falla si no las encuentra o si no son únicas):
  1. Ruta    : POST /api/mouse
  2. Seguridad: solo peers locales (loopback o la IP vinculada), sin cookie —
     es el proceso local mouse-route.py quien lo usa, nunca un cliente remoto.
  3. Handler : api_mouse_handler con rate-limit (240 eventos/s).
  4. Inyección: tipos "mdx" (movimiento relativo) y "mbtn" (botones) en
     inject_scrcpy_input, reutilizando el puntero mouse existente de scrcpy.

TEMPORAL: una vez validado en hardware, server.py se confirma completo en el
repo y este parche se elimina. Uso:
    python3 tools/patch-mouse-endpoint.py <ruta/a/server.py>
"""
import hashlib
import py_compile
import sys

ROUTE_OLD = '''    app.router.add_post("/api/control", api_control_handler)
    app.router.add_post("/log", log_handler)
    app.on_startup.append(on_startup)'''
ROUTE_NEW = '''    app.router.add_post("/api/control", api_control_handler)
    app.router.add_post("/log", log_handler)
    app.router.add_post("/api/mouse", api_mouse_handler)
    app.on_startup.append(on_startup)'''

MIDDLEWARE_OLD = '''            raise web.HTTPForbidden(text="Same-origin HTTPS request required")
    else:
        public_paths = {"/login", "/manifest.json", "/icon.svg"}'''
MIDDLEWARE_NEW = '''            raise web.HTTPForbidden(text="Same-origin HTTPS request required")
    elif request.path == "/api/mouse":
        # Solo el enrutador de mouse de ESTE teléfono (proceso local) puede
        # inyectar eventos de puntero físico; nada remoto. Sin cookie: la
        # restricción es el peer local (loopback o la propia IP vinculada).
        if request.method != "POST" or not is_local_peer(request):
            raise web.HTTPForbidden(text="Local machine mouse router only")
    else:
        public_paths = {"/login", "/manifest.json", "/icon.svg"}'''

HANDLER_OLD = '''async def ws_handler(request):'''
HANDLER_NEW = '''MOUSE_RATE_LIMIT_PER_S = 240
_mouse_events = {"t": 0.0, "n": 0}


async def api_mouse_handler(request):
    """Puntero físico del propio teléfono: el mouse Bluetooth se captura con
    `adb shell getevent` (mouse-route.py) y se reenvía aquí como deltas/botones;
    se inyectan al display virtual vía el socket de control de scrcpy."""
    now = time.monotonic()
    if now - _mouse_events["t"] > 1.0:
        _mouse_events["t"] = now
        _mouse_events["n"] = 0
    if _mouse_events["n"] >= MOUSE_RATE_LIMIT_PER_S:
        raise web.HTTPTooManyRequests(text="Mouse event rate limit exceeded")
    _mouse_events["n"] += 1
    if request.content_type != "application/json":
        raise web.HTTPUnsupportedMediaType(text="JSON request body required")
    try:
        data = await request.json()
    except (ValueError, json.JSONDecodeError) as exc:
        raise web.HTTPBadRequest(text="A JSON object is required") from exc
    if not isinstance(data, dict):
        raise web.HTTPBadRequest(text="A JSON object is required")
    if not SCRCPY_MODE:
        raise web.HTTPServiceUnavailable(text="Mouse routing requires the scrcpy backend")
    await inject_scrcpy_input(request.app["state"], data)
    return web.json_response({"ok": True})


async def ws_handler(request):'''

INJECT_OLD = '''            scrcpy_control.send(ctrl_scroll(_scrcpy_x, _scrcpy_y, w, h, d * n))
        elif t == "keydown":'''
INJECT_NEW = '''            scrcpy_control.send(ctrl_scroll(_scrcpy_x, _scrcpy_y, w, h, d * n))
        elif t == "mdx":
            # Movimiento relativo del mouse físico (mouse-route.py + getevent).
            _scrcpy_x = max(0, min(w - 1, _scrcpy_x + int(data.get("dx", 0))))
            _scrcpy_y = max(0, min(h - 1, _scrcpy_y + int(data.get("dy", 0))))
            if _scrcpy_mouse_buttons:
                scrcpy_control.send(ctrl_touch(MOTION_ACTION_MOVE, _scrcpy_x, _scrcpy_y, w, h, 1.0,
                                               POINTER_ID_MOUSE, 0, _scrcpy_mouse_buttons))
            else:
                scrcpy_control.send(ctrl_touch(ACTION_HOVER_MOVE, _scrcpy_x, _scrcpy_y, w, h, 1.0,
                                               POINTER_ID_MOUSE))
        elif t == "mbtn":
            # Botón del mouse físico: 1=izquierdo, 2=central, 3=derecho (convención
            # del cliente web; X_BTN_TO_ANDROID hace el mapeo a Android).
            ab = X_BTN_TO_ANDROID.get(int(data.get("b", 1)), BTN_PRIMARY)
            if data.get("down"):
                _scrcpy_mouse_buttons |= ab
                scrcpy_control.send(ctrl_touch(ACTION_DOWN, _scrcpy_x, _scrcpy_y, w, h, 1.0,
                                               POINTER_ID_MOUSE, ab, _scrcpy_mouse_buttons))
            else:
                _scrcpy_mouse_buttons &= ~ab
                scrcpy_control.send(ctrl_touch(ACTION_UP, _scrcpy_x, _scrcpy_y, w, h, 0.0,
                                               POINTER_ID_MOUSE, ab, _scrcpy_mouse_buttons))
        elif t == "keydown":'''

REPLACEMENTS = [
    ("ruta /api/mouse", ROUTE_OLD, ROUTE_NEW),
    ("regla de middleware local-only", MIDDLEWARE_OLD, MIDDLEWARE_NEW),
    ("handler api_mouse_handler", HANDLER_OLD, HANDLER_NEW),
    ("brazos mdx/mbtn en inject_scrcpy_input", INJECT_OLD, INJECT_NEW),
]


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "server.py"
    with open(path, encoding="utf-8") as handle:
        src = handle.read()
    if '"/api/mouse"' in src:
        print(f"ya estaba parcheado: {path}")
        print("sha256:", hashlib.sha256(src.encode()).hexdigest())
        return
    for name, old, new in REPLACEMENTS:
        count = src.count(old)
        if count != 1:
            print(f"ERROR: ancla de {name} no única (encontrada {count} veces); "
                  f"no se tocó nada. ¿server.py es la versión del repo?")
            sys.exit(1)
        src = src.replace(old, new)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(src)
    py_compile.compile(path, doraise=True)
    print(f"parche aplicado y compila OK: {path}")
    print("sha256:", hashlib.sha256(src.encode()).hexdigest())


if __name__ == "__main__":
    main()
