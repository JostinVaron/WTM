#!/usr/bin/env python3
"""
Remote display server: low-latency H.264 screen streaming + input injection.

Two backends share this process:
- scrcpy backend (default in Termux): consumes the raw H.264 stream produced
  by the scrcpy Android server (virtual display 2000x1200@60) and relays it
  over WebSocket to a WebCodecs client (Chrome on the tablet). Pointer and
  keyboard events are injected through the scrcpy control socket.
- X11 backend: captures an X display (Xvfb :1) with ffmpeg/libx264 and injects
  input with XTEST.

Security model: HTTPS-only with a per-install certificate, Host allowlist,
same-site Origin enforcement, and cookie-based session tokens loaded from a
per-install auth file (viewer/admin roles). The service refuses to start
without valid TLS material and an auth file.

Env: DISPLAY(:1) W H FPS PORT CRF PRESET GOP VIDEO_BITRATE CERT KEY
     WTM_BIND_HOST WTM_ALLOWED_HOSTS WTM_AUTH_FILE
"""
import asyncio
import base64
import calendar
import hashlib
import hmac
import ipaddress
import json
import os
import re
import ssl
import struct
import time
from urllib.parse import urlsplit

from aiohttp import web, WSMsgType

try:
    from Xlib import display as xdisplay, X, XK
    from Xlib.ext import xtest
    HAS_XLIB = True
except Exception:
    HAS_XLIB = False

DISPLAY = os.environ.get("DISPLAY", ":1")
W = int(os.environ.get("W", "2000"))
H = int(os.environ.get("H", "1200"))
FPS = int(os.environ.get("FPS", "60"))
PORT = int(os.environ.get("PORT", "8443"))
CRF = os.environ.get("CRF", "18")
PRESET = os.environ.get("PRESET", "superfast")
GOP = int(os.environ.get("GOP", str(FPS)))
VIDEO_BITRATE = os.environ.get("VIDEO_BITRATE", "0")  # kbps cap, 0 = pure CRF
CERT = os.environ.get("CERT", "")
KEY = os.environ.get("KEY", "")

HERE = os.path.dirname(os.path.abspath(__file__))
# Directorio temporal portable: en Termux nativo /tmp no es escribible, se usa $PREFIX/tmp.
TMPDIR = os.environ.get("TMPDIR") or "/data/data/com.termux/files/usr/tmp"
AUTH_FILE = os.environ.get(
    "WTM_AUTH_FILE",
    os.path.join(os.environ.get("HOME", ""), ".config", "wtm", "auth-tokens.json"),
)
AUTH_COOKIE = "wtm_session"
AUTH_WINDOW_SECONDS = 300
AUTH_MAX_ATTEMPTS = 6
_auth_attempts = {}
_CERT_TIME_FORMAT = "%b %d %H:%M:%S %Y %Z"


def load_auth_tokens(path):
    try:
        mode = os.stat(path).st_mode & 0o777
        if mode & 0o077:
            raise RuntimeError("WTM auth file must not be group/world accessible")
        with open(path, encoding="utf-8") as handle:
            tokens = json.load(handle)
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"Unable to load WTM auth file: {exc}") from exc
    if not isinstance(tokens, dict) or set(tokens) != {"viewer", "admin"}:
        raise RuntimeError("WTM auth file must contain viewer and admin tokens")
    if any(not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value)
           for value in tokens.values()):
        raise RuntimeError("WTM auth tokens must be 256-bit lowercase hexadecimal values")
    if hmac.compare_digest(tokens["viewer"], tokens["admin"]):
        raise RuntimeError("WTM viewer and admin tokens must be different")
    return tokens


def load_server_config():
    auth_tokens = load_auth_tokens(AUTH_FILE)
    bind_host = os.environ.get("WTM_BIND_HOST", "127.0.0.1")
    default_hosts = {"localhost", "127.0.0.1", "::1"}
    configured_hosts = {
        host.strip().lower()
        for host in os.environ.get("WTM_ALLOWED_HOSTS", "").split(",")
        if host.strip()
    }
    allowed_hosts = default_hosts | configured_hosts
    if bind_host not in {"127.0.0.1", "::1", "localhost"} and not configured_hosts:
        raise RuntimeError(
            "WTM_ALLOWED_HOSTS must be set before binding beyond loopback")
    cert = os.environ.get(
        "CERT", os.path.join(os.path.dirname(AUTH_FILE), "cert.pem"))
    key = os.environ.get(
        "KEY", os.path.join(os.path.dirname(AUTH_FILE), "key.pem"))
    if not os.path.isfile(cert) or not os.path.isfile(key):
        raise RuntimeError(
            "WTM TLS certificate and private key are required; "
            "refusing plaintext startup")
    ctx = validate_cert_chain(cert, key)
    return {
        "auth_tokens": auth_tokens,
        "bind_host": bind_host,
        "allowed_hosts": allowed_hosts,
        "ssl_context": ctx,
        "cert_fingerprint": pem_fingerprint(cert),
    }


def pem_fingerprint(cert):
    """SHA-256 fingerprint of the PEM certificate, colon-separated.

    Used for first-connection verification: the value printed at install
    time must match the value shown by the server; a mismatch indicates a
    different server (possible active man-in-the-middle).
    """
    with open(cert, "rb") as handle:
        digest = hashlib.sha256(handle.read()).hexdigest().upper()
    return ":".join(digest[i:i + 2] for i in range(0, 64, 2))


def validate_cert_chain(cert, key):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(cert, key)
    decode = getattr(ssl._ssl, "_test_decode_cert", None)
    if decode is None:
        # If this CPython/OpenSSL build cannot decode certificates, do not
        # run with an unvalidated chain: refuse to start.
        raise RuntimeError(
            "WTM TLS validation unavailable in this Python build; "
            "refusing insecure startup")
    try:
        leaf = decode(cert)
    except Exception as exc:
        raise RuntimeError(
            "WTM TLS certificate could not be parsed; "
            "refusing insecure startup") from exc
    required = ("notBefore", "notAfter")
    if not all(leaf.get(field) for field in required):
        raise RuntimeError(
            "WTM TLS certificate has no validity window; "
            "refusing insecure startup")
    now = time.time()
    try:
        not_before = calendar.timegm(
            time.strptime(leaf["notBefore"], _CERT_TIME_FORMAT))
        not_after = calendar.timegm(
            time.strptime(leaf["notAfter"], _CERT_TIME_FORMAT))
    except (ValueError, OverflowError) as exc:
        raise RuntimeError(
            "WTM TLS certificate has an unparseable validity window; "
            "refusing insecure startup") from exc
    if not (not_before <= now <= not_after):
        raise RuntimeError(
            "WTM TLS certificate is expired or not yet valid; "
            "refusing insecure startup")
    return ctx


def build_app(auth_tokens, allowed_hosts, cert_fingerprint=""):
    app = web.Application(
        middlewares=[security_middleware],
        client_max_size=4096,
    )
    app["auth_tokens"] = auth_tokens
    app["allowed_hosts"] = allowed_hosts
    app["cert_fingerprint"] = cert_fingerprint
    app["state"] = StreamState()
    app.router.add_get("/healthz", health_handler)
    app.router.add_get("/login", login_handler)
    app.router.add_post("/api/auth", api_auth_handler)
    app.router.add_post("/api/logout", api_logout_handler)
    app.router.add_get("/api/session", api_session_handler)
    app.router.add_get("/", index_handler)
    app.router.add_get("/panel", panel_handler)
    app.router.add_get("/ws", ws_handler)
    app.router.add_get("/manifest.json", manifest_handler)
    app.router.add_get("/icon.svg", icon_handler)
    app.router.add_get("/stats", stats_handler)
    app.router.add_get("/api/status", api_status_handler)
    app.router.add_get("/api/logs", api_logs_handler)
    app.router.add_post("/api/control", api_control_handler)
    app.router.add_post("/log", log_handler)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


def session_role(request):
    token = request.cookies.get(AUTH_COOKIE, "")
    if not token:
        return None
    for role, expected in request.app["auth_tokens"].items():
        if hmac.compare_digest(token, expected):
            return role
    return None


def request_hostname(request):
    try:
        return (urlsplit("//" + request.host).hostname or "").lower()
    except ValueError:
        return ""


def origin_is_same_site(request):
    origin = request.headers.get("Origin", "")
    return origin == f"https://{request.host}"


def is_loopback_peer(request):
    try:
        peer = ipaddress.ip_address(request.remote)
        return peer.is_loopback or (
            peer.version == 6 and peer.ipv4_mapped is not None
            and peer.ipv4_mapped.is_loopback
        )
    except (TypeError, ValueError):
        return False


@web.middleware
async def security_middleware(request, handler):
    allowed_hosts = request.app["allowed_hosts"]
    if request_hostname(request) not in allowed_hosts:
        raise web.HTTPBadRequest(text="Unrecognized host")
    if not request.secure:
        raise web.HTTPUpgradeRequired(text="HTTPS is required")
    if request.path == "/healthz":
        if request.method != "GET" or not is_loopback_peer(request):
            raise web.HTTPForbidden(text="Loopback health checks only")
    elif request.path == "/api/auth":
        if request.method != "POST" or not origin_is_same_site(request):
            raise web.HTTPForbidden(text="Same-origin HTTPS request required")
    else:
        public_paths = {"/login", "/manifest.json", "/icon.svg"}
        role = session_role(request)
        if request.path not in public_paths and role is None:
            if request.path in {"/", "/panel"}:
                raise web.HTTPFound("/login")
            raise web.HTTPUnauthorized(text="Authentication required")
        if request.path == "/api/control" and role != "admin":
            raise web.HTTPForbidden(text="Administrator authorization required")
        if request.path == "/ws" or request.method not in {"GET", "HEAD", "OPTIONS"}:
            if not origin_is_same_site(request):
                raise web.HTTPForbidden(text="Same-origin HTTPS request required")
    response = await handler(request)
    response.headers.setdefault("Cache-Control", "no-store")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self' 'unsafe-inline'; connect-src 'self' wss:; "
        "object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
    )
    response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    return response


# ---------------------------------------------------------------- H.264 utils
class AnnexBParser:
    """Splits an Annex-B byte stream into NAL units."""

    def __init__(self):
        self.buf = b""

    def feed(self, chunk):
        self.buf += chunk
        out = []
        while True:
            p = self.buf.find(b"\x00\x00\x01")
            if p == -1:
                break
            nal = self.buf[:p].rstrip(b"\x00")
            if nal:
                out.append(nal)
            self.buf = self.buf[p + 3:]
        return out


def nal_type(nal):
    return nal[0] & 0x1F if nal else -1


def build_avcc(sps, pps):
    codec = f"avc1.{sps[1]:02x}{sps[2]:02x}{sps[3]:02x}"
    avcc = bytes([1, sps[1], sps[2], sps[3], 0xFF, 0xE1])
    avcc += len(sps).to_bytes(2, "big") + sps
    avcc += bytes([1]) + len(pps).to_bytes(2, "big") + pps
    return codec, avcc


def avcc_frame(nals):
    out = bytearray()
    for n in nals:
        out += len(n).to_bytes(4, "big") + n
    return bytes(out)


# --------------------------------------------------------------------- state
class StreamState:
    def __init__(self):
        self.config = None
        self.clients = set()
        self.access_unit = []
        self.sps = None
        self.pps = None
        self.frames = 0
        self.bytes = 0
        self.need_key = set()
        self.cur_is_key = False
        self.transports = {}
        self.dropped = 0
        self.capd_stdin = None
        self.last_key_req = 0.0
        self.proc_us_sum = 0
        self.got_frame = False
        self.age_us_sum = 0
        self.flash_t = 0.0
        self.last_key_frame = None  # cached AVCC keyframe (scrcpy mode) for new clients
        self.sps_dirty = False
        self.scrcpy_w = W          # current video size in scrcpy mode (from SPS)
        self.scrcpy_h = H
        self.client_stats = {}     # latest stats reported by the tablet client (parsed from /log)
        self.client_stats_ts = 0.0
        self.scrcpy_connected = False  # True while the scrcpy-server video stream is up


async def broadcast_text(state, text):
    for ws in list(state.clients):
        try:
            await ws.send_str(text)
        except Exception:
            state.clients.discard(ws)


def request_key(state):
    """Ask the encoder for an IDR (rate limited)."""
    now = time.monotonic()
    if state.capd_stdin is not None and now - state.last_key_req > 0.2:
        state.last_key_req = now
        try:
            state.capd_stdin.write(b"K\n")
        except Exception:
            pass


async def broadcast_frame(state, frame, is_key, ts_us=None, proc_us=0):
    # wire format: u8 key | u64 capture time (us, server clock) | u32 encode time (us) | AVCC access unit
    if ts_us is None:
        ts_us = int(time.time() * 1e6)
    state.frames += 1
    state.proc_us_sum += proc_us
    now_us = int(time.time() * 1e6)
    state.age_us_sum += now_us - ts_us
    if state.flash_t and ts_us >= state.flash_t * 1e6:      # first frame captured after the test flash was injected
        print(f"[flash] inject->capture_start={(ts_us - state.flash_t * 1e6) / 1000:.1f} ms  capture->relay={(now_us - ts_us) / 1000:.1f} ms  "
              f"(enc reported {proc_us / 1000:.1f} ms, {len(frame)} B)", flush=True)
        state.flash_t = 0.0
        flash_flag = 2
    else:
        flash_flag = 0
    payload = (bytes([(1 if is_key else 0) | flash_flag]) + ts_us.to_bytes(8, "big") + proc_us.to_bytes(4, "big") + frame)
    state.bytes += len(payload)
    for ws in list(state.clients):
        try:
            if is_key:
                state.need_key.discard(ws)
            elif ws in state.need_key:
                continue
            else:
                tr = state.transports.get(ws)
                if tr is not None and tr.get_write_buffer_size() > 512 * 1024:
                    # Client/network is backed up: skip until the next IDR so the
                    # decoder never sees a broken reference chain.
                    state.need_key.add(ws)
                    state.dropped += 1
                    request_key(state)
                    continue
            await ws.send_bytes(payload)
        except Exception:
            state.clients.discard(ws)


async def maybe_send_config(state):
    if state.config is None and state.sps and state.pps:
        codec, avcc = build_avcc(state.sps, state.pps)
        state.config = {
            "codec": codec,
            "description": base64.b64encode(avcc).decode("ascii"),
            "width": W,
            "height": H,
            "fps": FPS,
        }
        await broadcast_text(state, json.dumps({"type": "config", **state.config}))


async def handle_nal(state, nal):
    t = nal_type(nal)
    if t == 7:
        state.sps = nal
        await maybe_send_config(state)
    elif t == 8:
        state.pps = nal
        await maybe_send_config(state)
    elif t == 9:  # AUD: previous access unit complete
        if state.access_unit and state.config:
            await broadcast_frame(state, avcc_frame(state.access_unit), state.cur_is_key)
        state.access_unit = []
        state.cur_is_key = False
    elif t in (1, 5):
        if t == 5:
            state.cur_is_key = True
        state.access_unit.append(nal)


def ffmpeg_cmd():
    vf = f"scale=out_color_matrix=bt709:out_range=tv,format=yuv420p"
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-fflags", "nobuffer", "-flags", "low_delay",
        "-f", "x11grab", "-video_size", f"{W}x{H}", "-framerate", str(FPS),
        "-draw_mouse", "0", "-i", DISPLAY,
        "-vf", vf,
        "-c:v", "libx264", "-preset", PRESET, "-tune", "zerolatency",
        "-profile:v", "high",
        "-g", str(GOP), "-keyint_min", str(GOP), "-sc_threshold", "0",
        "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
        "-color_range", "tv",
        "-x264-params",
        f"aud=1:keyint={GOP}:min-keyint={GOP}:scenecut=0:rc-lookahead=0:sync-lookahead=0:"
        f"threads=4:sliced-threads=1",
    ]
    if VIDEO_BITRATE != "0":
        cmd += ["-crf", CRF, "-maxrate", f"{VIDEO_BITRATE}k", "-bufsize", f"{int(VIDEO_BITRATE)}k"]
    else:
        cmd += ["-crf", CRF]
    cmd += ["-f", "h264", "pipe:1"]
    return cmd


async def ffmpeg_reader(state):
    while True:
        proc = await asyncio.create_subprocess_exec(
            *ffmpeg_cmd(), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        print(f"[stream] ffmpeg pid={proc.pid}", flush=True)
        parser = AnnexBParser()
        state.access_unit = []
        try:
            while True:
                chunk = await proc.stdout.read(262144)
                if not chunk:
                    break
                for nal in parser.feed(chunk):
                    await handle_nal(state, nal)
        except asyncio.CancelledError:
            if proc.returncode is None:
                proc.terminate()
                await proc.wait()
            raise
        if proc.returncode is None:
            proc.terminate()
        await proc.wait()
        print(f"[stream] ffmpeg exited rc={proc.returncode}; restart in 1s", flush=True)
        await asyncio.sleep(1)


CAPD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "capd")


async def capd_reader(state):
    """Run the damage-driven C encoder and relay its framed output."""
    env = dict(os.environ, DISPLAY=DISPLAY, FPS=str(FPS), CRF=CRF, PRESET=PRESET,
               THREADS=os.environ.get("THREADS", "3"), MAXRATE=os.environ.get("MAXRATE", "60000"),
               STATS=os.environ.get("CAPD_STATS", "0"))
    while True:
        proc = await asyncio.create_subprocess_exec(
            CAPD, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, env=env)
        state.capd_stdin = proc.stdin
        state.need_key.update(state.clients)
        print(f"[stream] capd pid={proc.pid}", flush=True)
        try:
            while True:
                hdr = await proc.stdout.readexactly(24)
                mtype, flags = hdr[0], hdr[1]
                ln = int.from_bytes(hdr[4:8], "little")
                ts_us = int.from_bytes(hdr[8:16], "little")
                proc_us = int.from_bytes(hdr[16:20], "little")
                payload = await proc.stdout.readexactly(ln) if ln else b""
                if mtype == 1:
                    sl = int.from_bytes(payload[0:2], "little")
                    sps = payload[2:2 + sl]
                    pl = int.from_bytes(payload[2 + sl:4 + sl], "little")
                    pps = payload[4 + sl:4 + sl + pl]
                    codec, avcc = build_avcc(sps, pps)
                    state.config = {"codec": codec, "description": base64.b64encode(avcc).decode("ascii"),
                                    "width": W, "height": H, "fps": FPS}
                    await broadcast_text(state, json.dumps({"type": "config", **state.config}))
                elif mtype == 2:
                    await broadcast_frame(state, payload, bool(flags & 1), ts_us, proc_us)
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        except asyncio.CancelledError:
            if proc.returncode is None:
                proc.terminate()
                await proc.wait()
            raise
        state.capd_stdin = None
        if proc.returncode is None:
            proc.terminate()
        await proc.wait()
        print(f"[stream] capd exited rc={proc.returncode}; restart in 1s", flush=True)
        await asyncio.sleep(1)


APP_PROCESS = "/system/bin/app_process"
HWJAR = os.environ.get("HWJAR", "/data/data/com.termux/files/home/rd/hwenc.jar")
HW_KBPS = os.environ.get("HW_KBPS", "12000")
HW_MODE = os.environ.get("HW_MODE", "2")          # 2 = CBR, 1 = VBR
SHM_NV12 = os.environ.get("SHM_NV12", "/tmp/rd_nv12.bin")


_START = re.compile(b"\x00\x00\x01")


def annexb_nals(buf):
    """Split an Annex-B buffer into NAL units (start codes removed, trailing zeros stripped)."""
    ends = [m.end() for m in _START.finditer(buf)]
    out = []
    for i, s in enumerate(ends):
        e = ends[i + 1] - 3 if i + 1 < len(ends) else len(buf)
        while e > s and buf[e - 1] == 0:
            e -= 1
        if e > s:
            out.append(buf[s:e])
    return out


async def relay(state, stdout):
    """Parse capd/HwEnc framed messages (24-byte header + payload) and broadcast them.
    flags bit0 = key frame, bit1 = payload is raw Annex-B (hardware encoder) instead of AVCC."""
    async def set_config(sps, pps):
        codec, avcc = build_avcc(sps, pps)
        cfg = {"codec": codec, "description": base64.b64encode(avcc).decode("ascii"),
               "width": W, "height": H, "fps": FPS}
        if cfg != state.config:
            state.config = cfg
            await broadcast_text(state, json.dumps({"type": "config", **cfg}))

    while True:
        hdr = await stdout.readexactly(24)
        mtype, flags = hdr[0], hdr[1]
        ln = int.from_bytes(hdr[4:8], "little")
        ts_us = int.from_bytes(hdr[8:16], "little")
        proc_us = int.from_bytes(hdr[16:20], "little")
        payload = await stdout.readexactly(ln) if ln else b""
        raw = bool(flags & 2)
        if mtype == 1:
            if raw:
                sps = pps = None
                for n in annexb_nals(payload):
                    t = n[0] & 0x1F
                    sps = n if t == 7 else sps
                    pps = n if t == 8 else pps
            else:
                sl = int.from_bytes(payload[0:2], "little")
                sps = payload[2:2 + sl]
                pl = int.from_bytes(payload[2 + sl:4 + sl], "little")
                pps = payload[4 + sl:4 + sl + pl]
            if sps and pps:
                await set_config(sps, pps)
        elif mtype == 2:
            if raw:
                parts, sps, pps = [], None, None
                for n in annexb_nals(payload):
                    t = n[0] & 0x1F
                    if t in (1, 5):
                        parts.append(len(n).to_bytes(4, "big") + n)
                    elif t == 7:
                        sps = n
                    elif t == 8:
                        pps = n
                if sps and pps and state.config is None:
                    await set_config(sps, pps)
                if not parts:
                    continue
                payload = b"".join(parts)
            state.got_frame = True
            await broadcast_frame(state, payload, bool(flags & 1), ts_us, proc_us)


async def hw_reader(state):
    """Capture with capd (shared NV12 frame) and encode with the Android hardware H.264 encoder (HwEnc)."""
    r, w = os.pipe()
    cenv = dict(os.environ, DISPLAY=DISPLAY, FPS=str(FPS), HWMODE="1", SHM_NV12=SHM_NV12,
                TAIL=os.environ.get("TAIL", "3"), REFINE=os.environ.get("REFINE", "0"),
                STATS=os.environ.get("CAPD_STATS", "0"))
    henv = {k: v for k, v in os.environ.items() if k not in ("LD_LIBRARY_PATH", "LD_PRELOAD")}
    henv["CLASSPATH"] = HWJAR
    capd = await asyncio.create_subprocess_exec(CAPD, stdin=asyncio.subprocess.PIPE, stdout=w, env=cenv)
    os.close(w)
    hw = await asyncio.create_subprocess_exec(
        APP_PROCESS, "-Xnoimage-dex2oat", "/", "--nice-name=hwenc", "HwEnc",
        str(W), str(H), str(FPS), HW_KBPS, SHM_NV12, HW_MODE, "",
        stdin=r, stdout=asyncio.subprocess.PIPE, env=henv)
    os.close(r)
    state.capd_stdin = capd.stdin
    state.need_key.update(state.clients)
    print(f"[stream] HW pipeline: capd pid={capd.pid} + HwEnc pid={hw.pid} ({HW_KBPS} kbps, mode {HW_MODE})", flush=True)
    try:
        await relay(state, hw.stdout)
    except (asyncio.IncompleteReadError, ConnectionError):
        pass
    finally:
        state.capd_stdin = None
        for p in (hw, capd):
            if p.returncode is None:
                p.terminate()
        for p in (hw, capd):
            try:
                await asyncio.wait_for(p.wait(), 3)
            except asyncio.TimeoutError:
                p.kill()
        print(f"[stream] HW pipeline ended (HwEnc rc={hw.returncode}, capd rc={capd.returncode})", flush=True)


async def key_watchdog(state):
    """A client waiting for a keyframe (new connection, decoder switch, drop) keeps asking until it gets one."""
    while True:
        await asyncio.sleep(0.3)
        if state.need_key and state.clients and state.capd_stdin is not None:
            request_key(state)


SCRCPY_HOST = os.environ.get("SCRCPY_HOST", "127.0.0.1")
SCRCPY_PORT = int(os.environ.get("SCRCPY_PORT", "1234"))


class AnnexBStream:
    """Streaming Annex-B parser: splits H.264 NAL units across arbitrary chunk boundaries."""

    def __init__(self):
        self.buf = b""

    def feed(self, chunk):
        self.buf += chunk
        out = []
        while True:
            # locate next start code
            p3 = self.buf.find(b"\x00\x00\x01")
            if p3 == -1:
                break
            # a 4-byte start code (00 00 00 01) looks like a 3-byte code whose
            # preceding byte is also 0x00
            if p3 >= 1 and self.buf[p3 - 1] == 0:
                nal = self.buf[:p3 - 1]
                next_start = p3 + 3
            else:
                nal = self.buf[:p3]
                next_start = p3 + 3
            nal = nal.rstrip(b"\x00")
            if nal:
                out.append(nal)
            self.buf = self.buf[next_start:]
        return out


def sps_dimensions(sps):
    """Parse width/height from an H.264 SPS NAL (returns (w, h) or None)."""
    def unescape(b):
        out = bytearray()
        i = 0
        while i < len(b):
            if i + 2 < len(b) and b[i] == 0 and b[i + 1] == 0 and b[i + 2] == 3:
                out += b[i:i + 2]
                i += 3
            else:
                out.append(b[i])
                i += 1
        return bytes(out)

    try:
        rbsp = unescape(sps[1:])
        bits = "".join(f"{x:08b}" for x in rbsp)
        pos = 0

        def u(n):
            nonlocal pos
            v = int(bits[pos:pos + n], 2)
            pos += n
            return v

        def ue():
            nonlocal pos
            z = 0
            while pos < len(bits) and bits[pos] == "0":
                z += 1
                pos += 1
            pos += 1
            v = (1 << z) - 1 + (int(bits[pos:pos + z], 2) if z else 0)
            pos += z
            return v

        profile = u(8)
        u(8)  # constraint flags
        u(8)  # level
        ue()  # seq_parameter_set_id
        if profile in (100, 110, 122, 244, 44, 83, 86, 118, 128, 138, 139, 134, 135):
            chroma = ue()
            if chroma == 3:
                u(1)
            ue()  # bit_depth_luma_minus8
            ue()  # bit_depth_chroma_minus8
            u(1)  # qpprime_y_zero_transform_bypass
            if u(1):  # seq_scaling_matrix_present_flag
                for _ in range(8 if chroma != 3 else 12):
                    if u(1):
                        u(64 if _ < 6 else 16)
        ue()  # log2_max_frame_num_minus4
        poc = ue()
        if poc == 0:
            ue()
        elif poc == 1:
            u(1)
            ue()
            ue()
            for _ in range(ue()):
                ue()
        ue()  # max_num_ref_frames
        u(1)  # gaps_in_frame_num_value_allowed_flag
        width = (ue() + 1) * 16  # pic_width_in_mbs_minus1
        height = (ue() + 1) * 16  # pic_height_in_map_units_minus1
        frame_mbs_only = u(1)
        if not frame_mbs_only:
            u(1)
            height *= 2
        u(1)  # direct_8x8_inference_flag
        if u(1):  # frame_cropping_flag
            crop_l = ue()
            crop_r = ue()
            crop_t = ue()
            crop_b = ue()
            width -= (crop_l + crop_r) * 2
            height -= (crop_t + crop_b) * (2 if frame_mbs_only else 4)
        return max(1, width), max(1, height)
    except Exception:
        return None


async def scrcpy_reader(state):
    """Read a raw Annex-B H.264 stream from scrcpy-server (the whole S25 screen) and
    republish it as AVCC access units over WebSocket to the tablet client."""
    parser = AnnexBStream()
    n_frames = 0
    n_keys = 0
    t_stats = time.monotonic()
    while True:
        try:
            reader, writer = await asyncio.open_connection(SCRCPY_HOST, SCRCPY_PORT)
        except Exception as e:
            print(f"[scrcpy] connect {SCRCPY_HOST}:{SCRCPY_PORT} failed: {e}; retrying in 1s", flush=True)
            await asyncio.sleep(1)
            continue
        print(f"[scrcpy] connected to {SCRCPY_HOST}:{SCRCPY_PORT}", flush=True)
        state.need_key.update(state.clients)
        state.scrcpy_connected = True
        # tunnel_forward=true: the server accepts video first, then control, and only
        # then starts streaming. Connect the control socket right after the video
        # connection (small delay so adb queues the video LocalSocket first) so the
        # server can proceed past its accept() for the control channel.
        await asyncio.sleep(0.3)
        await scrcpy_control.connect()
        try:
            while True:
                chunk = await reader.read(262144)
                if not chunk:
                    break
                for nal in parser.feed(chunk):
                    t = nal[0] & 0x1F
                    if t == 7:  # SPS
                        if state.sps != nal:
                            state.sps = nal
                            state.sps_dirty = True
                            print(f"[scrcpy] SPS nuevo: {len(nal)}B hash={hash(nal) & 0xffff}", flush=True)
                    elif t == 8:  # PPS
                        if state.pps != nal:
                            state.pps = nal
                            state.sps_dirty = True
                        # scrcpy reinicia su encoder al cambiar de app/rotar: emite SPS/PPS
                        # nuevos. Hay que reenviar la config para que el cliente reconfigure su
                        # decodificador; si no, el siguiente IDR referencia SPS desconocido y
                        # el decodificador falla (freeze que solo se arregla recargando).
                        if state.sps_dirty and state.sps and state.pps:
                            state.sps_dirty = False
                            codec, avcc = build_avcc(state.sps, state.pps)
                            dims = sps_dimensions(state.sps)
                            W2, H2 = dims if dims else (W, H)
                            state.scrcpy_w, state.scrcpy_h = W2, H2
                            cfg = {"codec": codec, "description": base64.b64encode(avcc).decode("ascii"),
                                   "width": W2, "height": H2, "fps": FPS}
                            changed = cfg != state.config
                            state.config = cfg
                            state.last_key_frame = None  # el keyframe cacheado ya no vale (SPS nuevo)
                            state.need_key.update(state.clients)
                            if changed:
                                print(f"[scrcpy] encoder reset: nueva config {W2}x{H2} {codec}", flush=True)
                                await broadcast_text(state, json.dumps({"type": "config", **cfg}))
                    elif t in (1, 5):  # slice: one slice = one frame (verified)
                        if state.config is None:
                            continue
                        is_key = t == 5
                        avcc = len(nal).to_bytes(4, "big") + nal
                        if is_key:
                            state.last_key_frame = avcc
                            n_keys += 1
                        n_frames += 1
                        state.got_frame = True
                        await broadcast_frame(state, avcc, is_key,
                                              ts_us=int(time.time() * 1e6), proc_us=0)
                        if time.monotonic() - t_stats >= 5:
                            print(f"[scrcpy] {n_frames/(time.monotonic()-t_stats):.1f} fps, "
                                  f"{n_keys} IDR en {time.monotonic()-t_stats:.1f}s "
                                  f"(intervalo IDR ~{ (time.monotonic()-t_stats)/max(1,n_keys):.1f}s)", flush=True)
                            n_frames = 0; n_keys = 0; t_stats = time.monotonic()
        except (asyncio.IncompleteReadError, ConnectionError, OSError):
            pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            scrcpy_control.close()
            state.scrcpy_connected = False
        print("[scrcpy] connection lost; reconnecting in 1s", flush=True)
        await asyncio.sleep(1)


async def supervisor(state):
    """Prefer the hardware encoder; fall back to x264 if it cannot start or keeps failing.
    BACKEND=scrcpy reads the whole Android screen from scrcpy-server instead of Xvfb."""
    mode = os.environ.get("BACKEND", "auto")
    if mode == "scrcpy":
        await scrcpy_reader(state)
        return
    hw_ok = mode in ("auto", "hw") and os.path.exists(APP_PROCESS) and os.path.exists(HWJAR)
    fails = 0
    while True:
        if hw_ok:
            t0 = time.monotonic()
            state.got_frame = False
            try:
                await hw_reader(state)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                print(f"[stream] HW error: {e}", flush=True)
            if not state.got_frame and time.monotonic() - t0 < 15:
                fails += 1
            else:
                fails = 0
            if fails >= 2 and mode == "auto":
                print("[stream] hardware encoder unavailable -> falling back to x264", flush=True)
                hw_ok = False
            await asyncio.sleep(1)
        else:
            try:
                await capd_reader(state)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                print(f"[stream] capd unavailable or failed: {e}; retry in 1s", flush=True)
                await asyncio.sleep(1)


# ------------------------------------------------ scrcpy control protocol
# The scrcpy control channel carries binary injection messages (touch, keycode,
# text, scroll). Formats are the scrcpy v3/v5 protocol (big-endian, DataInputStream).
CTRL_TYPE_INJECT_KEYCODE = 0
CTRL_TYPE_INJECT_TEXT = 1
CTRL_TYPE_INJECT_TOUCH = 2
CTRL_TYPE_INJECT_SCROLL = 3

ACTION_DOWN = 0
ACTION_UP = 1
MOTION_ACTION_MOVE = 2

POINTER_ID_GENERIC_FINGER = -2   # scrcpy SC_POINTER_ID_GENERIC_FINGER (finger touch)
POINTER_ID_MOUSE = -1            # scrcpy SC_POINTER_ID_MOUSE (cursor/hover)
ACTION_HOVER_MOVE = 7            # Android MotionEvent.ACTION_HOVER_MOVE
BTN_PRIMARY = 1
BTN_SECONDARY = 2
BTN_TERTIARY = 4
X_BTN_TO_ANDROID = {1: BTN_PRIMARY, 2: BTN_TERTIARY, 3: BTN_SECONDARY}  # X11 button -> Android

# Android KeyEvent keycodes (AKEYCODE_*) for the key names the client sends.
ANDROID_KEYCODES = {
    "space": 62, "Return": 66, "BackSpace": 67, "Tab": 61, "Escape": 111,
    "Up": 19, "Down": 20, "Left": 21, "Right": 22,
    "Delete": 112, "Home": 122, "End": 123, "Prior": 92, "Next": 93,
    "shift": 59, "ctrl": 113, "alt": 57, "super": 117,
    "CapsLock": 115, "Insert": 124,
    **{f"F{i}": 131 + (i - 1) for i in range(1, 13)},
}


def android_keycode(name):
    if name in ANDROID_KEYCODES:
        return ANDROID_KEYCODES[name]
    if len(name) == 1:
        c = name.lower()
        if "a" <= c <= "z":
            return 29 + (ord(c) - ord("a"))
        if "0" <= c <= "9":
            return 7 + (ord(c) - ord("0"))
    return None


def u16fp(pressure):
    """Encode a 0..1 float as scrcpy's unsigned 16-bit fixed point (0xffff = 1.0)."""
    if pressure >= 1.0:
        return 0xFFFF
    if pressure <= 0.0:
        return 0
    return int(pressure * 65536)


def ctrl_touch(action, x, y, w, h, pressure, pointer_id=POINTER_ID_GENERIC_FINGER, action_button=0, buttons=0):
    return struct.pack(">BBqiiHHHii", CTRL_TYPE_INJECT_TOUCH, action, pointer_id,
                       int(x), int(y), int(w), int(h), u16fp(pressure), action_button, buttons)


def ctrl_keycode(action, keycode, repeat=0, metastate=0):
    return struct.pack(">BBiii", CTRL_TYPE_INJECT_KEYCODE, action, int(keycode), repeat, metastate)


def ctrl_text(text):
    b = text.encode("utf-8")[:300]
    return struct.pack(">BI", CTRL_TYPE_INJECT_TEXT, len(b)) + b


def _i16fp(v):
    """Encode a scroll value (range [-16, 16]) as scrcpy signed 16-bit fixed point."""
    v = max(-16.0, min(16.0, v))
    return max(-32768, min(32767, int(round(v / 16.0 * 32768))))


def ctrl_scroll(x, y, w, h, vscroll, hscroll=0.0):
    return struct.pack(">BiiHHhhi", CTRL_TYPE_INJECT_SCROLL, int(x), int(y), int(w), int(h),
                       _i16fp(hscroll), _i16fp(vscroll), 0)


class ScrcpyControl:
    """Writer for the scrcpy control channel (the 2nd connection to the adb tunnel).

    The scrcpy-server with tunnel_forward=true accepts the video socket first, then
    the control socket. So this MUST connect only after the video stream is up.
    """

    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.writer = None
        self.queue = asyncio.Queue()
        self._task = None

    async def connect(self):
        try:
            _, writer = await asyncio.open_connection(self.host, self.port)
        except Exception as e:
            print(f"[scrcpy] control connect failed: {e}", flush=True)
            self.writer = None
            return False
        # cancel any stale drain task from a previous connection
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self.writer = writer
        self._task = asyncio.create_task(self._drain(writer))
        print("[scrcpy] control channel connected", flush=True)
        return True

    async def _drain(self, writer):
        try:
            while True:
                data = await self.queue.get()
                if data is None:
                    break
                writer.write(data)
                await writer.drain()
        except (ConnectionError, OSError, asyncio.CancelledError):
            pass
        finally:
            if self.writer is writer:
                self.writer = None
            try:
                writer.close()
            except Exception:
                pass
            print("[scrcpy] control channel lost", flush=True)

    def send(self, data):
        if self.writer is not None:
            self.queue.put_nowait(data)

    def close(self):
        if self._task is not None and not self._task.done():
            self._task.cancel()
        if self.writer is not None:
            try:
                self.writer.close()
            except Exception:
                pass
        self.writer = None


# --------------------------------------------------------------------- input
KEYNAME_MAP = {
    "space": "space", "Return": "Return", "BackSpace": "BackSpace", "Tab": "Tab",
    "Escape": "Escape", "Up": "Up", "Down": "Down", "Left": "Left", "Right": "Right",
    "Delete": "Delete", "Home": "Home", "End": "End", "Prior": "Prior", "Next": "Next",
    "shift": "Shift_L", "ctrl": "Control_L", "alt": "Alt_L", "super": "Super_L",
    "CapsLock": "Caps_Lock", "Insert": "Insert",
    **{f"F{i}": f"F{i}" for i in range(1, 13)},
}


class InputInjector:
    def __init__(self, disp):
        self.d = self.root = None
        if HAS_XLIB:
            try:
                self.d = xdisplay.Display(disp)
                self.root = self.d.screen().root
            except Exception as e:
                print(f"[input] XTEST init failed: {e}", flush=True)

    def move(self, x, y):
        if self.d:
            xtest.fake_input(self.d, X.MotionNotify, detail=0, root=self.root, x=int(x), y=int(y))
            self.d.flush()

    def button(self, b, down):
        if self.d:
            xtest.fake_input(self.d, X.ButtonPress if down else X.ButtonRelease, detail=int(b))
            self.d.flush()

    def wheel(self, d, n=1):
        if self.d:
            btn = 4 if d > 0 else 5
            for _ in range(max(1, n)):
                xtest.fake_input(self.d, X.ButtonPress, detail=btn)
                xtest.fake_input(self.d, X.ButtonRelease, detail=btn)
            self.d.flush()

    def keycode(self, name):
        xname = KEYNAME_MAP.get(name, name)
        ks = XK.string_to_keysym(xname)
        if ks == 0 and len(name) == 1:
            ks = ord(name)  # unicode keysym fallback (latin-1 range maps 1:1)
        return self.d.keysym_to_keycode(ks) if (self.d and ks) else 0

    def flash(self, color):
        """Test hook: recolor a small always-on-top window (used to measure end-to-end latency)."""
        if not self.d:
            return
        if getattr(self, "fw", None) is None:
            scr = self.d.screen()
            self.fw = self.root.create_window(W - 48, H - 48, 48, 48, 0, scr.root_depth, X.InputOutput,
                                              X.CopyFromParent, override_redirect=True, background_pixel=0)
            self.fw.map()
        self.fw.change_attributes(background_pixel=int(color))
        self.fw.clear_area()
        self.d.flush()

    def type_char(self, ch):
        if not self.d:
            return
        ks = XK.string_to_keysym(ch) or (ord(ch) if ord(ch) < 256 else 0)
        kc = self.d.keysym_to_keycode(ks) if ks else 0
        if not kc:
            return
        shift = self.d.keysym_to_keycode(XK.string_to_keysym("Shift_L"))
        need_shift = self.d.keycode_to_keysym(kc, 0) != ks and self.d.keycode_to_keysym(kc, 1) == ks
        if need_shift:
            xtest.fake_input(self.d, X.KeyPress, detail=shift)
        xtest.fake_input(self.d, X.KeyPress, detail=kc)
        xtest.fake_input(self.d, X.KeyRelease, detail=kc)
        if need_shift:
            xtest.fake_input(self.d, X.KeyRelease, detail=shift)
        self.d.flush()

    def key(self, name, down):
        kc = self.keycode(name)
        if not kc:
            return False
        xtest.fake_input(self.d, X.KeyPress if down else X.KeyRelease, detail=kc)
        self.d.flush()
        return True


injector = None
scrcpy_control = ScrcpyControl(SCRCPY_HOST, SCRCPY_PORT)
SCRCPY_MODE = os.environ.get("BACKEND", "auto") == "scrcpy"

# Last known pointer position and finger state for scrcpy touch injection
# (the client sends "move" with x/y, then "down"/"up" without coordinates).
_scrcpy_x = 0
_scrcpy_y = 0
_scrcpy_finger_down = False
_scrcpy_mouse_buttons = 0


async def inject_scrcpy_input(state, data):
    global _scrcpy_x, _scrcpy_y, _scrcpy_finger_down, _scrcpy_mouse_buttons
    t = data.get("t")
    w, h = state.scrcpy_w, state.scrcpy_h
    pt = data.get("pt", "touch")
    try:
        if t == "move":
            _scrcpy_x, _scrcpy_y = int(data["x"]), int(data["y"])
            if pt == "mouse":
                if _scrcpy_mouse_buttons:
                    # dragging with a button held
                    scrcpy_control.send(ctrl_touch(MOTION_ACTION_MOVE, _scrcpy_x, _scrcpy_y, w, h, 1.0,
                                                   POINTER_ID_MOUSE, 0, _scrcpy_mouse_buttons))
                else:
                    # hover: move the cursor (DeX shows a pointer)
                    scrcpy_control.send(ctrl_touch(ACTION_HOVER_MOVE, _scrcpy_x, _scrcpy_y, w, h, 1.0, POINTER_ID_MOUSE))
            elif _scrcpy_finger_down:
                scrcpy_control.send(ctrl_touch(MOTION_ACTION_MOVE, _scrcpy_x, _scrcpy_y, w, h, 1.0))
        elif t == "down":
            if pt == "mouse":
                ab = X_BTN_TO_ANDROID.get(int(data.get("b", 1)), BTN_PRIMARY)
                _scrcpy_mouse_buttons |= ab
                scrcpy_control.send(ctrl_touch(ACTION_DOWN, _scrcpy_x, _scrcpy_y, w, h, 1.0,
                                               POINTER_ID_MOUSE, ab, _scrcpy_mouse_buttons))
            else:
                _scrcpy_finger_down = True
                scrcpy_control.send(ctrl_touch(ACTION_DOWN, _scrcpy_x, _scrcpy_y, w, h, 1.0))
        elif t == "up":
            if pt == "mouse":
                ab = X_BTN_TO_ANDROID.get(int(data.get("b", 1)), BTN_PRIMARY)
                _scrcpy_mouse_buttons &= ~ab
                scrcpy_control.send(ctrl_touch(ACTION_UP, _scrcpy_x, _scrcpy_y, w, h, 0.0,
                                               POINTER_ID_MOUSE, ab, _scrcpy_mouse_buttons))
            else:
                _scrcpy_finger_down = False
                scrcpy_control.send(ctrl_touch(ACTION_UP, _scrcpy_x, _scrcpy_y, w, h, 0.0))
        elif t == "wheel":
            d = int(data.get("d", 1))
            n = int(data.get("n", 1))
            # One notch == 1.0 AXIS_VSCROLL unit (matches scrcpy's preciseY semantics).
            scrcpy_control.send(ctrl_scroll(_scrcpy_x, _scrcpy_y, w, h, d * n))
        elif t == "keydown":
            kc = android_keycode(data.get("k", ""))
            if kc is not None:
                scrcpy_control.send(ctrl_keycode(ACTION_DOWN, kc))
        elif t == "keyup":
            kc = android_keycode(data.get("k", ""))
            if kc is not None:
                scrcpy_control.send(ctrl_keycode(ACTION_UP, kc))
        elif t == "key":
            kc = android_keycode(data.get("k", ""))
            if kc is not None:
                scrcpy_control.send(ctrl_keycode(ACTION_DOWN, kc))
                scrcpy_control.send(ctrl_keycode(ACTION_UP, kc))
        elif t == "type":
            scrcpy_control.send(ctrl_text(data.get("s", "")))
    except Exception as e:
        print(f"[scrcpy-input] error: {e}", flush=True)


async def inject_input(state, data):
    if SCRCPY_MODE:
        await inject_scrcpy_input(state, data)
        return
    t = data.get("t")
    try:
        if t == "move":
            injector.move(data["x"], data["y"])
        elif t == "down":
            injector.button(data.get("b", 1), True)
        elif t == "up":
            injector.button(data.get("b", 1), False)
        elif t == "wheel":
            injector.wheel(int(data.get("d", 1)), int(data.get("n", 1)))
        elif t == "keydown":
            injector.key(data.get("k", ""), True)
        elif t == "keyup":
            injector.key(data.get("k", ""), False)
        elif t == "key":
            k = data.get("k", "")
            injector.key(k, True)
            injector.key(k, False)
        elif t == "type":
            for ch in data.get("s", ""):
                injector.type_char(ch)
    except Exception as e:
        print(f"[input] error: {e}", flush=True)


# ---------------------------------------------------------------- http / ws
async def login_handler(request):
    next_path = request.query.get("next", "/")
    if next_path not in {"/", "/panel"}:
        next_path = "/"
    destination = json.dumps(next_path)
    fingerprint = request.app.get("cert_fingerprint", "")
    fingerprint_html = (
        f"<p>Huella SHA-256 del certificado: <code>{fingerprint}</code></p>"
        if fingerprint else ""
    )
    return web.Response(
        text=f"""<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>WTM - Iniciar sesión</title>
<style>body{{font:16px sans-serif;max-width:28rem;margin:15vh auto;padding:1rem;background:#111;color:#eee}}
input,button{{font:inherit;padding:.7rem;width:100%;margin:.5rem 0}}#error{{color:#f88}}</style>
<h1>WTM</h1><p>Introduce el token de acceso local creado durante la instalación.</p>
{fingerprint_html}
<p>Verifica que la huella coincide con la mostrada al instalar. Si no coincide,
no introduzcas ningún token: alguien podría estar interceptando la conexión.</p>
<form id="login"><label for="token">Access token</label>
<input id="token" type="password" autocomplete="current-password" required>
<button>Iniciar sesión</button><p id="error" role="alert"></p></form>
<script>
document.getElementById("login").addEventListener("submit", async (event) => {{
  event.preventDefault();
  const error = document.getElementById("error");
  error.textContent = "";
  try {{
    const response = await fetch("/api/auth", {{method:"POST",
      headers:{{"Content-Type":"application/json"}},
      body:JSON.stringify({{token:document.getElementById("token").value}})}});
    if (!response.ok) throw new Error("Acceso denegado. Comprueba el token e inténtalo de nuevo.");
    location.replace({destination});
  }} catch (e) {{ error.textContent = e.message; }}
}});
</script></html>""",
        content_type="text/html",
    )


async def api_auth_handler(request):
    now = time.monotonic()
    peer = request.remote or "unknown"
    for address, (_, started_at) in list(_auth_attempts.items()):
        if now - started_at >= AUTH_WINDOW_SECONDS:
            _auth_attempts.pop(address, None)
    attempts, started = _auth_attempts.get(peer, (0, now))
    if now - started >= AUTH_WINDOW_SECONDS:
        attempts, started = 0, now
    if attempts >= AUTH_MAX_ATTEMPTS:
        raise web.HTTPTooManyRequests(text="Too many sign-in attempts")
    if request.content_type != "application/json":
        raise web.HTTPUnsupportedMediaType(text="JSON request body required")
    try:
        data = await request.json()
    except (ValueError, json.JSONDecodeError) as exc:
        raise web.HTTPBadRequest(text="Invalid JSON request body") from exc
    token = data.get("token", "") if isinstance(data, dict) else ""
    role = None
    if isinstance(token, str):
        for candidate, expected in request.app["auth_tokens"].items():
            if hmac.compare_digest(token, expected):
                role = candidate
    if role is None:
        _auth_attempts[peer] = (attempts + 1, started)
        raise web.HTTPUnauthorized(text="Invalid access token")
    _auth_attempts.pop(peer, None)
    response = web.json_response({"role": role})
    response.set_cookie(
        AUTH_COOKIE,
        token,
        max_age=12 * 60 * 60,
        secure=True,
        httponly=True,
        samesite="Strict",
        path="/",
    )
    return response


async def api_logout_handler(_request):
    response = web.json_response({"result": "signed out"})
    response.del_cookie(AUTH_COOKIE, path="/", secure=True, httponly=True, samesite="Strict")
    return response


async def api_session_handler(request):
    return web.json_response({"role": session_role(request)})


async def index_handler(request):
    return web.FileResponse(os.path.join(HERE, "web", "index.html"),
                            headers={"Cache-Control": "no-store"})


async def panel_handler(request):
    return web.FileResponse(os.path.join(HERE, "web", "panel.html"),
                            headers={"Cache-Control": "no-store"})


async def manifest_handler(request):
    return web.json_response({
        "name": "Remote Display", "short_name": "Display", "start_url": "/",
        "display": "fullscreen", "orientation": "landscape",
        "background_color": "#000000", "theme_color": "#000000",
        "icons": [{"src": "/icon.svg", "sizes": "any", "type": "image/svg+xml"}],
    })


async def icon_handler(request):
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
           '<rect width="100" height="100" rx="22" fill="#0a84ff"/>'
           '<rect x="18" y="26" width="64" height="40" rx="5" fill="#fff"/>'
           '<rect x="40" y="70" width="20" height="5" fill="#fff"/></svg>')
    return web.Response(text=svg, content_type="image/svg+xml")


CLIENT_STATS_RE = re.compile(
    r"in=([\d.]+) fps=([\d.]+) mbps=([\d.]+) enc_ms=([\d.]+) net_ms=([\d.]+)\(max (\d+)\) "
    r"decode_ms=([\d.]+)\(max (\d+)\) TOTAL_ms=([\d.]+)\(max (\d+)\) dropped=(\d+) rtt=([\d.]+)")


async def log_handler(request):
    text = re.sub(r"[\x00-\x1f\x7f]", "", (await request.text())[:400])
    print("[client] " + text, flush=True)
    m = CLIENT_STATS_RE.search(text)
    if m:
        s = request.app["state"]
        s.client_stats = {
            "in_fps": float(m.group(1)), "fps": float(m.group(2)), "mbps": float(m.group(3)),
            "enc_ms": float(m.group(4)), "net_ms": float(m.group(5)), "net_ms_max": int(m.group(6)),
            "decode_ms": float(m.group(7)), "decode_ms_max": int(m.group(8)),
            "total_ms": float(m.group(9)), "total_ms_max": int(m.group(10)),
            "dropped": int(m.group(11)), "rtt": float(m.group(12)),
        }
        s.client_stats_ts = time.monotonic()
    return web.Response(text="ok")


def server_fps(s):
    """Server-side encode fps over the last few seconds (simple EWMA of frame rate)."""
    now = time.monotonic()
    prev = getattr(s, "_fps_t", None)
    prev_frames = getattr(s, "_fps_frames", 0)
    if prev is None:
        s._fps_t = now
        s._fps_frames = s.frames
        return 0.0
    dt = now - prev
    if dt < 0.5:
        return getattr(s, "_fps_val", 0.0)
    fps = (s.frames - prev_frames) / dt
    s._fps_t = now
    s._fps_frames = s.frames
    s._fps_val = getattr(s, "_fps_val", fps) * 0.7 + fps * 0.3
    return s._fps_val


_device_info_cache = {"data": {}, "ts": 0.0}


def _device_info_sync():
    """Best-effort device info (battery, WiFi RSSI, hotspot mode, RAM, CPU, temp) via adb.
    Runs in a worker thread (NEVER blocks the event loop)."""
    import subprocess
    info = {"battery": None, "wifi_rssi": None, "mode": None, "hotspot": False, "ip": None,
            "ram_used_pct": None, "load": None, "temp_c": None}
    try:
        out = subprocess.run(
            ["/data/data/com.termux/files/usr/bin/adb", "-s", "127.0.0.1:5555", "shell",
             "dumpsys battery 2>/dev/null | grep 'level:'; "
             "dumpsys wifi 2>/dev/null | grep -c 'num SoftApManagers:1'; "
             "ip -4 addr show wlan0 2>/dev/null | grep -oE 'inet [0-9.]+'; "
             "ip -4 addr show swlan0 2>/dev/null | grep -oE 'inet [0-9.]+'; "
             "dumpsys wifi 2>/dev/null | grep -oE 'RSSI: [-0-9]+' | head -1; "
             "grep -E 'MemTotal|MemAvailable' /proc/meminfo 2>/dev/null; "
             "cat /proc/loadavg 2>/dev/null; "
             "grep -hoE '[0-9]+' /sys/class/thermal/thermal_zone*/temp 2>/dev/null"],
            capture_output=True, text=True, timeout=4)
        lines = [l.strip() for l in out.stdout.splitlines() if l.strip()]
        mem_total = mem_avail = None
        temps = []
        for l in lines:
            if l.startswith("level:"):
                try:
                    info["battery"] = int(l.split("level:")[1].strip())
                except Exception:
                    pass
            elif l.startswith("inet "):
                info["ip"] = l.split()[1]
            elif l.startswith("RSSI:"):
                m = re.search(r"RSSI: (-?\d+)", l)
                info["wifi_rssi"] = int(m.group(1)) if m else None
            elif l.startswith("MemTotal:"):
                mem_total = int(re.sub(r"\D", "", l.split(":")[1]) or 0)
            elif l.startswith("MemAvailable:"):
                mem_avail = int(re.sub(r"\D", "", l.split(":")[1]) or 0)
            elif re.match(r"^\d+\.\d+\s+\d+\.\d+\s+\d+\.\d+", l):
                info["load"] = float(l.split()[0])
            elif l.isdigit() and len(l) == 1:
                info["hotspot"] = int(l) > 0
            elif l.isdigit() and len(l) >= 3:
                temps.append(int(l))
        if mem_total and mem_avail is not None:
            info["ram_used_pct"] = round((mem_total - mem_avail) / mem_total * 100)
        if temps:
            # normalizar: Samsung reporta miligrados (>1000) o decigrados (>100)
            vals = [t / 1000 if t > 1000 else t / 10 if t > 150 else t for t in temps]
            # descartar valores sin sentido (sensores reservados, p.ej. 273000 m°C)
            vals = [v for v in vals if 20 <= v <= 100]
            info["temp_c"] = round(max(vals), 1) if vals else None
        info["mode"] = "hotspot" if info["hotspot"] else "wifi"
    except Exception:
        pass
    return info


async def device_info():
    """Cached (5s TTL) async device info: runs the subprocess in a thread so it never
    stalls the WebSocket frame loop (this was the cause of high latency/lag)."""
    now = time.monotonic()
    if _device_info_cache["data"] and now - _device_info_cache["ts"] < 5:
        return _device_info_cache["data"]
    loop = asyncio.get_running_loop()
    info = await loop.run_in_executor(None, _device_info_sync)
    if info.get("battery") is not None or info.get("ip"):
        _device_info_cache["data"] = info
        _device_info_cache["ts"] = now
    return info


async def stats_handler(request):
    s = request.app["state"]
    return web.json_response({"frames": s.frames, "bytes": s.bytes, "dropped": s.dropped,
                              "enc_ms_avg": (s.proc_us_sum / s.frames / 1000) if s.frames else 0,
                              "age_ms_avg": (s.age_us_sum / s.frames / 1000) if s.frames else 0,
                              "clients": len(s.clients), "config": s.config})


async def health_handler(request):
    state = request.app["state"]
    return web.json_response({
        "ok": True,
        "scrcpy_alive": state.scrcpy_connected,
        "tls_fingerprint": request.app.get("cert_fingerprint", ""),
    })


async def api_logs_handler(request):
    parts = []
    for f in (f"{TMPDIR}/rd-bridge.log", f"{TMPDIR}/rd-watchdog.log"):
        try:
            with open(f, "rb") as fh:
                fh.seek(0, 2)
                size = fh.tell()
                fh.seek(max(0, size - 6000))
                parts.append(f"### {f}\n" + fh.read().decode("utf-8", "replace").lstrip())
        except Exception:
            pass
    return web.Response(text="\n".join(parts) or "(sin logs)")


async def api_control_handler(request):
    if request.content_type != "application/json":
        raise web.HTTPUnsupportedMediaType(text="JSON request body required")
    try:
        data = await request.json()
    except (ValueError, json.JSONDecodeError) as exc:
        raise web.HTTPBadRequest(text="A JSON action is required") from exc
    if not isinstance(data, dict):
        raise web.HTTPBadRequest(text="A JSON object is required")
    action = data.get("action")
    if action not in {"start_dex", "stop_dex"}:
        raise web.HTTPBadRequest(text="Unsupported action")
    try:
        STOP_FLAG = f"{TMPDIR}/rd-dex-stopped"
        if action == "start_dex":
            try:
                os.remove(STOP_FLAG)
            except OSError:
                pass
            await asyncio.create_subprocess_exec(
                "bash", f"{HERE}/dex.sh",
                cwd=HERE,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL)
            result = "DeX iniciando; validación en curso"
        elif action == "stop_dex":
            stop_script = os.path.join(
                os.environ.get("HOME", ""), ".shortcuts", "stop-dex.sh"
            )
            await asyncio.create_subprocess_exec(
                "bash", stop_script,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL)
            result = "DeX deteniéndose"
    except OSError as exc:
        print(f"[control] unable to start fixed action {action}: {exc}", flush=True)
        raise web.HTTPServiceUnavailable(text="Requested service action could not start") from exc
    return web.json_response({"result": result})


async def api_status_handler(request):
    s = request.app["state"]
    cs = s.client_stats
    stale = (time.monotonic() - s.client_stats_ts) > 10 if s.client_stats_ts else True
    return web.json_response({
        "server": {
            "frames": s.frames, "dropped": s.dropped, "fps": round(server_fps(s), 1),
            "enc_ms_avg": round((s.proc_us_sum / s.frames / 1000), 1) if s.frames else 0,
            "bytes": s.bytes,
        },
        "client": cs if cs and not stale else None,
        "clients": len(s.clients),
        "config": s.config,
        "device": await device_info(),
        "scrcpy_w": s.scrcpy_w, "scrcpy_h": s.scrcpy_h,
        "scrcpy_alive": s.scrcpy_connected,
        "dex_stopped": os.path.exists(f"{TMPDIR}/rd-dex-stopped"),
        "tls_fingerprint": request.app.get("cert_fingerprint", ""),
        "time": time.time(),
    })


async def ws_handler(request):
    ws = web.WebSocketResponse(max_msg_size=4 * 1024 * 1024, heartbeat=15)
    await ws.prepare(request)
    state = request.app["state"]
    for other in list(state.clients):          # single active session (Sidecar-like)
        try:
            await other.close(code=4000, message=b"replaced")
        except Exception:
            pass
        state.clients.discard(other)
    state.clients.add(ws)
    state.transports[ws] = request.transport
    print(f"[ws] client connected ({len(state.clients)} total)", flush=True)
    state.need_key.add(ws)
    request_key(state)
    if state.config:
        await ws.send_str(json.dumps({"type": "config", **state.config}))
        if state.last_key_frame:
            # serve the cached keyframe so the client can decode immediately (scrcpy has no on-demand IDR)
            payload = b"\x01" + int(time.time() * 1e6).to_bytes(8, "big") + b"\x00" * 4 + state.last_key_frame
            await ws.send_bytes(payload)
            state.need_key.discard(ws)
    try:
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                except Exception:
                    continue
                if data.get("t") == "sync":
                    await ws.send_str(json.dumps({"type": "sync", "s": time.time() * 1000, "c": data.get("c", 0)}))
                elif data.get("t") == "idr":
                    state.need_key.add(ws)
                    request_key(state)
                    if state.last_key_frame:  # scrcpy mode: serve cached keyframe on demand
                        payload = b"\x01" + int(time.time() * 1e6).to_bytes(8, "big") + b"\x00" * 4 + state.last_key_frame
                        await ws.send_bytes(payload)
                        state.need_key.discard(ws)
                elif data.get("t") == "hb":
                    await ws.send_str("h")      # downlink heartbeat (keeps the client's Wi-Fi radio awake)
                elif data.get("t") == "flash":
                    state.flash_t = time.time()
                    injector.flash(data.get("c", 0))
                else:
                    await inject_input(state, data)
            elif msg.type == WSMsgType.ERROR:
                break
    finally:
        state.clients.discard(ws)
        state.transports.pop(ws, None)
        state.need_key.discard(ws)
        print(f"[ws] client disconnected ({len(state.clients)} total)", flush=True)
    return ws


async def on_startup(app):
    global injector
    injector = InputInjector(DISPLAY)
    backend = os.environ.get("BACKEND", "auto")
    print(f"[server] backend={backend} (hw -> x264 fallback)" + ("; control=scrcpy" if backend == "scrcpy" else ""), flush=True)
    app["ffmpeg_task"] = asyncio.create_task(supervisor(app["state"]))
    app["wd_task"] = asyncio.create_task(key_watchdog(app["state"]))


async def on_cleanup(app):
    for task_key in ("ffmpeg_task", "wd_task"):
        task = app.get(task_key)
        if task is None:
            continue
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            print(f"[server] cleanup: {task_key} ended with {exc!r}", flush=True)
    scrcpy_control.close()


def boost_priority():
    """Give the streaming pipeline (and the X server it captures) CPU priority over regular apps.
    Android grants RLIMIT_NICE=40 to app processes, so negative nice values work without root."""
    prio = int(os.environ.get("NICE", "-12"))
    try:
        os.setpriority(os.PRIO_PROCESS, 0, prio)       # children (capd, HwEnc) inherit it
    except Exception as e:
        print(f"[server] cannot raise priority: {e}", flush=True)
        return
    try:
        import subprocess
        pids = subprocess.run(["pgrep", "-f", f"Xvfb {DISPLAY}"], capture_output=True, text=True).stdout.split()
        for pid in pids:
            try:
                os.setpriority(os.PRIO_PROCESS, int(pid), prio)
            except Exception:
                pass
        print(f"[server] priority {prio} applied to server and Xvfb {DISPLAY} ({len(pids)} pid)", flush=True)
    except Exception:
        pass


def main():
    config = load_server_config()
    auth_tokens = config["auth_tokens"]
    bind_host = config["bind_host"]
    ctx = config["ssl_context"]
    boost_priority()
    app = build_app(auth_tokens, config["allowed_hosts"],
                    cert_fingerprint=config["cert_fingerprint"])
    print(
        f"[server] {DISPLAY} {W}x{H}@{FPS} crf={CRF} preset={PRESET} "
        f"on https://{bind_host}:{PORT}",
        flush=True,
    )
    web.run_app(app, host=bind_host, port=PORT, ssl_context=ctx, print=None, shutdown_timeout=1.0)


if __name__ == "__main__":
    main()
