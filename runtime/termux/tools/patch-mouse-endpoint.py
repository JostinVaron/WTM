#!/usr/bin/env python3
"""
patch-mouse-endpoint.py — parche IDEMPOTENTE que añade al runtime el soporte
completo del enrutado del mouse físico del teléfono (feature restaurada del
viejo "DeX Tahoe"). Parchea DOS archivos, con anclas exactas (falla si no las
encuentra o si no son únicas; nunca rompe un archivo a medias):

  server.py:
    1. Ruta    : POST /api/mouse
    2. Seguridad: solo peers locales (loopback o la IP vinculada), sin cookie —
       es el proceso local mouse-route.py quien lo usa, nunca un cliente remoto.
    3. Handler : api_mouse_handler con rate-limit (240 eventos/s).
    4. Inyección: tipos "mdx" (movimiento relativo) y "mbtn" (botones) en
       inject_scrcpy_input, reutilizando el puntero mouse existente de scrcpy.
    5. Cursor  : _broadcast_cursor difunde la posición del cursor inyectado a
       los clientes WebSocket. Android NO dibuja puntero para eventos
       inyectados en un display virtual; el cursor lo dibuja el navegador
       (exactamente como hace el cliente de scrcpy en escritorio).

  web/index.html:
    6. SVG del cursor + moveCursor(): dibuja la flecha en la posición recibida
       ("cursor" por WebSocket), mapeando coordenadas de video a pantalla con
       la misma geometría que usa el input táctil. Cursor estilo Samsung:
       negro con borde blanco (no la flecha blanca estilo Windows).

TEMPORAL: una vez validado en hardware, ambos archivos se confirman completos
en el repo y este parche se elimina. Uso:
    python3 tools/patch-mouse-endpoint.py <server.py> [web/index.html]
"""
import hashlib
import os
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


async def _broadcast_cursor(state, x, y, down):
    """Difunde la posición del cursor inyectado a los clientes: Android no
    dibuja puntero para eventos inyectados en un display virtual, así que el
    navegador dibuja el cursor con estos datos (como el cliente de scrcpy en
    escritorio)."""
    msg = json.dumps({"type": "cursor", "x": int(x), "y": int(y), "down": bool(down)})
    for ws in list(state.clients):
        try:
            await ws.send_str(msg)
        except Exception:
            state.clients.discard(ws)


async def api_mouse_handler(request):
    """Puntero físico del propio teléfono: el mouse Bluetooth se captura con
    EVIOCGRAB (mouse-grab, modo exclusivo) o getevent (mouse-route.py) y se
    reenvía aquí como deltas/botones; se inyectan al display virtual vía el
    socket de control de scrcpy."""
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
            # Movimiento relativo del mouse físico (mouse-route.py).
            _scrcpy_x = max(0, min(w - 1, _scrcpy_x + int(data.get("dx", 0))))
            _scrcpy_y = max(0, min(h - 1, _scrcpy_y + int(data.get("dy", 0))))
            if _scrcpy_mouse_buttons:
                scrcpy_control.send(ctrl_touch(MOTION_ACTION_MOVE, _scrcpy_x, _scrcpy_y, w, h, 1.0,
                                               POINTER_ID_MOUSE, 0, _scrcpy_mouse_buttons))
            else:
                scrcpy_control.send(ctrl_touch(ACTION_HOVER_MOVE, _scrcpy_x, _scrcpy_y, w, h, 1.0,
                                               POINTER_ID_MOUSE))
            await _broadcast_cursor(state, _scrcpy_x, _scrcpy_y, bool(_scrcpy_mouse_buttons))
        elif t == "mbtn":
            # Botón del mouse físico: 1=izquierdo, 2=central, 3=derecho (convención
            # del cliente web; X_BTN_TO_ANDROID hace el mapeo a Android).
            ab = X_BTN_TO_ANDROID.get(int(data.get("b", 1)), BTN_PRIMARY)
            if data.get("down"):
                _scrcpy_mouse_buttons |= ab
                scrcpy_control.send(ctrl_touch(ACTION_DOWN, _scrcpy_x, _scrcpy_y, w, h, 1.0,
                                               POINTER_ID_MOUSE, ab, _scrcpy_mouse_buttons))
                await _broadcast_cursor(state, _scrcpy_x, _scrcpy_y, True)
            else:
                _scrcpy_mouse_buttons &= ~ab
                scrcpy_control.send(ctrl_touch(ACTION_UP, _scrcpy_x, _scrcpy_y, w, h, 0.0,
                                               POINTER_ID_MOUSE, ab, _scrcpy_mouse_buttons))
                await _broadcast_cursor(state, _scrcpy_x, _scrcpy_y, bool(_scrcpy_mouse_buttons))
        elif t == "keydown":'''

SERVER_REPLACEMENTS = [
    ("ruta /api/mouse", ROUTE_OLD, ROUTE_NEW),
    ("regla de middleware local-only", MIDDLEWARE_OLD, MIDDLEWARE_NEW),
    ("handler api_mouse_handler", HANDLER_OLD, HANDLER_NEW),
    ("brazos mdx/mbtn en inject_scrcpy_input", INJECT_OLD, INJECT_NEW),
]

# --- web/index.html: dibujar el cursor en la tablet --------------------------
IDX_CSS_OLD = '''           background: rgba(0,0,0,.85); color: #fff; font: 22px sans-serif; }
</style>'''
IDX_CSS_NEW = '''           background: rgba(0,0,0,.85); color: #fff; font: 22px sans-serif; }
  #cursor { position: fixed; left: 0; top: 0; width: 26px; height: 26px; z-index: 9;
            pointer-events: none; display: none; filter: drop-shadow(0 1px 2px rgba(0,0,0,.85)); }
</style>'''

IDX_BODY_OLD = '''<canvas id="screen"></canvas>'''
IDX_BODY_NEW = '''<canvas id="screen"></canvas>
<svg id="cursor" viewBox="0 0 12 19"><path d="M1 1 L1 16 L4.8 12.4 L7.4 18.3 L9.3 17.4 L6.7 11.6 L11.5 11.5 Z" fill="#111" stroke="#fff" stroke-width="1" stroke-linejoin="round"/></svg>'''

IDX_MSG_OLD = '''      if (m.type === "config") configure(m);
      else if (m.type === "sync") {'''
IDX_MSG_NEW = '''      if (m.type === "config") configure(m);
      else if (m.type === "cursor") moveCursor(m.x, m.y, m.down);
      else if (m.type === "sync") {'''

IDX_FN_OLD = '''function sync() { send({ t: "sync", c: Date.now() }); }'''
IDX_FN_NEW = '''function sync() { send({ t: "sync", c: Date.now() }); }

/* Cursor del mouse físico enrutado: lo dibuja el navegador porque Android no
   dibuja punteros para eventos inyectados en un display virtual. */
const cursorEl = document.getElementById("cursor");
function moveCursor(x, y, down) {
  const r = canvas.getBoundingClientRect(), s = Math.min(r.width / videoW, r.height / videoH);
  const ox = (r.width - videoW * s) / 2, oy = (r.height - videoH * s) / 2;
  cursorEl.style.left = (r.left + ox + x * s) + "px";
  cursorEl.style.top = (r.top + oy + y * s) + "px";
  cursorEl.style.display = "block";
  cursorEl.style.opacity = down ? "0.7" : "1";
}'''

INDEX_REPLACEMENTS = [
    ("css #cursor", IDX_CSS_OLD, IDX_CSS_NEW),
    ("svg del cursor", IDX_BODY_OLD, IDX_BODY_NEW),
    ("mensaje ws cursor", IDX_MSG_OLD, IDX_MSG_NEW),
    ("función moveCursor", IDX_FN_OLD, IDX_FN_NEW),
]


def patch_file(path, replacements, already_marker, label):
    """Aplica replacements sobre path. Idempotente: si already_marker ya está
    presente, no toca nada. Aborta (sin escribir) ante cualquier ancla que no
    sea única."""
    with open(path, encoding="utf-8") as handle:
        src = handle.read()
    if already_marker in src:
        print(f"ya estaba parcheado: {path}")
        return False
    for name, old, new in replacements:
        count = src.count(old)
        if count != 1:
            print(f"ERROR: ancla de {name} no única (encontrada {count} veces) en {path}; "
                  f"no se tocó nada. ¿{label} es la versión del repo?")
            sys.exit(1)
        src = src.replace(old, new)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(src)
    print(f"parche aplicado: {path}")
    return True


def main():
    server_path = sys.argv[1] if len(sys.argv) > 1 else "server.py"
    index_path = sys.argv[2] if len(sys.argv) > 2 else os.path.join(
        os.path.dirname(os.path.abspath(server_path)), "web", "index.html")

    changed = patch_file(server_path, SERVER_REPLACEMENTS, '"/api/mouse"', "server.py")
    if changed:
        py_compile.compile(server_path, doraise=True)
        print("server.py compila OK")
    else:
        py_compile.compile(server_path, doraise=True)
        print("server.py compila OK")

    if os.path.isfile(index_path):
        patch_file(index_path, INDEX_REPLACEMENTS, 'id="cursor"', "web/index.html")
    else:
        print(f"AVISO: no se encontró {index_path}; el cliente no dibujará el cursor")

    for path in (server_path, index_path):
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as handle:
                digest = hashlib.sha256(handle.read().encode()).hexdigest()
            print(f"sha256 {path}: {digest}")


if __name__ == "__main__":
    main()
