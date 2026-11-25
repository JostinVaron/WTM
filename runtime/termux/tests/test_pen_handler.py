#!/usr/bin/env python3
"""Unit tests for the S Pen UHID path (F6, 2026-11-25).

These tests do not need a phone: they verify the wire formats against the
scrcpy v5 control protocol and the HID report descriptor semantics that the
Linux hid-input driver maps to BTN_TOOL_PEN / ABS_PRESSURE / ABS_TILT_X/Y.
"""
import asyncio
import math
import struct
import sys
import unittest
from pathlib import Path
from unittest import mock

SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_DIR))

import pen_handler  # noqa: E402
import server  # noqa: E402


class FakeControl:
    def __init__(self):
        self.writer = object()      # _ensure() exige un writer vivo
        self.sent = []

    def send(self, data):
        self.sent.append(data)


class FakeServer:
    def __init__(self):
        self.scrcpy_control = FakeControl()
        self._scrcpy_session = 7
        self.ctrl_touch = server.ctrl_touch


class FakeState:
    def __init__(self, w=2000, h=1200):
        self.scrcpy_w = w
        self.scrcpy_h = h


class StartAppMessageTest(unittest.TestCase):
    def test_type_and_length_prefix(self):
        msg = server.ctrl_start_app("com.android.settings")
        self.assertEqual(msg[0], server.CTRL_TYPE_START_APP)
        self.assertEqual(msg[1], len("com.android.settings"))
        self.assertEqual(msg[2:], b"com.android.settings")

    def test_truncates_at_255_bytes(self):
        msg = server.ctrl_start_app("x" * 400)
        self.assertEqual(msg[1], 255)
        self.assertEqual(len(msg), 2 + 255)


class HidDescriptorTest(unittest.TestCase):
    def setUp(self):
        self.desc = pen_handler._report_desc(2000, 1200)

    def test_is_pen_application(self):
        # Usage Page Digitizers, Usage Pen, Collection Application: hid-input
        # marca INPUT_PROP_DIRECT solo con application == 0x02 (Pen).
        self.assertEqual(self.desc[:6], bytes((0x05, 0x0D, 0x09, 0x02, 0xA1, 0x01)))

    def test_has_tip_inrange_eraser_barrel(self):
        for usage in (0x42, 0x32, 0x45, 0x44):
            self.assertIn(bytes((0x09, usage)), self.desc)

    def test_has_pressure_and_tilt(self):
        self.assertIn(bytes((0x09, 0x30)), self.desc)      # Tip Pressure
        self.assertIn(bytes((0x09, 0x3D)), self.desc)      # X Tilt
        self.assertIn(bytes((0x09, 0x3E)), self.desc)      # Y Tilt
        self.assertIn(bytes((0x26, 0xFF, 0x0F)), self.desc)  # max 4095

    def test_independent_x_y_ranges(self):
        # X max 1999 = 0x07CF, Y max 1199 = 0x04AF
        self.assertIn(bytes((0x26, 0xCF, 0x07)), self.desc)
        self.assertIn(bytes((0x26, 0xAF, 0x04)), self.desc)

    def test_report_is_nine_bytes(self):
        report = pen_handler._report(123, 456, 4095, 30, -20, True, True, False)
        self.assertEqual(len(report), 9)
        flags, x, y, pressure, tx, ty = struct.unpack("<BHHHbb", report)
        self.assertEqual(flags, 0b11)      # tip + in range
        self.assertEqual((x, y, pressure), (123, 456, 4095))
        self.assertEqual((tx, ty), (30, -20))

    def test_eraser_bit(self):
        report = pen_handler._report(1, 2, 100, 0, 0, True, True, True)
        self.assertEqual(report[0] & 0b100, 0b100)


def _android_reconstruct(tx_deg, ty_deg):
    """Misma formula que TouchInputMapper.cpp de AOSP."""
    tx = math.radians(tx_deg)
    ty = math.radians(ty_deg)
    orientation = math.degrees(math.atan2(-math.sin(tx), math.sin(ty)))
    tilt = math.degrees(math.acos(math.cos(tx) * math.cos(ty)))
    return orientation, tilt


def _angle_delta(a, b):
    return abs((a - b + 180.0) % 360.0 - 180.0)


class TiltConversionTest(unittest.TestCase):
    def assert_roundtrip(self, tilt, orientation):
        tx, ty = pen_handler._tilt_xy(tilt, orientation)
        got_or, got_tilt = _android_reconstruct(tx, ty)
        self.assertLess(_angle_delta(got_or, math.degrees(orientation)), 1.0,
                        (tilt, orientation, tx, ty, got_or))
        self.assertLess(abs(got_tilt - math.degrees(tilt)), 1.0,
                        (tilt, orientation, tx, ty, got_tilt))

    def test_perpendicular_is_zero(self):
        self.assertEqual(pen_handler._tilt_xy(0.0, 0.0), (0, 0))

    def test_orientation_zero_roundtrip(self):
        self.assert_roundtrip(math.pi / 4, 0.0)

    def test_orientation_plus_90_roundtrip(self):
        self.assert_roundtrip(math.pi / 4, math.pi / 2)

    def test_orientation_minus_57_roundtrip(self):
        self.assert_roundtrip(0.5, -1.0)

    def test_orientation_minus_143_roundtrip(self):
        self.assert_roundtrip(math.pi / 3, -2.5)

    def test_orientation_45_tilt_60_roundtrip(self):
        self.assert_roundtrip(math.pi / 3, math.pi / 4)

    def test_clamps(self):
        tx, ty = pen_handler._tilt_xy(math.pi, 0.0)
        self.assertLessEqual(abs(tx), 90)
        self.assertLessEqual(abs(ty), 90)


class UhidWireFormatTest(unittest.TestCase):
    def setUp(self):
        self.srv = FakeServer()
        pen_handler._uhid_ready = False
        pen_handler._uhid_session = -1

    def test_create_message(self):
        pen_handler._uhid_create(self.srv, 2000, 1200)
        msg = self.srv.scrcpy_control.sent[0]
        self.assertEqual(msg[0], pen_handler.CTRL_TYPE_UHID_CREATE)
        ident, vid, pid, name_len = struct.unpack(">HHHB", msg[1:8])
        self.assertEqual((ident, vid, pid), (pen_handler.UHID_ID,
                                             pen_handler.UHID_VID,
                                             pen_handler.UHID_PID))
        self.assertEqual(name_len, len(pen_handler.UHID_NAME))
        pos = 8 + name_len
        desc_len = struct.unpack(">H", msg[pos:pos + 2])[0]
        desc = msg[pos + 2:]
        self.assertEqual(desc_len, len(desc))
        self.assertEqual(desc, pen_handler._report_desc(2000, 1200))

    def test_ensure_recreates_on_new_session(self):
        state = FakeState()
        self.assertTrue(pen_handler._ensure(self.srv, state))
        self.assertEqual(len(self.srv.scrcpy_control.sent), 1)
        # misma sesion: no duplica
        self.assertTrue(pen_handler._ensure(self.srv, state))
        self.assertEqual(len(self.srv.scrcpy_control.sent), 1)
        # sesion nueva: recrea el dispositivo (el server viejo murio)
        self.srv._scrcpy_session = 8
        self.assertTrue(pen_handler._ensure(self.srv, state))
        self.assertEqual(len(self.srv.scrcpy_control.sent), 2)

    def test_input_message(self):
        report = pen_handler._report(10, 20, 3000, 5, -5, True, True)
        pen_handler._send_report(self.srv, report)
        msg = self.srv.scrcpy_control.sent[0]
        self.assertEqual(msg[0], pen_handler.CTRL_TYPE_UHID_INPUT)
        ident, size = struct.unpack(">HH", msg[1:5])
        self.assertEqual(ident, pen_handler.UHID_ID)
        self.assertEqual(size, len(report))
        self.assertEqual(msg[5:], report)


class DispatchTest(unittest.TestCase):
    def setUp(self):
        self.srv = FakeServer()
        self.state = FakeState()
        pen_handler._uhid_ready = False
        pen_handler._uhid_session = -1
        pen_handler._stylus_active_until = 0.0
        pen_handler._finger_pids = set()
        self.patch = mock.patch.object(pen_handler, "_srv_module", lambda: self.srv)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()

    def test_finger_uses_inject_touch(self):
        pen_handler._handle(self.state, {"a": 0, "pid": 1, "x": 10, "y": 20,
                                         "p": 0.5, "pt": "finger"})
        msg = self.srv.scrcpy_control.sent[-1]
        self.assertEqual(msg[0], server.CTRL_TYPE_INJECT_TOUCH)
        self.assertEqual(msg[1], 0)                 # ACTION_DOWN
        self.assertEqual(1, pen_handler._finger_pids.pop())

    def test_stylus_creates_uhid_and_reports_pressure(self):
        pen_handler._handle(self.state, {"a": 0, "pid": 0, "x": 100, "y": 200,
                                         "p": 0.5, "pt": "stylus",
                                         "tl": 0.0, "or": 0.0})
        sent = self.srv.scrcpy_control.sent
        self.assertEqual(sent[0][0], pen_handler.CTRL_TYPE_UHID_CREATE)
        self.assertEqual(sent[-1][0], pen_handler.CTRL_TYPE_UHID_INPUT)
        report = sent[-1][5:]
        flags, x, y, pressure, _, _ = struct.unpack("<BHHHbb", report)
        self.assertEqual(flags, 0b11)
        self.assertEqual((x, y), (100, 200))
        self.assertGreater(pressure, 2000)          # 0.5 * 4095

    def test_hover_has_no_pressure(self):
        pen_handler._handle(self.state, {"a": 7, "pid": 0, "x": 100, "y": 200,
                                         "p": 1.0, "pt": "stylus"})
        report = self.srv.scrcpy_control.sent[-1][5:]
        flags, _, _, pressure, _, _ = struct.unpack("<BHHHbb", report)
        self.assertEqual(flags, 0b10)               # in range, sin tip
        self.assertEqual(pressure, 0)

    def test_eraser_sets_tool(self):
        pen_handler._handle(self.state, {"a": 0, "pid": 0, "x": 10, "y": 20,
                                         "p": 0.2, "pt": "stylus",
                                         "tool": "eraser"})
        report = self.srv.scrcpy_control.sent[-1][5:]
        self.assertEqual(report[0] & 0b100, 0b100)

    def test_palm_dropped(self):
        pen_handler._handle(self.state, {"a": 0, "pid": 0, "x": 1, "y": 2,
                                         "p": 1.0, "pt": "finger",
                                         "tool": "palm"})
        self.assertEqual(self.srv.scrcpy_control.sent, [])

    def test_finger_while_stylus_active_is_dropped_and_released(self):
        # un dedo ya abajo
        pen_handler._handle(self.state, {"a": 0, "pid": 2, "x": 5, "y": 6,
                                         "p": 0.5, "pt": "finger"})
        self.assertIn(2, pen_handler._finger_pids)
        # entra el stylus: desde aqui el dedo se rechaza
        pen_handler._handle(self.state, {"a": 0, "pid": 0, "x": 7, "y": 8,
                                         "p": 0.5, "pt": "stylus"})
        before = len(self.srv.scrcpy_control.sent)
        pen_handler._handle(self.state, {"a": 2, "pid": 2, "x": 9, "y": 10,
                                         "p": 0.5, "pt": "finger"})
        self.assertEqual(len(self.srv.scrcpy_control.sent), before + 1)
        # el unico mensaje nuevo es el UP de liberacion
        msg = self.srv.scrcpy_control.sent[-1]
        self.assertEqual(msg[0], server.CTRL_TYPE_INJECT_TOUCH)
        self.assertEqual(msg[1], 1)                 # ACTION_UP
        self.assertNotIn(2, pen_handler._finger_pids)


class OutOfRangeAsyncTest(unittest.IsolatedAsyncioTestCase):
    """Ciclo de vida del hover: nunca dejar la pluma en rango para siempre.

    Regresion medida en campo 2026-11-25: tras usar el S Pen, tactil y mouse
    quedaban muertos porque la pluma UHID seguia "in range" indefinidamente.
    """

    async def asyncSetUp(self):
        self.srv = FakeServer()
        self.state = FakeState()
        pen_handler._uhid_ready = False
        pen_handler._uhid_session = -1
        pen_handler._stylus_active_until = 0.0
        pen_handler._finger_pids = set()
        pen_handler._cancel_oor()
        self.patch = mock.patch.object(pen_handler, "_srv_module",
                                       lambda: self.srv)
        self.patch.start()

    async def asyncTearDown(self):
        self.patch.stop()
        pen_handler._cancel_oor()

    @staticmethod
    def hover(**extra):
        data = {"a": 7, "pid": 0, "x": 100, "y": 200,
                "p": 0.0, "pt": "stylus"}
        data.update(extra)
        return data

    def test_hover_exit_sends_out_of_range_immediately(self):
        pen_handler._handle(self.state, self.hover())
        before = len(self.srv.scrcpy_control.sent)
        pen_handler._handle(self.state, self.hover(a=10))
        self.assertEqual(len(self.srv.scrcpy_control.sent), before + 1)
        report = self.srv.scrcpy_control.sent[-1][5:]
        self.assertEqual(report[0] & 0b11, 0)     # sin tip ni in_range

    async def test_hover_timer_is_a_real_fallback(self):
        with mock.patch.object(pen_handler, "_oor_delay", lambda: 0.05):
            pen_handler._handle(self.state, self.hover())
            self.assertIsNotNone(pen_handler._oor_task)
            await asyncio.sleep(0.12)
        report = self.srv.scrcpy_control.sent[-1][5:]
        self.assertEqual(report[0] & 0b11, 0)     # fuera de rango

    async def test_touch_cancels_the_hover_timer(self):
        with mock.patch.object(pen_handler, "_oor_delay", lambda: 0.5):
            pen_handler._handle(self.state, self.hover())
            task = pen_handler._oor_task
            pen_handler._handle(self.state, {"a": 0, "pid": 0, "x": 10,
                                             "y": 20, "p": 0.5,
                                             "pt": "stylus"})
            await asyncio.sleep(0)
            self.assertTrue(task.cancelled())


if __name__ == "__main__":
    unittest.main()
