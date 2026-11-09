#!/usr/bin/env python3
"""Host tests for the HEVC bitstream helpers (runtime/termux/hevc.py).

Pure parsing/serialization checks against hand-built NALs: no encoder, no
network, no device. Covers NAL typing, IDR detection, emulation-prevention
unescaping, SPS parsing (profile/tier/level, chroma, bit depths, dimensions
with and without conformance window), rejection of truncated and
multi-sub-layer SPS, hvcC byte layout, and codec-string formatting.

Run: cd runtime/termux && python3 -m unittest tests.test_hevc -v
"""
import sys
import unittest
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_DIR))

import hevc  # noqa: E402


class _W:
    """MSB-first bit writer with exp-golomb ue(v) support (test helper)."""

    def __init__(self):
        self.bits = []

    def u(self, n, value):
        for i in range(n - 1, -1, -1):
            self.bits.append((value >> i) & 1)

    def ue(self, value):
        value += 1
        width = value.bit_length()
        self.u(width - 1, 0)
        self.u(width, value)

    def rbsp(self):
        bits = list(self.bits)
        while len(bits) % 8:
            bits.append(0)
        return bytes(
            int("".join(str(b) for b in bits[i:i + 8]), 2)
            for i in range(0, len(bits), 8)
        )


def _nal_header(nal_type):
    """2-byte HEVC NAL header: forbidden=0, type, layer_id=0, tid_plus1=1."""
    return bytes([(nal_type << 1) & 0x7E, 0x01])


def _build_sps(width=2000, height=1200, chroma=1, max_sub_layers=0,
               window=None, level=120, profile_idc=1,
               compat=0x60000000, tier=0):
    """Build a minimal HEVC SPS NAL covering the fields hevc.py reads."""
    w = _W()
    w.u(4, 0)                 # sps_video_parameter_set_id
    w.u(3, max_sub_layers)    # sps_max_sub_layers_minus1
    w.u(1, 1)                 # sps_temporal_id_nesting_flag
    w.u(2, 0)                 # general_profile_space
    w.u(1, tier)              # general_tier_flag
    w.u(5, profile_idc)       # general_profile_idc
    w.u(32, compat)           # general_profile_compatibility_flags
    for _ in range(6):
        w.u(8, 0)             # general_constraint_indicator_flags
    w.u(8, level)             # general_level_idc
    w.ue(0)                   # sps_seq_parameter_set_id
    w.ue(chroma)              # chroma_format_idc
    if chroma == 3:
        w.u(1, 0)             # separate_colour_plane_flag
    w.ue(width)               # pic_width_in_luma_samples
    w.ue(height)              # pic_height_in_luma_samples
    if window:
        w.u(1, 1)             # conformance_window_flag
        for offset in window:  # left, right, top, bottom
            w.ue(offset)
    else:
        w.u(1, 0)
    w.ue(0)                   # bit_depth_luma_minus8
    w.ue(0)                   # bit_depth_chroma_minus8
    # (remaining SPS fields are past everything hevc.py reads)
    return _nal_header(hevc.HEVC_NAL_SPS) + w.rbsp()


VPS_NAL = _nal_header(hevc.HEVC_NAL_VPS) + b"\xaa\xbb"
PPS_NAL = _nal_header(hevc.HEVC_NAL_PPS) + b"\xcc\xdd"


class NalTypeTests(unittest.TestCase):
    def test_nal_type_extraction(self):
        self.assertEqual(hevc.hevc_nal_type(bytes([0x42, 0x01, 0x00])), 33)
        self.assertEqual(hevc.hevc_nal_type(bytes([0x40, 0x01])), 32)
        self.assertEqual(hevc.hevc_nal_type(bytes([0x02, 0x01])), 1)

    def test_idr_detection(self):
        for nal_type in (hevc.HEVC_NAL_IDR_W_RADL, hevc.HEVC_NAL_IDR_N_LP):
            nal = _nal_header(nal_type) + b"\x00"
            self.assertTrue(hevc.hevc_is_idr(nal), nal_type)
        for nal_type in (0, 1, hevc.HEVC_NAL_CRA_NUT, 33):
            nal = _nal_header(nal_type) + b"\x00"
            self.assertFalse(hevc.hevc_is_idr(nal), nal_type)

    def test_short_nal_is_none(self):
        self.assertIsNone(hevc.hevc_nal_type(b""))
        self.assertIsNone(hevc.hevc_nal_type(b"\x42"))


class RbspTests(unittest.TestCase):
    def test_emulation_prevention_removed(self):
        self.assertEqual(hevc.unescape_rbsp(b"\x00\x00\x03\xff"), b"\x00\x00\xff")
        self.assertEqual(hevc.unescape_rbsp(b"\x00\x00\x03\x00"), b"\x00\x00\x00")
        self.assertEqual(hevc.unescape_rbsp(b"\x00\x00\xff"), b"\x00\x00\xff")
        self.assertEqual(hevc.unescape_rbsp(b"\x00\x00\x03"), b"\x00\x00")


class SpsParseTests(unittest.TestCase):
    def test_main_8bit_2000x1200(self):
        sps = _build_sps(width=2000, height=1200, chroma=1, level=120)
        info = hevc.parse_hevc_sps(sps)
        self.assertIsNotNone(info)
        self.assertEqual(info["profile_space"], 0)
        self.assertEqual(info["tier"], 0)
        self.assertEqual(info["profile_idc"], 1)
        self.assertEqual(info["compat"], 0x60000000)
        self.assertEqual(info["constraints"], b"\x00" * 6)
        self.assertEqual(info["level_idc"], 120)
        self.assertEqual(info["chroma_format_idc"], 1)
        self.assertEqual(info["width"], 2000)
        self.assertEqual(info["height"], 1200)
        self.assertEqual(info["bit_depth_luma_minus8"], 0)
        self.assertEqual(info["bit_depth_chroma_minus8"], 0)

    def test_conformance_window_chroma420(self):
        sps = _build_sps(width=2000, height=1200, chroma=1, window=(2, 2, 0, 0))
        info = hevc.parse_hevc_sps(sps)
        self.assertEqual(info["width"], 1992)
        self.assertEqual(info["height"], 1200)
        sps = _build_sps(width=2000, height=1200, chroma=1, window=(0, 0, 4, 4))
        info = hevc.parse_hevc_sps(sps)
        self.assertEqual(info["width"], 2000)
        self.assertEqual(info["height"], 1184)

    def test_conformance_window_monochrome(self):
        sps = _build_sps(width=2000, height=1200, chroma=0, window=(2, 2, 0, 0))
        info = hevc.parse_hevc_sps(sps)
        self.assertEqual(info["chroma_format_idc"], 0)
        self.assertEqual(info["width"], 1996)

    def test_high_tier_and_level_fields(self):
        sps = _build_sps(width=1600, height=960, chroma=1, tier=1,
                         profile_idc=4, compat=0x30000000, level=150)
        info = hevc.parse_hevc_sps(sps)
        self.assertEqual(info["tier"], 1)
        self.assertEqual(info["profile_idc"], 4)
        self.assertEqual(info["compat"], 0x30000000)
        self.assertEqual(info["level_idc"], 150)
        self.assertEqual(info["width"], 1600)
        self.assertEqual(info["height"], 960)

    def test_truncated_sps_rejected(self):
        self.assertIsNone(hevc.parse_hevc_sps(bytes([0x42, 0x01, 0x01])))
        self.assertIsNone(hevc.parse_hevc_sps(bytes([0x42, 0x01])))

    def test_multi_sub_layer_rejected(self):
        sps = _build_sps(max_sub_layers=1)
        self.assertIsNone(hevc.parse_hevc_sps(sps))

    def test_emulation_prevention_in_sps(self):
        # Identical SPS payloads, one with an emulation-prevention byte
        # inserted in the constraint-bytes zero run, must parse equal.
        plain = _build_sps(width=1000, height=600, chroma=1, level=90)
        escaped = bytearray(plain)
        escaped.insert(9, 0x03)  # inside the 6 zero constraint bytes
        escaped = bytes(escaped)
        self.assertNotEqual(plain, escaped)
        self.assertEqual(hevc.parse_hevc_sps(plain),
                         hevc.parse_hevc_sps(escaped))


class HvccTests(unittest.TestCase):
    def test_hvcc_byte_layout(self):
        sps = _build_sps(width=2000, height=1200, chroma=1, level=120)
        rec = hevc.build_hvcc(VPS_NAL, sps, PPS_NAL)
        self.assertEqual(rec[0], 1)                      # configurationVersion
        self.assertEqual(rec[1], 0x01)                   # space=0,tier=0,profile=1
        self.assertEqual(rec[2:6], b"\x60\x00\x00\x00")  # compat
        self.assertEqual(rec[6:12], b"\x00" * 6)          # constraints
        self.assertEqual(rec[12], 120)                   # level_idc
        self.assertEqual(rec[13:15], b"\xf0\x00")        # reserved + mssi
        self.assertEqual(rec[15], 0xFC)                   # reserved + parallelism
        self.assertEqual(rec[16], 0xF9)                  # reserved + chroma 1
        self.assertEqual(rec[17], 0xF8)                  # luma depth 8
        self.assertEqual(rec[18], 0xF8)                  # chroma depth 8
        self.assertEqual(rec[19:21], b"\x00\x00")        # avgFrameRate
        self.assertEqual(rec[21], 0x0F)                  # 4-byte NAL lengths
        self.assertEqual(rec[22], 3)                     # num of arrays
        offset = 23
        for nal_type, nal in (
                (hevc.HEVC_NAL_VPS, VPS_NAL),
                (hevc.HEVC_NAL_SPS, sps),
                (hevc.HEVC_NAL_PPS, PPS_NAL)):
            self.assertEqual(rec[offset], 0x80 | nal_type)
            self.assertEqual(rec[offset + 1:offset + 3], b"\x00\x01")
            declared = int.from_bytes(rec[offset + 3:offset + 5], "big")
            self.assertEqual(declared, len(nal))
            self.assertEqual(rec[offset + 5:offset + 5 + declared], nal)
            offset += 5 + declared
        self.assertEqual(offset, len(rec))

    def test_hvcc_rejects_unsupported_sps(self):
        bad = _build_sps(max_sub_layers=1)
        with self.assertRaises(ValueError):
            hevc.build_hvcc(VPS_NAL, bad, PPS_NAL)

    def test_hvcc_rejects_empty_nal(self):
        sps = _build_sps()
        with self.assertRaises(ValueError):
            hevc.build_hvcc(b"", sps, PPS_NAL)


class CodecStringTests(unittest.TestCase):
    def test_codec_string_main(self):
        sps = _build_sps(width=2000, height=1200, level=120)
        self.assertEqual(hevc.hevc_codec_string(sps), "hvc1.1.60000000.L120")

    def test_codec_string_high_tier(self):
        sps = _build_sps(width=1600, height=960, tier=1, level=150)
        self.assertEqual(hevc.hevc_codec_string(sps), "hvc1.1.60000000.H150")

    def test_codec_string_rejects_bad_sps(self):
        with self.assertRaises(ValueError):
            hevc.hevc_codec_string(bytes([0x42, 0x01]))

    def test_build_hevc_config_pair(self):
        sps = _build_sps()
        codec, hvcc = hevc.build_hevc_config(VPS_NAL, sps, PPS_NAL)
        self.assertEqual(codec, "hvc1.1.60000000.L120")
        self.assertEqual(hvcc[0], 1)
        self.assertEqual(hvcc[22], 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
