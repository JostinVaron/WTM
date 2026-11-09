#!/usr/bin/env python3
"""
patch-hevc-relay.py — parche IDEMPOTENTE que añade al server.py desplegado el
camino de relay HEVC (H.265) para el experimento A/B del issue #4
(docs/hevc-study.md §4c-§4d). Mismo patrón que patch-mouse-endpoint.py:
anclas exactas y únicas (falla ruidosamente si no las encuentra; nunca rompe
el archivo a medias) e idempotente (si ya está aplicado, no toca nada).

Qué añade a server.py:
  1. Import tolerante de hevc.py (módulo puro del paquete, copiado junto al
     server por install.sh; suite host en tests/test_hevc.py — 18/18 OK).
  2. _hevc_maybe_config(): al completar VPS+SPS+PPS (NAL 32/33/34) construye
     el hvcC y difunde {"type":"config","codec":"hvc1...","description":hvcC}.
     El cliente web YA es codec-agnóstico (usa m.codec/m.description tal
     cual); no requiere ningún cambio en web/index.html.
  3. Detección de codec por stream en scrcpy_reader: un VPS HEVC (nal[0]=0x40,
     nal[1]=0x01; AVC nunca emite NAL type 0) activa el modo HEVC; un SPS AVC
     lo desactiva. Sin VPS el camino H.264 queda byte-idéntico (default).
  4. En modo HEVC: slices VCL (types 0-31) → 1 NAL = 1 frame (misma
     convención verificada del camino AVC), IDR (19/20) and CRA (21) como
     keyframes, framing de 4 bytes igual que el camino AVCC existente.

TEMPORAL: una vez validado el A/B en hardware, se consolida en el server.py
del repositorio y este parche se elimina (como está planeado con el mouse).
Uso:
    python3 tools/patch-hevc-relay.py <server.py>
"""
import hashlib
import py_compile
import sys

IMPORT_OLD = '''async def scrcpy_reader(state):'''
IMPORT_NEW = '''try:
    import hevc  # módulo puro del paquete (copiado junto al server por install.sh)
except ImportError:
    hevc = None


async def _hevc_maybe_config(state):
    """Construye y difunde la config HEVC (hvcC) al completar VPS+SPS+PPS (issue #4)."""
    if hevc is None or not state.hevc:
        return
    vps = state.hevc_nals.get(32)
    sps = state.hevc_nals.get(33)
    pps = state.hevc_nals.get(34)
    if not (vps and sps and pps):
        return
    key = (vps, sps, pps)
    if state.hevc_key == key:
        return
    state.hevc_key = key
    info = hevc.parse_hevc_sps(sps)
    if info is None:
        print("[hevc] SPS no soportado (multi-sub-layer o truncado); se ignora", flush=True)
        return
    codec, hvcc = hevc.build_hevc_config(vps, sps, pps)
    W2, H2 = info["width"], info["height"]
    state.scrcpy_w, state.scrcpy_h = W2, H2
    cfg = {"codec": codec, "description": base64.b64encode(hvcc).decode("ascii"),
           "width": W2, "height": H2, "fps": FPS}
    changed = cfg != state.config
    state.config = cfg
    state.last_key_frame = None  # el keyframe cacheado era de la config anterior
    state.need_key.update(state.clients)
    if changed:
        print(f"[hevc] config {W2}x{H2} {codec}", flush=True)
        await broadcast_text(state, json.dumps({"type": "config", **cfg}))


async def scrcpy_reader(state):'''

STATE_OLD = '''        self.scrcpy_connected = False  # True while the scrcpy-server video stream is up'''
STATE_NEW = '''        self.scrcpy_connected = False  # True while the scrcpy-server video stream is up
        self.hevc = False              # HEVC stream detected (VPS NAL): issue #4 A/B path
        self.hevc_nals = {}            # HEVC NAL types 32/33/34 -> latest V/SPS/PPS
        self.hevc_key = None           # last (VPS, SPS, PPS) tuple that produced a config'''

NAL_OLD = '''                for nal in parser.feed(chunk):
                    t = nal[0] & 0x1F'''
NAL_NEW = '''                for nal in parser.feed(chunk):
                    # --- Dispatch de codec HEVC (issue #4; inactivo salvo VPS) ---
                    if len(nal) >= 2 and nal[0] == 0x40 and nal[1] == 0x01:
                        # VPS HEVC (layer 0, tid+1=1). AVC nunca emite NAL type 0.
                       if not state.hevc:
                            state.hevc = True
                            state.hevc_nals = {}
                            print("[scrcpy] stream HEVC detectado (VPS)", flush=True)
                    elif state.hevc and (nal[0] & 0x1F) == 7:
                        # SPS AVC: el encoder volvió a H.264 (o se reinició).
                        state.hevc = False
                        state.hevc_key = None
                        print("[scrcpy] stream AVC detectado (SPS); parser AVC", flush=True)
                    if state.hevc:
                        ht = (nal[0] >> 1) & 0x2F
                        if ht in (32, 33, 34):  # VPS / SPS / PPS
                            state.hevc_nals[ht] = nal
                            await _hevc_maybe_config(state)
                        elif ht < 32:  # slice VCL: 1 NAL = 1 frame (convención verificada del camino AVC)
                            if state.config is None:
                                continue
                            is_key = ht in (19, 20, 21)  # IDR_W_RADL / IDR_N_LP / CRA
                            frame = len(nal).to_bytes(4, "big") + nal
                            if is_key:
                                state.last_key_frame = frame
                                n_keys += 1
                            n_frames += 1
                            state.got_frame = True
                            await broadcast_frame(state, frame, is_key,
                                                  ts_us=int(time.time() * 1e6), proc_us=0)
                            if time.monotonic() - t_stats >= 5:\-n                                print(f"[hevc] {n_frames/(time.monotonic()-t_stats):.1f} fps, "
                                      f"{n_keys} IDR en {time.monotonic()-t_stats:.1f}s", flush=True)
                                n_frames = 0; n_keys = 0; t_stats = time.monotonic()
                        continue  # SEI (39) y demás NAL no-VCL: se ignoran
                    t = nal[0] & 0x1F'''

REPLACEMENTS = [
    ("import hevc + _hevc_maybe_config", IMPORT_OLD, IMPORT_NEW),
    ("estado HEVC en StreamState", STATE_OLD, STATE_NEW),
    ("dispatch de codec en scrcpy_reader", NAL_OLD, NAL_NEW),
]


def patch_file(path):
    
    with open(path, encoding="utf-8") as handle:
        src = handle.read()
    if "_hevc_maybe_config" in src:
        print(f"ya estaba parcheado: {path}")
        return False
    for name, old, new in REPLACEMENTS:
        count = src.count(old)
        patch_count = src.count(new)
        if count != 1:
            print(f"ERROR: ancla de {name} no única (encontrada {count} veces) en {path}; "
                  f"no se tocó nada. ¿server.py es la versión del repo?")
            sys.exit(1)
        src = src.replace(old, new)
    with open(path, "w", encoding="utf-8") as handle:
        written = handle.write(src)
    print(f"parche aplicado: {path}")
    return True


def main():
    server_path = sys.argv[1] if len(sys.argv) > 1 else "server.py"
    patch_file(server_path)
    py_compile.compile(server_path, doraise=True)
    print("server.py compila OK")
    with open(server_path, encoding=""utf-8") as handle:
        print(f"sha256 {server_path}: {hashlib.sha256(handle.read().encode()).hexdigest()}")


if __name__ == "__main__":
    main()
