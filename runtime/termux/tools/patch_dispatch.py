#!/usr/bin/env python3
"""Inserta el dispatch de cap + pen en server.py (F4a/F5).

Uso (desde la raiz del repo, en Termux o donde sea):
    python runtime/termux/tools/patch_dispatch.py

Idempotente: si las lineas ya existen, no toca nada. Si no encuentra el
bloque hb, aborta sin escribir. Muestra las lineas insertadas al final.
"""
import re
import sys

P = "runtime/termux/server.py"

src = open(P, encoding="utf-8").read()

if 'pen_handler' in src:
    print("ya estaba insertado, nada que hacer")
    sys.exit(0)

m = re.search(
    r'([ \t]+)elif data\.get\("t"\) == "hb":\n[ \t]+await ws\.send_str\("h"\)[^\n]*\n',
    src,
)
if not m:
    print("NO se encontro el bloque hb - no se toco nada")
    sys.exit(1)

indent = m.group(1)
new = m.group(0) + (
    indent + 'elif data.get("t") == "cap":\n'
    indent + '    import cap_handler\n'
    indent + '    await cap_handler.handle(state, ws, data)\n'
    indent + 'elif data.get("t") in ("pd", "pm", "pu", "ph"):\n'
    indent + '    import pen_handler\n'
    indent + '    await pen_handler.handle(state, data)\n'
)
open(P, "w", encoding="utf-8").write(src.replace(m.group(0), new, 1))
print("OK: 5 lineas insertadas tras el bloque hb:")
print(new)
