#!/usr/bin/env python3
"""Aplica el puente S Pen (pen_bridge) a una Craft app ya clonada.

Uso interno: wtm-pen-app.sh <app> (desde el proot, con el cwd en el repo).
Edita apps/<app>/Cargo.toml (dep linux rust-motion-tablet) y
apps/<app>/src/main.rs (mod + start + inject en raw_input_hook).
"""
import pathlib
import re
import sys

app = sys.argv[1] if len(sys.argv) > 1 else ""
if not app:
    sys.exit("uso: wtm-pen-patch.py <app>")

main = pathlib.Path(f"apps/{app}/src/main.rs")
cargo = pathlib.Path(f"apps/{app}/Cargo.toml")
if not main.exists():
    sys.exit(f"no existe {main}")
if not cargo.exists():
    sys.exit(f"no existe {cargo}")

c = cargo.read_text()
if "rust-motion-tablet" not in c:
    target = "[target.'cfg(target_os = \"linux\")'.dependencies]"
    if target in c:
        c = c.replace(target, target + "\nrust-motion-tablet = \"1.1\"", 1)
    else:
        c = c.rstrip() + "\n\n" + target + "\nrust-motion-tablet = \"1.1\"\n"
    cargo.write_text(c)

m = main.read_text()
if "mod pen_bridge;" not in m:
    at = re.search(r"^fn main\b", m, re.M)
    if not at:
        sys.exit("main.rs: no encuentro fn main")
    m = m[: at.start()] + "mod pen_bridge;\n\n" + m[at.start() :]

if "pen_bridge::start()" not in m:
    at = re.search(r"^(fn main\([^)]*\)[^{]*\{)", m, re.M)
    if not at:
        sys.exit("main.rs: no encuentro el cuerpo de main")
    m = m[: at.end()] + "\n    pen_bridge::start();" + m[at.end() :]

if "pen_bridge::inject" not in m:
    at = re.search(r"fn raw_input_hook\s*\([^)]*\)\s*\{", m)
    if at:
        m = m[: at.end()] + "\n        pen_bridge::inject(raw);" + m[at.end() :]
    else:
        at = re.search(r"(\n\s*self\.0\.raw_input_hook\(ctx, raw\)\;)", m)
        if at:
            m = m[: at.end()] + "\n        pen_bridge::inject(raw);" + m[at.end() :]
        else:
            # eframe's App trait: add the hook to the app's impl (it is a
            # default trait method, so apps without it never received our
            # events). Insert it before the matching closing brace.
            imp = re.search(r"impl\s+eframe::App\s+for\s+[A-Za-z0-9_]+\s*\{", m)
            if not imp:
                sys.exit("main.rs: sin raw_input_hook, delegacion ni impl eframe::App")
            depth = 1
            j = imp.end()
            while j < len(m) and depth:
                if m[j] == "{":
                    depth += 1
                elif m[j] == "}":
                    depth -= 1
                j += 1
            if depth:
                sys.exit("main.rs: impl eframe::App sin cierre")
            hook = (
                "\n    fn raw_input_hook(&mut self, _ctx: &egui::Context, "
                "raw: &mut egui::RawInput) {\n        pen_bridge::inject(raw);\n    }\n"
            )
            m = m[: j - 1] + hook + m[j - 1 :]

main.write_text(m)
print(f"patch OK: {main}")
