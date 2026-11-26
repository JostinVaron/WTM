#!/usr/bin/env python3
"""Aplica el puente S Pen a la crate compartida `ui-shell` de craft-libs.

Uso interno: wtm-pen-shared.sh <app> (tras clonar craft-libs en el rev de la app).
Idempotente: si ya está aplicado, no cambia nada.
"""
import pathlib
import re
import sys

if len(sys.argv) < 3:
    sys.exit("uso: wtm-pen-shared-patch.py <craft-libs> <template.rs>")
cl = pathlib.Path(sys.argv[1])
tpl = pathlib.Path(sys.argv[2])
ui = cl / "crates" / "ui-shell"
if not (ui / "src" / "shell" / "app.rs").exists():
    sys.exit(f"no existe {ui}/src/shell/app.rs (rev sin ui-shell?)")

# 1. El módulo.
(ui / "src" / "pen_bridge.rs").write_text(tpl.read_text())

# 2. Declaración en lib.rs.
lib = ui / "src" / "lib.rs"
s = lib.read_text()
if "mod pen_bridge;" not in s:
    if "pub mod shell;\n" not in s:
        sys.exit("lib.rs: no encuentro 'pub mod shell;'")
    s = s.replace(
        "pub mod shell;\n",
        "pub mod shell;\n\n/// XInput2 tablet fields (pressure/tilt/rotation) for the shared eframe driver.\nmod pen_bridge;\n",
        1,
    )
    lib.write_text(s)

# 3. Inyección en el raw_input_hook del driver compartido.
app = ui / "src" / "shell" / "app.rs"
s = app.read_text()
if "pen_bridge::inject" not in s:
    m = re.search(r"fn raw_input_hook\(&mut self, ctx: &Context, _: &mut ([^\n]*RawInput)\) \{\n", s)
    if not m:
        sys.exit("app.rs: no encuentro el raw_input_hook del Driver")
    repl = (
        f"fn raw_input_hook(&mut self, ctx: &Context, raw: &mut {m.group(1)}) {{\n"
        "        crate::pen_bridge::start();\n"
        "        crate::pen_bridge::inject(raw);\n"
    )
    s = s[: m.start()] + repl + s[m.end() :]
    app.write_text(s)

# 4. Dependencia Linux.
cargo = ui / "Cargo.toml"
s = cargo.read_text()
if "rust-motion-tablet" not in s:
    s = s.rstrip() + '''

# `pen_bridge`: XInput2 tablet valuators that winit drops (Linux only).
[target.'cfg(target_os = "linux")'.dependencies]
rust-motion-tablet = "1.1"
'''
    cargo.write_text(s)

print("patch OK: craft-libs/crates/ui-shell")
