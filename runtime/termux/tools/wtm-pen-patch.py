#!/usr/bin/env python3
"""Aplica el puente S Pen a una Craft app clonada.

Encuentra el `raw_input_hook` real (crate `ui-egui`, no el web/test), copia
`pen_bridge.rs` al crate que lo contiene, lo declara en el crate root, añade
la dependencia Linux y llama `start()/inject()` al principio del hook.

Uso: wtm-pen-patch.py <repo> <pen_bridge.rs>
Idempotente: si el hook ya tiene `pen_bridge::inject`, no cambia nada.
"""
import pathlib
import re
import sys

if len(sys.argv) < 3:
    sys.exit("uso: wtm-pen-patch.py <repo> <pen_bridge.rs>")
root = pathlib.Path(sys.argv[1])
tpl = pathlib.Path(sys.argv[2])

skip = {"target", "tests", "test", "benches", "examples"}
hooks = []
for p in root.rglob("*.rs"):
    if skip & set(p.parts):
        continue
    try:
        txt = p.read_text(errors="ignore")
    except OSError:
        continue
    if "fn raw_input_hook" not in txt:
        continue
    if "pen_bridge::inject" in txt:
        print(f"ya parcheado: {p}")
        sys.exit(0)
    hooks.append(p)

if not hooks:
    sys.exit("no encuentro raw_input_hook en el repo")


def score(p: pathlib.Path) -> tuple:
    s = str(p)
    return (
        "/apps/" not in s,      # las apps son wrappers; el App vive en crates/
        "-web" not in s,        # el shell web no es el nativo
        "ui-egui" in s,         # el crate de UI de la suite
        "app.rs" in s,          # el App nativo
    )


hooks.sort(key=score, reverse=True)
p = hooks[0]
txt = p.read_text()
m = re.search(
    r"fn raw_input_hook\s*\(&mut self,[^)]*?([A-Za-z_]\w*)\s*:\s*&mut\s+([^)]*RawInput)\)\s*\{",
    txt,
)
if not m:
    sys.exit(f"{p}: no pude leer la firma del raw_input_hook")
param = m.group(1)
txt = txt[: m.end()] + f"\n        crate::pen_bridge::start();\n        crate::pen_bridge::inject({param});" + txt[m.end() :]
p.write_text(txt)
print(f"hook parcheado: {p} (raw = {param})")

# El crate que contiene el hook.
crate = p.parent
while crate != root and not (crate / "Cargo.toml").exists():
    crate = crate.parent
if not (crate / "Cargo.toml").exists():
    sys.exit(f"{p}: no encuentro el Cargo.toml del crate")

(crate / "src" / "pen_bridge.rs").write_text(tpl.read_text())

entry = crate / "src" / "lib.rs"
if not entry.exists():
    entry = crate / "src" / "main.rs"
if not entry.exists():
    sys.exit(f"{crate}: sin lib.rs/main.rs")
s = entry.read_text()
if "mod pen_bridge;" not in s:
    lines = s.splitlines(keepends=True)
    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("//!"):
            i += 1
        elif ln.startswith("#!["):
            while i < len(lines) and "]" not in lines[i]:
                i += 1
            i += 1
        else:
            break
    s = "".join(lines[:i]) + "mod pen_bridge;\n\n" + "".join(lines[i:])
    entry.write_text(s)
    print(f"mod pen_bridge en {entry}")

cargo = crate / "Cargo.toml"
s = cargo.read_text()
if "rust-motion-tablet" not in s:
    target = "[target.'cfg(target_os = \"linux\")'.dependencies]"
    if target in s:
        s = s.replace(target, target + "\nrust-motion-tablet = \"1.1\"", 1)
    else:
        s = s.rstrip() + f"\n\n{target}\nrust-motion-tablet = \"1.1\"\n"
    cargo.write_text(s)
    print(f"dep rust-motion-tablet en {cargo}")

print("patch OK")
