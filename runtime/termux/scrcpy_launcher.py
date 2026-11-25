#!/usr/bin/env python3
"""ScrcpyLauncher: implementacion real de la interfaz launch()/read_cmdline()
de stream_profile.Relay contra el scrcpy.Server vivo del S25.

Correccion de campo 2026-11-25: el scrcpy.Server NO es un proceso de Termux;
lo arranca `adb shell env CLASSPATH=... app_process ...` en el TELEFONO. La
version anterior usaba pgrep local y /proc/<pid>/cmdline local, asi que nunca
veia el proceso: la negociacion cap respondia applied:false (o relanzaba en
bucle al perfil conservador) con el stream perfectamente vivo. Ahora todo se
lee y se mata por adb, apuntando al telefono por modelo (el adb server ve
tambien la tablet).

Ciclo (verificado por tests.test_stream_profile, 23/23):
  launch(profile)   -> kill -TERM del scrcpy.Server en el telefono,
                       relanzamiento de scrcpy-phone.sh con env del perfil
                       (NEW_DISPLAY/BITRATE/FPS), y espera del cmdline nuevo.
  read_cmdline()    -> cmdline del scrcpy.Server vivo en el telefono, o None.
                       Misma tecnica usada en campo para verificar 35 Mbps.

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
_DEFAULT_ADB = "/data/data/com.termux/files/usr/bin/adb"
ADB = os.environ.get("ADB") or (
    _DEFAULT_ADB if os.path.exists(_DEFAULT_ADB) else "adb")
PHONE_MODEL = os.environ.get("WTM_PHONE_MODEL", "SM-S931B")
CMDLINE_TIMEOUT = 10.0    # s: el relanzamiento debe ser legible en 10 s


def _run(args, timeout=5):
    """subprocess.run sin heredar el event loop; texto, sin excepciones."""
    try:
        return subprocess.run([ADB] + list(args), capture_output=True,
                              text=True, timeout=timeout)
    except Exception:
        return None


class ScrcpyLauncher:
    """Implementa launch()/read_cmdline() para stream_profile.Relay."""

    def __init__(self):
        self.launches = 0
        self._last_cmdline = None
        self._serial = None

    # -- descubrimiento del telefono -------------------------------------
    def phone_serial(self):
        """Serial adb del telefono (por modelo), no el de la tablet.

        El adb server del S25 ve tambien la tablet (SM-P613); elegir el
        primer `device` a secas mandaria los kills al dispositivo equivocado.
        """
        if self._serial:
            return self._serial
        out = _run(["devices"])
        if out is None:
            return None
        candidates = []
        for line in out.stdout.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "device":
                candidates.append(parts[0])
        # primero los transportes que no son emuladores locales
        candidates.sort(key=lambda s: s.startswith("emulator-"))
        for serial in candidates:
            model = _run(["-s", serial, "shell", "getprop ro.product.model"])
            if model is not None and model.stdout.strip() == PHONE_MODEL:
                self._serial = serial
                return self._serial
        return None

    def _phone_pids(self, serial):
        """PIDs de scrcpy.Server en el telefono (ps de Android, no pgrep local)."""
        out = _run(["-s", serial, "shell",
                    "ps -A -o PID,ARGS 2>/dev/null | grep '[s]crcpy.Server'"])
        if out is None:
            return []
        pids = []
        for line in out.stdout.splitlines():
            line = line.strip()
            if not line or "scrcpy.Server" not in line:
                continue
            head = line.split(None, 1)[0]
            if head.isdigit():
                pids.append(int(head))
        return pids

    # -- interfaz del Relay ----------------------------------------------
    def read_cmdline(self):
        """cmdline del scrcpy.Server vivo en el telefono; None si no hay.

        Contiene new_display=..., max_fps=... y video_bit_rate=... (los args
        con que scrcpy-phone.sh lanzo app_process). Es la unica fuente de
        verdad para el cap_ack: un perfil solo esta "aplicado" si aparece
        aqui exactamente como se pidio.
        """
        serial = self.phone_serial()
        if not serial:
            return None
        for pid in self._phone_pids(serial):
            out = _run(["-s", serial, "shell", f"cat /proc/{pid}/cmdline"])
            if out is None or not out.stdout:
                continue
            cmdline = out.stdout.replace("\x00", " ").strip()
            if "scrcpy.Server" in cmdline:
                self._last_cmdline = cmdline
                return cmdline
        return None

    def launch(self, profile):
        """Relanza scrcpy con el perfil pedido. Bloqueante (sincrono).

        Se invoca desde el handler cap a traves de run_in_executor para no
        bloquear el event loop del relay.
        """
        self.launches += 1
        serial = self.phone_serial()
        # 1. matar la sesion actual EN EL TELEFONO (si existe)
        if serial:
            for pid in self._phone_pids(serial):
                _run(["-s", serial, "shell", f"kill -TERM {pid}"])
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                if not self._phone_pids(serial):
                    break
                time.sleep(0.1)
        # 2. relanzar con env del perfil (scrcpy-phone.sh elige el telefono
        #    por modelo y rehace el `adb forward tcp:1234` del socket)
        env = dict(os.environ)
        env["WTM_PHONE_MODEL"] = PHONE_MODEL
        env["NEW_DISPLAY"] = f"{int(profile['w'])}x{int(profile['h'])}/240"
        env["BITRATE"] = str(int(profile["bitrate"]))
        env["FPS"] = str(int(round(profile["fps"])))
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
