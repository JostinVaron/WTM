#!/usr/bin/env python3
"""Handler del mensaje {"t":"cap"} para server.py (F4a, 2026-11-17).

server.py despacha aqui con 3 lineas en su dispatch WS:
    elif data.get("t") == "cap":
        import cap_handler
        await cap_handler.handle(state, ws, data)

Todo lo demas vive en este modulo para que el diff sobre server.py sea
minimo y auditable. La negociacion es la ya validada (23/23 en Termux):
validate_cap (anti-abuso) -> clamp al techo declarado del emisor ->
aplicar via ScrcpyLauncher -> VERIFICAR cmdline -> cap_ack honesto.

Politicas implementadas (todas del plan F2 aprobado):
  - single-profile last-wins: el ultimo viewer autorizado manda
  - cooldown anti-thrash: dos viewers alternando perfiles no pueden
    relanzar el encoder en bucle (8 s entre relanzamientos)
  - jamas verde falso: applied solo si el cmdline del scrcpy vivo
    contiene exactamente lo pedido
  - perfiles aprendidos persistentes en ~/.config/wtm/profiles.json:
    SOLO los escribe la telemetria (F4d) con lo MEDIDO — un apply no
    persiste nada, porque "aplicado" no es "sostenido" (matriz T3: lo
    medido gana a lo declarado, no al reves).
"""
import asyncio
import json
import os
import time

import stream_profile
from scrcpy_launcher import ScrcpyLauncher

PROFILES_FILE = os.path.join(os.environ.get("HOME", ""),
                             ".config", "wtm", "profiles.json")

APPLY_COOLDOWN_S = 8.0


class _PersistentStore(stream_profile.ProfileStore):
    """ProfileStore que sobrevive reinicios del relay.

    record_verified() SOLO lo llama la telemetria (F4d) cuando un perfil se
    MEDIO sostenido — nunca el propio apply. Asi la metadata que miente no
    puede auto-confirmarse (caso T3 de la matriz validada).
    """

    def __init__(self, path):
        super().__init__()
        self.path = path
        try:
            with open(path, encoding="utf-8") as handle:
                raw = json.load(handle)
            if isinstance(raw, dict):
                for device, profile in raw.items():
                    if (isinstance(profile, dict)
                            and all(k in profile for k in
                                    ("w", "h", "fps", "bitrate"))):
                        self._learned[str(device)] = {
                            "w": int(profile["w"]), "h": int(profile["h"]),
                            "fps": profile["fps"],
                            "bitrate": int(profile["bitrate"]),
                        }
        except (OSError, ValueError):
            pass  # sin archivo o corrupto: se empieza sin aprendido

    def record_verified(self, device, profile):
        super().record_verified(device, profile)
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as handle:
                json.dump(self._learned, handle)
        except OSError as exc:
            print(f"[cap] no se pudo persistir el perfil: {exc}", flush=True)


_relay = None
_last_launch_monotonic = 0.0


def _get_relay():
    global _relay
    if _relay is None:
        _relay = stream_profile.Relay(ScrcpyLauncher(),
                                      _PersistentStore(PROFILES_FILE))
    return _relay


def _handle_sync(msg, device):
    """Ejecuta la negociacion (bloqueante: relanza procesos)."""
    global _last_launch_monotonic
    relay = _get_relay()
    launches_before = relay.launcher.launches
    ack = relay.handle_cap(msg, device)
    if relay.launcher.launches > launches_before:
        _last_launch_monotonic = time.monotonic()
    return ack


async def handle(state, ws, data):
    """Entry point del dispatch de server.py para {"t":"cap"}."""
    device = str(data.get("device") or "anon")[:64]
    relay = _get_relay()
    if time.monotonic() - _last_launch_monotonic < APPLY_COOLDOWN_S:
        # anti-thrash: dentro del cooldown NO se relanza; se reporta el perfil
        # vigente VERIFICADO (honesto: es el que corre, no el recien pedido).
        ack = {"t": "cap_ack",
               "applied": relay.applied is not None,
               "profile": relay.applied,
               "reason": "cooldown: se mantiene el perfil vigente verificado"}
    else:
        loop = asyncio.get_running_loop()
        # launch() mata y relanza scrcpy (bloqueante hasta 10 s): fuera del loop
        ack = await loop.run_in_executor(None, _handle_sync, data, device)
    try:
        await ws.send_str(json.dumps(ack))
    except Exception:
        pass  # el viewer se desconecto a mitad de la negociacion: no importa
    return ack
