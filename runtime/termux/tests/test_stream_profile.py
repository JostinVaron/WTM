#!/usr/bin/env python3
"""Tests de la negociacion de perfil cap/cap_ack (stream_profile.py).

Matriz F3 (9 casos): pure logic contra un launcher simulado — no encoder, no
red, no dispositivo. Cubre: tablet real (perfil esperado exacto), flagship
OLED 120 Hz (clamp al techo declarado del emisor), metadata que miente (lo
aprendido gana en sesion 2), cap hostil (rechazado + conservador verificado),
emisor desobediente (applied:false honesto, jamas verde falso), 59.94 Hz NTSC
(fps float exacto, ceil solo al cmdline), rotacion (cero relanzamientos),
techo hardware-declarado con TV 4K (single-profile last-wins), y emisor
crash/timeout (applied:false sin colgarse).

Run: cd runtime/termux && python3 -m unittest tests.test_stream_profile -v
"""
import math
import sys
import unittest
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_DIR))

import stream_profile as sp  # noqa: E402


class FakeLauncher:
    """Simula scrcpy-phone.sh para los tests.

    modes:
      obey     arma el cmdline pedido exactamente (emisor obediente)
      disobey  aplica SIEMPRE video_bit_rate=35000000 (como el server.env viejo)
      crash    el proceso muere: no hay cmdline
      timeout  el proceso no responde: read_cmdline devuelve None
    """

    def __init__(self, mode="obey"):
        self.mode = mode
        self.launches = 0
        self.cmdline = None

    def launch(self, profile):
        self.launches += 1
        if self.mode == "crash":
            self.cmdline = None
            return
        opts = (f"new_display={profile['w']}x{profile['h']}/240 "
                f"max_fps={math.ceil(profile['fps'])} "
                f"video_bit_rate={profile['bitrate']}")
        if self.mode == "disobey":
            opts = opts.replace(f"video_bit_rate={profile['bitrate']}",
                                "video_bit_rate=35000000")
        self.cmdline = ("app_process / com.genymobile.scrcpy.Server 5.0.1 "
                        f"scid=7777 raw_stream=true {opts}")

    def read_cmdline(self):
        return None if self.mode == "timeout" else self.cmdline


# Perfil del receptor real de campo (Tab S6 Lite SM-P613, verificado en logs
# perf3): panel 2000x1200, decoder declara >=150 Mbps, link WiFi 866 Mbps.
TAB_CAP = {"w": 2000, "h": 1200, "fps": 60.0, "bitrate_max": 14_400_000,
           "link_mbps": 866}


class ReceiverBitrateTests(unittest.TestCase):
    def test_tablet_real_profile(self):
        """2000x1200@60 con decoder 150M y link 866: techo util = 14.4 Mbps."""
        bitrate = sp.receiver_bitrate(2000, 1200, 60.0, 150_000_000, 866)
        self.assertEqual(bitrate, 14_400_000)

    def test_ntsc_59_94_exact_float(self):
        """59.94 Hz debe calcularse sin redondear a 60 (float exacto)."""
        bitrate = sp.receiver_bitrate(2000, 1200, 59.94, 150_000_000, 866)
        self.assertEqual(bitrate, 14_385_600)

    def test_link_without_wifi_reports_zero(self):
        """link_mbps=0 (sin dato) no debe clampar por el enlace."""
        bitrate = sp.receiver_bitrate(1280, 720, 30, 6_000_000, 0)
        self.assertEqual(bitrate, 2_304_000)  # solo el techo util


class ValidateCapTests(unittest.TestCase):
    def test_hostile_8k_240_terabit_rejected(self):
        with self.assertRaises(sp.CapError):
            sp.validate_cap({"w": 7680, "h": 4320, "fps": 240,
                             "bitrate_max": 10**12})

    def test_zero_width_rejected(self):
        with self.assertRaises(sp.CapError):
            sp.validate_cap({"w": 0, "h": 1200, "fps": 60})

    def test_negative_bitrate_rejected(self):
        with self.assertRaises(sp.CapError):
            sp.validate_cap({"w": 2000, "h": 1200, "fps": 60,
                             "bitrate_max": -5})

    def test_missing_field_rejected(self):
        with self.assertRaises(sp.CapError):
            sp.validate_cap({"w": 2000, "h": 1200})


class ClampToEmitterTests(unittest.TestCase):
    def test_flagship_oled_120hz(self):
        """1440x3200@120 excede el techo del emisor: se escala a 972x2160."""
        bitrate = sp.receiver_bitrate(1440, 3200, 120.0, 100_000_000, 1200)
        self.assertEqual(bitrate, 55_296_000)
        profile = sp.clamp_to_emitter(
            {"w": 1440, "h": 3200, "fps": 120.0, "bitrate_max": bitrate})
        self.assertEqual(
            profile,
            {"w": 972, "h": 2160, "fps": 120.0, "bitrate": 55_296_000})

    def test_tv_4k_allowed_by_declared_ceiling(self):
        """DECISION DEL USUARIO: techo = hardware declarado; la TV 4K pasa."""
        profile = sp.clamp_to_emitter(
            {"w": 3840, "h": 2160, "fps": 60.0, "bitrate_max": 80_000_000})
        self.assertEqual(
            profile,
            {"w": 3840, "h": 2160, "fps": 60.0, "bitrate": 80_000_000})

    def test_scaled_dimensions_stay_even(self):
        """Los encoders exigen dimensiones pares tras el clamp."""
        profile = sp.clamp_to_emitter(
            {"w": 1441, "h": 3200, "fps": 60.0, "bitrate_max": 10_000_000})
        self.assertEqual(profile["w"] % 2, 0)
        self.assertEqual(profile["h"] % 2, 0)


class VerifyCmdlineTests(unittest.TestCase):
    def test_obedient_cmdline_verifies(self):
        launcher = FakeLauncher("obey")
        launcher.launch(dict(sp.CONSERVATIVE_PROFILE))
        ok, reason = sp.verify_cmdline(dict(sp.CONSERVATIVE_PROFILE),
                                       launcher.read_cmdline())
        self.assertTrue(ok, reason)

    def test_disobedient_bitrate_fails(self):
        launcher = FakeLauncher("disobey")
        launcher.launch(dict(sp.CONSERVATIVE_PROFILE))
        ok, _ = sp.verify_cmdline(dict(sp.CONSERVATIVE_PROFILE),
                                  launcher.read_cmdline())
        self.assertFalse(ok)

    def test_illegible_cmdline_fails(self):
        ok, reason = sp.verify_cmdline(dict(sp.CONSERVATIVE_PROFILE),
                                       "app_process sin opciones")
        self.assertFalse(ok)
        self.assertEqual(reason, "cmdline ilegible")

    def test_float_fps_uses_ceil_for_cmdline(self):
        asked = {"w": 2000, "h": 1200, "fps": 59.94, "bitrate": 14_385_600}
        launcher = FakeLauncher("obey")
        launcher.launch(asked)
        ok, reason = sp.verify_cmdline(asked, launcher.read_cmdline())
        self.assertTrue(ok, reason)


class RelayHandleCapTests(unittest.TestCase):
    def test_t1_tablet_real_applies_exact_profile(self):
        relay = sp.Relay(FakeLauncher("obey"))
        ack = relay.handle_cap(dict(TAB_CAP), device="tab")
        self.assertTrue(ack["applied"], ack)
        self.assertEqual(
            ack["profile"],
            {"w": 2000, "h": 1200, "fps": 60, "bitrate": 14_400_000})
        self.assertEqual(relay.launcher.launches, 1)

    def test_t3_learned_profile_beats_lying_metadata(self):
        """Sesion 1 aplica lo declarado; sesion 2 gana lo aprendido."""
        store = sp.ProfileStore()
        relay = sp.Relay(FakeLauncher("obey"), store)
        declared = {"w": 3840, "h": 2160, "fps": 30.0,
                    "bitrate_max": 40_000_000, "link_mbps": 866}
        ack1 = relay.handle_cap(declared, device="tab")
        self.assertTrue(ack1["applied"])
        self.assertEqual(ack1["profile"]["w"], 3840)
        # Telemetria de sesion 1: el 4K no se sostuvo; lo verificado fue 2K@60.
        store.record_verified(
            "tab", {"w": 2000, "h": 1200, "fps": 60,
                    "bitrate": 14_400_000})
        ack2 = relay.handle_cap(declared, device="tab")
        self.assertTrue(ack2["applied"])
        self.assertEqual(ack2["profile"]["w"], 2000)
        self.assertEqual(ack2["profile"]["bitrate"], 14_400_000)

    def test_t4_hostile_cap_falls_to_verified_conservative(self):
        relay = sp.Relay(FakeLauncher("obey"))
        for hostile in ({"w": 7680, "h": 4320, "fps": 240,
                         "bitrate_max": 10**12},
                        {"w": 0, "h": 1200, "fps": 60},
                        {"w": 2000, "h": 1200, "fps": 60,
                         "bitrate_max": -5}):
            ack = relay.handle_cap(hostile)
            self.assertIn("cap_rechazado", ack["reason"], ack)
            self.assertTrue(ack["applied"])
            self.assertEqual(ack["profile"],
                             {"w": 1280, "h": 720, "fps": 30,
                              "bitrate": 6_000_000})

    def test_t5_disobedient_emitter_never_fake_green(self):
        relay = sp.Relay(FakeLauncher("disobey"))
        ack = relay.handle_cap(dict(TAB_CAP))
        self.assertFalse(ack["applied"],
                         "applied no puede ser true si el cmdline contradice")
        self.assertIsNone(ack["profile"], "nunca reportar perfil no verificado")
        self.assertEqual(relay.launcher.launches, 2,
                         "debe reintentar una vez con conservador")

    def test_t6_ntsc_applies_with_ceil_cmdline(self):
        cap = {"w": 2000, "h": 1200, "fps": 59.94, "link_mbps": 866}
        cap["bitrate_max"] = sp.receiver_bitrate(2000, 1200, 59.94,
                                                 150_000_000, 866)
        relay = sp.Relay(FakeLauncher("obey"))
        ack = relay.handle_cap(cap)
        self.assertTrue(ack["applied"], ack)
        self.assertEqual(ack["profile"]["fps"], 60)  # ceil solo al cmdline

    def test_t7_rotation_never_relaunches(self):
        relay = sp.Relay(FakeLauncher("obey"))
        relay.handle_cap(dict(TAB_CAP, rot=0), device="tab")
        launches_before = relay.launcher.launches
        ack_a = relay.handle_cap(dict(TAB_CAP, rot=1), device="tab")
        ack_b = relay.handle_cap(dict(TAB_CAP, rot=0), device="tab")
        self.assertTrue(ack_a["applied"])
        self.assertTrue(ack_b["applied"])
        self.assertIn("ya activo", ack_a["reason"])
        self.assertIn("ya activo", ack_b["reason"])
        self.assertEqual(relay.launcher.launches, launches_before,
                         "rotar NO debe relanzar el stream")

    def test_t8_tv4k_single_profile_last_wins(self):
        relay = sp.Relay(FakeLauncher("obey"))
        relay.handle_cap(dict(TAB_CAP), device="tab")
        ack = relay.handle_cap({"w": 3840, "h": 2160, "fps": 60.0,
                                "bitrate_max": 80_000_000, "link_mbps": 0},
                               device="tv")
        self.assertTrue(ack["applied"],
                        "el techo DECLARADO debe permitir una TV 4K")
        self.assertEqual(
            ack["profile"],
            {"w": 3840, "h": 2160, "fps": 60, "bitrate": 80_000_000})
        self.assertEqual(relay.launcher.launches, 2,
                         "single-profile: el ultimo viewer autorizado manda")

    def test_t9_crash_and_timeout_never_hang(self):
        for mode in ("timeout", "crash"):
            relay = sp.Relay(FakeLauncher(mode))
            ack = relay.handle_cap(dict(TAB_CAP))
            self.assertFalse(ack["applied"], mode)
            self.assertIsNone(ack["profile"], mode)
            self.assertEqual(relay.launcher.launches, 2, mode)

    def test_repeat_cap_same_profile_no_relaunch(self):
        relay = sp.Relay(FakeLauncher("obey"))
        first = relay.handle_cap(dict(TAB_CAP), device="tab")
        second = relay.handle_cap(dict(TAB_CAP), device="tab")
        self.assertTrue(first["applied"])
        self.assertTrue(second["applied"])
        self.assertEqual(relay.launcher.launches, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
