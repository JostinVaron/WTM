#!/usr/bin/env python3
"""Negociacion de perfil de transmision cap/cap_ack (WTM, F3 — 2026-11-17).

El viewer (receptor) reporta las capacidades REALES de su hardware por
WebSocket ("cap"), y el relay (emisor, en el telefono) adapta la sesion de
scrcpy a ese perfil. Regla central: nadie declara victoria sin leer la
realidad — el ack solo dice applied:true si el cmdline del proceso scrcpy
vivo contiene exactamente lo pedido (misma tecnica usada en campo para
verificar 35 Mbps).

Diseno (plan F1-F4, validado por tests/test_stream_profile.py):
  validate_cap()    anti-abuso: un cap loco jamas llega al encoder (rangos duros)
  receiver_bitrate() techo perceptual: min(decoder, link*0.5, W*H*fps*0.1bpp)
  clamp_to_emitter() el receptor manda, pero jamas por encima del techo del
                     emisor. DECISION DEL USUARIO (2026-11-17): el techo del
                     emisor es LO QUE EL HARDWARE DECLARA (Snapdragon 8 Elite:
                     HEVC hasta 4K120), no un perfil conservador-verificado.
  ProfileStore      perfiles aprendidos por dispositivo: lo MEDIDO (verificado)
                    le gana a lo DECLARADO en sesiones posteriores (la metadata
                    del fabricante puede mentir: la Tab S6 Lite declara UHD@30
                    pero sostiene 2000x1200@60 real).
  Relay             ciclo cerrado cap -> aplicar -> verificar cmdline -> ack.
                    Si el pedido no se verifica, reintenta UNA vez con el perfil
                    conservador; si tampoco, applied:false honesto (jamas verde
                    falso). Politica single-profile: el ultimo viewer autorizado
                    manda (renegociacion completa por conexion).
  verify_cmdline()  parseo del cmdline real (/proc/PID/cmdline del scrcpy.Server).

Interfaz de launcher (la implementa F4 contra el scrcpy real):
  launch(profile)    relanza scrcpy-phone.sh con env NEW_DISPLAY/BITRATE/FPS
  read_cmdline()     devuelve el cmdline del proceso vivo, o None si murio.

Run: cd runtime/termux && python3 -m unittest tests.test_stream_profile -v
"""
import math
import re

# --- limites duros anti-abuso (un cap hostil no puede pedir locuras) ---------
CAP_LIMITS = {
    "w_min": 320, "w_max": 4096,     # 320p..4K en el lado mas largo del cap
    "fps_min": 24, "fps_max": 240,
    "bitrate_max": 200_000_000,      # 200 Mbps: ya absurdos para HEVC movil
    "bpp": 0.1,                      # bits/pixel: umbral visualmente lossless
}

# Perfil de ultimo recurso cuando el cap es invalido o el pedido no se verifica.
CONSERVATIVE_PROFILE = {
    "w": 1280, "h": 720, "fps": 30, "bitrate": 6_000_000,
}

# DECISION DEL USUARIO (2026-11-17): techo del emisor = lo que el hardware
# DECLARA. Snapdragon 8 Elite: HEVC hasta 4K120. No conservador-verificado.
EMITTER_CEILING = {
    "max_w": 3840, "max_h": 2160, "max_fps": 120, "max_bitrate": 240_000_000,
}


class CapError(ValueError):
    """cap rechazado por los limites duros: nunca llega al encoder."""


def validate_cap(msg):
    """Valida un mensaje cap. Devuelve dict normalizado o lanza CapError."""
    for key in ("w", "h", "fps"):
        if key not in msg or not isinstance(msg[key], (int, float)):
            raise CapError(f"campo {key} ausente o no numerico")
    w, h, fps = int(msg["w"]), int(msg["h"]), float(msg["fps"])
    if not (CAP_LIMITS["w_min"] <= w <= CAP_LIMITS["w_max"]):
        raise CapError(f"w={w} fuera de rango")
    if not (CAP_LIMITS["w_min"] <= h <= CAP_LIMITS["w_max"]):
        raise CapError(f"h={h} fuera de rango")
    if not (CAP_LIMITS["fps_min"] <= fps <= CAP_LIMITS["fps_max"]):
        raise CapError(f"fps={fps} fuera de rango")
    bitrate = msg.get("bitrate_max", CAP_LIMITS["bitrate_max"])
    if bitrate <= 0 or bitrate > CAP_LIMITS["bitrate_max"]:
        raise CapError(f"bitrate={bitrate} fuera de rango")
    return {
        "w": w, "h": h, "fps": fps,
        "bitrate_max": int(bitrate),
        "link_mbps": int(msg.get("link_mbps", 0)),
    }


def receiver_bitrate(w, h, fps, decoder_bitrate_max, link_mbps,
                    bpp=CAP_LIMITS["bpp"]):
    """Techo de bitrate del receptor: el minimo de sus tres limites.

    - decoder: lo que declara MediaCodecList (getBitrateRange) del decoder HW
    - link:    mitad del PHY WiFi negociado (el resto: control, retransmisiones)
    - util:    W*H*fps*bpp — umbral visualmente lossless de HEVC; mas alla de
               esto el panel no puede mostrar la diferencia (solo genera picos
               de bitrate y cuellos en el encoder).
    """
    useful = w * h * fps * bpp
    link_bitrate = link_mbps * 1_000_000 * 0.5 if link_mbps else float("inf")
    return int(min(decoder_bitrate_max, link_bitrate, useful))


def clamp_to_emitter(cap, emitter=EMITTER_CEILING):
    """El emisor obedece al receptor, pero jamas excede su techo declarado."""
    bitrate_src = cap.get("bitrate_max") or cap.get("bitrate")
    fps = min(cap["fps"], emitter["max_fps"])
    w, h = cap["w"], cap["h"]
    if w > emitter["max_w"] or h > emitter["max_h"]:
        scale = min(emitter["max_w"] / w, emitter["max_h"] / h)
        w, h = (int(w * scale) // 2) * 2, (int(h * scale) // 2) * 2  # par, exigido por encoders
    return {
        "w": w, "h": h, "fps": fps,
        "bitrate": int(min(bitrate_src, emitter["max_bitrate"])),
    }


def parse_cmdline(cmdline):
    """Extrae el perfil real del cmdline del scrcpy.Server vivo, o None."""
    m = re.search(r"new_display=(\d+)x(\d+)", cmdline)
    f = re.search(r"max_fps=(\d+)", cmdline)
    b = re.search(r"video_bit_rate=(\d+)", cmdline)
    if not (m and f and b):
        return None
    return {
        "w": int(m.group(1)), "h": int(m.group(2)),
        "fps": int(f.group(1)), "bitrate": int(b.group(1)),
    }


def verify_cmdline(asked, cmdline):
    """Nadie declara victoria sin leer la realidad: cmdline == pedido.

    El fps del cmdline es int (max_fps=N en scrcpy); el pedido puede ser float
    (59.94 NTSC) y se compara con ceil().
    """
    got = parse_cmdline(cmdline)
    if got is None:
        return False, "cmdline ilegible"
    if (got["w"], got["h"]) != (asked["w"], asked["h"]):
        return False, (f"resolucion {got['w']}x{got['h']} != pedida "
                       f"{asked['w']}x{asked['h']}")
    if got["fps"] != math.ceil(asked["fps"]):
        return False, f"fps {got['fps']} != pedido {math.ceil(asked['fps'])}"
    if got["bitrate"] != asked["bitrate"]:
        return False, f"bitrate {got['bitrate']} != pedido {asked['bitrate']}"
    return True, "verificado"


def same_profile(a, b):
    """Igualdad de perfil para decidir si hay que relanzar (evita bucles)."""
    return ((a["w"], a["h"], math.ceil(a["fps"]), a["bitrate"]) ==
            (b["w"], b["h"], math.ceil(b["fps"]), b["bitrate"]))


class ProfileStore:
    """Perfiles aprendidos por dispositivo: lo MEDIDO gana a lo DECLARADO."""

    def __init__(self):
        self._learned = {}

    def record_verified(self, device, profile):
        """Registra el perfil que se VERIFICO sostenido para este dispositivo."""
        self._learned[device] = dict(profile)

    def get(self, device):
        return self._learned.get(device)


class Relay:
    """Handler cap del relay: aplicar -> verificar -> ack. Jamas verde falso.

    launcher: objeto con launch(profile) y read_cmdline() (F4 lo implementa
    contra el scrcpy real; los tests contra un simulador).
    """

    def __init__(self, launcher, store=None):
        self.launcher = launcher
        self.store = store if store is not None else ProfileStore()
        self.applied = None

    def handle_cap(self, msg, device="anon"):
        """Procesa un mensaje cap. Devuelve el cap_ack a mandar al viewer."""
        try:
            cap = validate_cap(msg)
        except CapError as err:
            ok = self._apply(dict(CONSERVATIVE_PROFILE))
            return {"t": "cap_ack", "applied": ok, "profile": self.applied,
                    "reason": f"cap_rechazado: {err}"}

        learned = self.store.get(device)
        profile = clamp_to_emitter(learned if learned else cap)

        if self.applied and same_profile(self.applied, profile):
            return {"t": "cap_ack", "applied": True, "profile": self.applied,
                    "reason": "ya activo (sin relanzar)"}

        if self._apply(profile):
            return {"t": "cap_ack", "applied": True, "profile": self.applied,
                    "reason": "verificado via cmdline"}

        if self._apply(dict(CONSERVATIVE_PROFILE)):
            return {"t": "cap_ack", "applied": True, "profile": self.applied,
                    "reason": ("pedido no verificado; conservador aplicado "
                               "y verificado")}

        return {"t": "cap_ack", "applied": False, "profile": None,
                "reason": "fallo de verificacion incluso en conservador"}

    def _apply(self, profile):
        """Lanza con el perfil y verifica el cmdline. Solo la realidad manda."""
        self.launcher.launch(profile)
        cmdline = self.launcher.read_cmdline()
        if cmdline is None:
            return False
        ok, _ = verify_cmdline(profile, cmdline)
        if ok:
            self.applied = dict(parse_cmdline(cmdline))  # ack = lo REAL corriendo
            return True
        return False
