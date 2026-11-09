#!/usr/bin/env python3
"""
patch-bridge-timestamps.py — parche IDEMPOTENTE que añade marcas de tiempo a
las líneas de log del bridge (server.py).

Motivo (hallazgo de campo 2026-11-09): rd-bridge.log mezclaba las líneas de
carreras viejas y nuevas sin ninguna marca de tiempo, por lo que después de
un crash + reinicio era imposible distinguir el traceback viejo del actual
sin cruzar rd-start.log. Los atajos (start/stop-dex.sh) ya timestampean;
el bridge no.

Qué hace:
  1. Inserta una función ``log()`` con marca de tiempo (formato
     ``[%Y-%m-%d %H:%M:%S]``) junto a los helpers existentes.
  2. Redirige a ``log()`` los ``print()`` etiquetados que emite el bridge:
     ``[server]``, ``[hevc]`` y ``[client]``. El texto de la etiqueta se
     conserva dentro del mensaje (``[server] ...``).

Es idempotente (si ya está parcheado, no toca nada), usa anclas exactas
(aborta sin escribir ante cualquier ancla no única) y verifica con
py_compile antes de reportar éxito.

Uso:
    python3 tools/patch-bridge-timestamps.py <server.py>
"""
import os
import py_compile
import sys

HELPER_ANCHOR = '''def load_auth_tokens(path):'''
HELPER_NEW = '''def log(message):
    """Línea de log con marca de tiempo: todas las etiquetas del bridge
    ([server]/[hevc]/[client]) pasan por aquí para que rd-bridge.log sea
    cronológicamente legible tras reinicios."""
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}", flush=True)


def load_auth_tokens(path):'''

# Cada entrada: (etiqueta, línea print original exacta, reemplazo con log()).
# Las anclas son literales del server.py v0.3.0; si alguna no es única o
# falta, el parche aborta sin escribir nada.
PRINT_REPLACEMENTS = [
    ("stats hevc",
     '''                                print(f"[hevc] {n_frames/(time.monotonic()-t_stats):.1f} fps, "
                                      f"{n_keys} IDR en {time.monotonic()-t_stats:.1f}s", flush=True)''',
     '''                                log(f"[hevc] {n_frames/(time.monotonic()-t_stats):.1f} fps, "
                                    f"{n_keys} IDR en {time.monotonic()-t_stats:.1f}s")'''),
    ("hevc config",
     '''        print(f"[hevc] config {W2}x{H2} {codec}", flush=True)''',
     '''        log(f"[hevc] config {W2}x{H2} {codec}")'''),
    ("hevc sps no soportado",
     '''        print("[hevc] SPS no soportado (multi-sub-layer o truncado); se ignora", flush=True)''',
     '''        log("[hevc] SPS no soportado (multi-sub-layer o truncado); se ignora")'''),
    ("client log",
     '''    print("[client] " + text, flush=True)''',
     '''    log("[client] " + text)'''),
    ("server cannot raise priority",
     '''        print(f"[server] cannot raise priority: {e}", flush=True)''',
     '''        log(f"[server] cannot raise priority: {e}")'''),
    ("server priority",
     '''        print(f"[server] priority {prio} applied to server and Xvfb {DISPLAY} ({len(pids)} pid)", flush=True)''',
     '''        log(f"[server] priority {prio} applied to server and Xvfb {DISPLAY} ({len(pids)} pid)")'''),
    ("server banner",
     '''    print(
        f"[server] {DISPLAY} {W}x{H}@{FPS} crf={CRF} preset={PRESET} "
        f"on https://{bind_host}:{PORT}",
        flush=True,
    )''',
     '''    log(f"[server] {DISPLAY} {W}x{H}@{FPS} crf={CRF} preset={PRESET} "
        f"on https://{bind_host}:{PORT}")'''),
    ("server backend",
     '''    print(f"[server] backend={backend} (hw -> x264 fallback)" + ("; control=scrcpy" if backend == "scrcpy" else ""), flush=True)''',
     '''    log(f"[server] backend={backend} (hw -> x264 fallback)" + ("; control=scrcpy" if backend == "scrcpy" else ""))'''),
    ("server cleanup",
     '''            print(f"[server] cleanup: {task_key} ended with {exc!r}", flush=True)''',
     '''            log(f"[server] cleanup: {task_key} ended with {exc!r}")'''),
]


def patch(path):
    with open(path, encoding="utf-8") as handle:
        src = handle.read()

    if "def log(message):" in src:
        print(f"ya estaba parcheado: {path}")
        return False

    # 1. insertar el helper junto a load_auth_tokens
    count = src.count(HELPER_ANCHOR)
    if count != 1:
        print(f"ERROR: ancla del helper no única (encontrada {count} veces) en {path}; no se tocó nada.")
        sys.exit(1)
    src = src.replace(HELPER_ANCHOR, HELPER_NEW)

    # 2. redirigir los prints etiquetados
    for name, old, new in PRINT_REPLACEMENTS:
        count = src.count(old)
        if count != 1:
            print(f"ERROR: ancla de {name} no única (encontrada {count} veces) en {path}; no se tocó nada.")
            sys.exit(1)
        src = src.replace(old, new)

    # 3. escribir y validar
    tmp = path + ".wtm-tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(src)
    try:
        py_compile.compile(tmp, doraise=True)
    except Exception as exc:
        os.unlink(tmp)
        print(f"ERROR: el parche no compila ({exc}); no se tocó nada.")
        sys.exit(1)
    os.rename(tmp, path)
    print(f"parche aplicado: {path}")
    return True


def main():
    if len(sys.argv) < 2:
        print("uso: python3 tools/patch-bridge-timestamps.py <server.py>")
        sys.exit(2)
    patch(sys.argv[1])


if __name__ == "__main__":
    main()
