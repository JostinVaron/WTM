#!/usr/bin/env python3
"""ScrcpyLauncher: implementacion real de la interfaz launch()/read_cmdline()
de stream_profile.Relay contra el scrcpy.Server vivo del S25.

Ciclo (verificado por tests.test_stream_profile, 23/23):
  launch(profile)   -> pkill del scrcpy.Server actual, relanzamiento de
                       scrcpy-phone.sh con env del perfil (NEW_DISPLAY/BITRATE/
                       FPS), y espera del cmdline del proceso nuevo (10 s max).
  read_cmdline()    -> cmdline del proceso scrcpy.Server vivo (pgrep /proc),
                       o None si murio/no hay. Misma tecnica usada en campo
                       para verificar que corria a 35 Mbps reales.

Costo aceptado (documentado en F2): el relanzamiento mata el stream ~1-2 s,
UNA vez, solo si el perfil pedido difiere del activo. scrcpy_reader ya
reconecta solo y re-broadcastea la config tras el reinicio del encoder.

Run: se consume desde server.py (handler cap) — no tiene main.
"""
import asyncio
import os
import subprocess
import time

import stream_profile


SCRCPY_SH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "scrcpy-phone.sh")
PGREP = "pgrep"          # busybox/coreutils en Termux lo traen
CMDLINE_TIMEOUT = 10.0    # s: el relanzamiento debe ser legible en 10 s


def _pgrep_scrcpy_pids():
    try:
        out = subprocess.run([PGREP, "-f", "scrcpy.Server"],
                             capture_output=True, text=True, timeout=3)
        return [int(x) for x in out.stdout.split() if x.strip().isdigit()]
    except Exception:
        return []


class ScrcpyLauncher:
    """Implementa launch()/read_cmdline() para stream_profile.Relay."""

    def __init__(self):
        self.launches = 0
        self._last_cmdline = None

    def read_cmdline(self):
        """cmdline del scrcpy.Server vivo; None si no hay proceso.

        Misma tecnica de campo: /proc/<pid>/cmdline del proceso scrcpy
        contiene new_display=..., max_fps=..., video_bit_rate=... (los args
        con que app_process lo lanzo scrcpy-phone.sh).
        """
        for pid in _pgrep_scrcpy_pids():
            try:
                with open(f"/proc/{pid}/cmdline", "rb") as handle:
                    raw = handle.read()
                cmdline = raw.replace(b"\x00", b" ").decode("utf-8", "replace")
                if "scrcpy.Server" in cmdline:
                    self._last_cmdline = cmdline
                    return cmdline
            except OSError:
                continue
        return None

    def launch(self, profile):
        """Relanza scrcpy con el perfil pedido. Bloqueante (sincrono).

        Se invoca desde el handler cap a traves de run_in_executor para no
        bloquear el event loop del relay.
        """
        self.launches += 1
        # 1. matar la sesion actual (si existe)
        for pid in _pgrep_scrcpy_pids():
            try:
                os.kill(pid, 15)  # SIGTERM
            except OSError:
                pass
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if not _pgrep_scrcpy_pids():
                break
            time.sleep(0.1)
        # 2. relanzar con env del perfil
        env = dict(os.environ)
        env["NEW_DISPLAY"] = f"{profile['w']}x{profile['h']}/240"
        env["BITRATE"] = str(profile["bitrate"])
        env["FPS"] = str(int(profile["fps"]))
        try:
            subprocess.Popen(
                ["bash", SCRCPY_SH],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                start_new_session=True,  # sobrevive al handler; watchdog lo cuida
            )
        except Exception as exc:
            print(f"[cap] launch failed: {exc}", flush=True)
            self._last_cmdline = None
            return
        # 3. esperar cmdline legible (el server tarda en arrancar)
        deadline = time.monotonic() + CMDLINE_TIMEOUT
        while time.monotonic() < deadline:
            cmdline = self.read_cmdline()
            if cmdline is not None and stream_profile.parse_cmdline(cmdline) is not None:
                return
            time.sleep(0.2)
        self._last_cmdline = None

    async def launch_async(self, profile):
        """Wrapper async de launch() para el event loop del relay."""
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self.launch, profile)
