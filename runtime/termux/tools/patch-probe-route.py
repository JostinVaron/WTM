#!/usr/bin/env python3
"""
patch-probe-route.py — parche IDEMPOTENTE que añade al server la ruta pública
/probe.html, para servir la página de probe de codecs (docs/hevc-study.md §4)
desde el propio bridge HTTPS (WebCodecs exige contexto seguro; un servidor
aparte en http:// invalidaría el probe).

Parchea server.py en tres anclas exactas (falla sin escribir nada si alguna
no se encuentra o no es única):
  1. Handler probe_handler junto a index_handler (FileResponse de web/probe.html).
  2. Ruta   : GET /probe.html
  3. Acceso : "/probe.html" en public_paths (página estática, no toca datos
     del server ni exige cookie; el probe corre en el navegador).

Igual que tools/patch-mouse-endpoint.py: TEMPORAL hasta confirmar el server
completo en el repo. Uso:
    python3 tools/patch-probe-route.py <server.py> [web/probe.html]
Si web/probe.html no existe, AVISA (cópialo desde runtime/termux/web/).
"""
import hashlib
import os
import py_compile
import sys

HANDLER_OLD = '''async def index_handler(request):'''
HANDLER_NEW = '''async def probe_handler(request):
    """Probe de codecs (docs/hevc-study.md sec.4): página estática servida por
    el bridge para que WebCodecs tenga contexto seguro HTTPS. No consume
    recursos del server; todo corre en el navegador de la tablet."""
    return web.FileResponse(os.path.join(HERE, "web", "probe.html"),
                            headers={"Cache-Control": "no-store"})


async def index_handler(request):'''

ROUTE_OLD = '''    app.router.add_get("/healthz", health_handler)'''
ROUTE_NEW = '''    app.router.add_get("/healthz", health_handler)
    app.router.add_get("/probe.html", probe_handler)'''

PUBLIC_OLD = '''public_paths = {"/login", "/manifest.json", "/icon.svg"}'''
PUBLIC_NEW = '''public_paths = {"/login", "/manifest.json", "/icon.svg", "/probe.html"}'''

REPLACEMENTS = [
    ("handler probe_handler", HANDLER_OLD, HANDLER_NEW),
    ("ruta /probe.html", ROUTE_OLD, ROUTE_NEW),
    ("public_paths", PUBLIC_OLD, PUBLIC_NEW),
]


def patch_file(path, replacements, already_marker):
    with open(path, encoding="utf-8") as handle:
        src = handle.read()
    if already_marker in src:
        print(f"ya estaba parcheado: {path}")
        return False
    for name, old, new in replacements:
        count = src.count(old)
        if count != 1:
            print(f"ERROR: ancla de {name} no única (encontrada {count} veces) en {path}; "
                  "no se tocó nada.")
            sys.exit(1)
        src = src.replace(old, new)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(src)
    print(f"parche aplicado: {path}")
    return True


def main():
    server_path = sys.argv[1] if len(sys.argv) > 1 else "server.py"
    base_dir = os.path.dirname(os.path.abspath(server_path))
    probe_path = sys.argv[2] if len(sys.argv) > 2 else os.path.join(base_dir, "web", "probe.html")

    patch_file(server_path, REPLACEMENTS, '"/probe.html"')
    py_compile.compile(server_path, doraise=True)
    print("server.py compila OK")

    if not os.path.isfile(probe_path):
        print(f"AVISO: falta {probe_path}; cópialo desde runtime/termux/web/probe.html")

    with open(server_path, encoding="utf-8") as handle:
        digest = hashlib.sha256(handle.read().encode()).hexdigest()
    print(f"sha256 {server_path}: {digest}")


if __name__ == "__main__":
    main()
