#!/usr/bin/env python3
"""HEVC (H.265) bitstream helpers for the WTM relay.

Pure parsing/serialization for the scrcpy HEVC A/B experiment (issue #4,
docs/hevc-study.md). The shipped default remains H.264; nothing in the
runtime imports this module until the measured A/B wins.

Provides:
- hevc_nal_type(): NAL unit type from the 2-byte HEVC NAL header
- unescape_rbsp(): emulation-prevention removal (00 00 03 -> 00 00)
- parse_hevc_sps(): profile/tier/level, chroma format, bit depths and
  dimensions out of an SPS NAL (single-sub-layer only, which is what the
  scrcpy MediaCodec encoder emits)
- build_hvcc(): the HEVCDecoderConfigurationRecord ("hvcC") that the
  WebCodecs VideoDecoder expects in config.description
- hevc_codec_string(): "hvc1.<profile>.<compat>.<tier><level>" codec id
- build_hevc_config(): drop-in mirror of server.py's build_avcc()

Host-testable on any Python 3.10+; no I/O, no server dependency.
"""

HEVC_NAL_VPS = 32
HEVC_NAL_SPS = 33
HEVC_NAL_PPS = 34
HEVC_NAL_IDR_W_RADL = 19
HEVC_NAL_IDR_N_LP = 20
HEVC_NAL_CRA_NUT = 21


def hevc_nal_type(nal):
    """Return the HEVC NAL unit type (6 bits) or None if too short."""
    if not nal or len(nal) < 2:
        return None
    return (nal[0] >> 1) & 0x3F


def hevc_is_idr(nal):
    """True if the NAL starts an IDR key frame (W_RADL or N_LP)."""
    t = hevc_nal_type(nal)
    return t in (HEVC_NAL_IDR_W_RADL, HEVC_NAL_IDR_N_LP)


def unescape_rbsp(data):
    """Remove emulation-prevention bytes: 00 00 03 -> 00 00."""
    out = bytearray()
    i = 0
    n = len(data)
    while i < n:
        if i + 2 < n and data[i] == 0 and data[i + 1] == 0 and data[i + 2] == 3:
            out += data[i:i + 2]
            i += 3
        else:
            out.append(data[i])
            i += 1
    return bytes(out)


class _Bits:
    """MSB-first bit reader with ue(v) exp-golomb support."""

    def __init__(self, data):
        self.bits = "".join(f"{x:08b}" for x in data)
        self.pos = 0

    def u(self, n):
        if n == 0:
            return 0
        if self.pos + n > len(self.bits):
            raise ValueError("bitstream truncated")
        v = int(self.bits[self.pos:self.pos + n], 2)
        self.pos += n
        return v

    def ue(self):
        z = 0
        while z <= 32 and self.u(1) == 0:
            z += 1
        if z > 32:
            raise ValueError("bad exp-golomb prefix")
        suffix = self.u(z) if z else 0
        return (1 << z) - 1 + suffix


# SubWidthC/SubHeightC per chroma_format_idc (0 mono, 1 4:2:0, 2 4:2:2, 3 4:4:4)
_CHROMA_SUBSAMPLING = {0: (1, 1), 1: (2, 2), 2: (2, 1), 3: (1, 1)}


def parse_hevc_sps(sps):
    """Parse an HEVC SPS NAL (2-byte header included).

    Returns a dict with profile_space, tier, profile_idc, compat,
    constraints (6 bytes), level_idc, chroma_format_idc, width, height,
    bit_depth_luma_minus8, bit_depth_chroma_minus8 — or None when the SPS
    is truncated or uses multiple sub-layers (not produced by the scrcpy
    encoder; rejecting it beats guessing).
    """
    try:
        rbsp = unescape_rbsp(sps[2:])
        r = _Bits(rbsp)
        r.u(4)                       # sps_video_parameter_set_id
        max_sub_layers_minus1 = r.u(3)
        if max_sub_layers_minus1 != 0:
            return None              # multi-sub-layer not supported here
        r.u(1)                       # sps_temporal_id_nesting_flag
        out = {
            "profile_space": r.u(2),
            "tier": r.u(1),
            "profile_idc": r.u(5),
            "compat": r.u(32),
            "constraints": bytes(r.u(8) for _ in range(6)),
            "level_idc": r.u(8),
        }
        r.ue()                       # sps_seq_parameter_set_id
        chroma = r.ue()
        if chroma == 3:
            r.u(1)                   # separate_colour_plane_flag
        out["chroma_format_idc"] = chroma
        out["width"] = r.ue()        # pic_width_in_luma_samples
        out["height"] = r.ue()       # pic_height_in_luma_samples
        if r.u(1):                   # conformance_window_flag
            left = r.ue()
            right = r.ue()
            top = r.ue()
            bottom = r.ue()
            sw, sh = _CHROMA_SUBSAMPLING.get(chroma, (1, 1))
            out["width"] -= (left + right) * sw
            out["height"] -= (top + bottom) * sh
        out["bit_depth_luma_minus8"] = r.ue()
        out["bit_depth_chroma_minus8"] = r.ue()
        if out["width"] <= 0 or out["height"] <= 0:
            return None
        return out
    except (ValueError, IndexError):
        return None


def build_hvcc(vps, sps, pps, avg_frame_rate=0):
    """Build the HEVCDecoderConfigurationRecord (hvcC, ISO 14496-15).

    Fields not derivable from the SPS are written as "unspecified" per the
    spec, matching what hardware decoders accept. lengthSizeMinusOne=3
    matches the 4-byte NAL-length framing the relay already uses.
    """
    info = parse_hevc_sps(sps)
    if info is None:
        raise ValueError("unsupported HEVC SPS (truncated or multi-sub-layer)")
    rec = bytearray()
    rec.append(1)  # configurationVersion
    rec.append((info["profile_space"] << 6) | (info["tier"] << 5) | info["profile_idc"])
    rec += info["compat"].to_bytes(4, "big")
    rec += info["constraints"]
    rec.append(info["level_idc"])
    rec += b"\xf0\x00"  # reserved(1111) + min_spatial_segmentation_idc=0 (unspecified)
    rec.append(0xFC)    # reserved(111111) + parallelismType=0 (unspecified)
    rec.append(0xF8 | (info["chroma_format_idc"] & 0x07))
    rec.append(0xF8 | (info["bit_depth_luma_minus8"] & 0x07))
    rec.append(0xF8 | (info["bit_depth_chroma_minus8"] & 0x07))
    rec += int(avg_frame_rate).to_bytes(2, "big")
    rec.append(0x0F)    # constantFrameRate=0, numTemporalLayers=1, nested=1, lengthSizeMinusOne=3
    arrays = [
        (HEVC_NAL_VPS, [vps]),
        (HEVC_NAL_SPS, [sps]),
        (HEVC_NAL_PPS, [pps]),
    ]
    rec.append(len(arrays))
    for nal_type, nals in arrays:
        for nal in nals:
            if not nal:
                raise ValueError("empty NAL in hvcC array")
        rec.append(0x80 | nal_type)          # array_completeness=1
        rec += len(nals).to_bytes(2, "big")  # numNalus (2 bytes)
        for nal in nals:
            rec += len(nal).to_bytes(2, "big")  # nalUnitLength (2 bytes)
            rec += nal
    return bytes(rec)


def hevc_codec_string(sps):
    """Build the WebCodecs codec id: hvc1.<profile>.<compat>.<tier><level>.

    Example for Main 4:2:0 8-bit at level 3.1: "hvc1.1.6.L93".
    """
    info = parse_hevc_sps(sps)
    if info is None:
        raise ValueError("unsupported HEVC SPS (truncated or multi-sub-layer)")
    tier = "H" if info["tier"] else "L"
    compat = format(info["compat"], "x")
    return f"hvc1.{info['profile_idc']:x}.{compat}.{tier}{info['level_idc']}"


def build_hevc_config(vps, sps, pps):
    """Mirror of server.py build_avcc(): (codec_string, hvcc_bytes)."""
    return hevc_codec_string(sps), build_hvcc(vps, sps, pps)


if __name__ == "__main__":  # pragma: no cover
    print("hevc.py is a library module; run tests via tests.test_hevc")
