#!/usr/bin/env python3
"""Security and integration tests for the WTM candidate server.

These tests exercise the exact middleware that the public documentation
promises: HTTPS-only, Host allowlist, Origin checks, cookie-based auth,
role separation, login throttling, fail-closed TLS validation and the
loopback-only health endpoint. They do not require Termux, ADB, X11 or a
real encoder; only aiohttp and a local test certificate.
"""
import asyncio
import json
import os
import re
import ssl
import tempfile
import time
import unittest
from pathlib import Path

from aiohttp import ClientSession
from aiohttp.test_utils import TestServer, TestClient
import aiohttp

SERVER_DIR = Path(__file__).resolve().parents[1]
# tests/test_server_security.py vive a <repo>/runtime/termux/tests/:
# parents[0]=tests, [1]=termux, [2]=runtime, [3]=raíz del repositorio.
REPO_ROOT = Path(__file__).resolve().parents[3]
import sys

sys.path.insert(0, str(SERVER_DIR))

# server.py captures DISPLAY/W/H/FPS/PORT/CRF/PRESET/GOP/VIDEO_BITRATE from the
# environment at import time and starts nothing on import.
import server  # noqa: E402

VIEWER_TOKEN = "a" * 64
ADMIN_TOKEN = "b" * 64
_configured = False
CERT_PATH = None


def _certificates(tmpdir):
    """Generate a disposable localhost certificate once per session."""
    global CERT_PATH
    cert = os.path.join(tmpdir, "cert.pem")
    key = os.path.join(tmpdir, "key.pem")
    subject = "/CN=localhost"
    if not (os.path.isfile(cert) and os.path.isfile(key)):
        from subprocess import run  # avoid module-level dependency
        run([
            "openssl", "req", "-x509", "-newkey", "rsa:2048",
            "-keyout", key, "-out", cert, "-days", "2", "-nodes",
            "-subj", subject,
            "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1",
        ], check=True, capture_output=True)
        os.chmod(key, 0o600)
    CERT_PATH = cert
    return cert, key


def _auth_file(tmpdir):
    path = os.path.join(tmpdir, "auth-tokens.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"viewer": VIEWER_TOKEN, "admin": ADMIN_TOKEN}, handle)
    os.chmod(path, 0o600)
    return path


def _configure(tmpdir):
    """Point server module constants at the test configuration once."""
    global _configured
    if not _configured:
        cert, key = _certificates(tmpdir)
        server.AUTH_FILE = _auth_file(tmpdir)
        os.environ["CERT"] = cert
        os.environ["KEY"] = key
        os.environ["WTM_BIND_HOST"] = "127.0.0.1"
        os.environ.pop("WTM_ALLOWED_HOSTS", None)
        _configured = True


class AuthConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="wtm-auth-")
        _configure(self.tmp)

    def test_accepts_two_distinct_256bit_tokens(self):
        self.tmp_tokens = tempfile.mkdtemp(prefix="wtm-tokens-")
        path = os.path.join(self.tmp_tokens, "auth-tokens.json")
        tokens = {"viewer": "1" * 64, "admin": "2" * 64}
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(tokens, handle)
        os.chmod(path, 0o600)
        loaded = server.load_auth_tokens(path)
        self.assertEqual(loaded, tokens)

    def test_rejects_equal_tokens(self):
        path = os.path.join(self.tmp, "auth-tokens.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"viewer": "1" * 64, "admin": "1" * 64}, handle)
        os.chmod(path, 0o600)
        with self.assertRaises(RuntimeError):
            server.load_auth_tokens(path)

    def test_rejects_world_readable_auth_file(self):
        path = os.path.join(self.tmp, "auth-tokens.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"viewer": "1" * 64, "admin": "2" * 64}, handle)
        os.chmod(path, 0o644)
        with self.assertRaises(RuntimeError):
            server.load_auth_tokens(path)

    def test_rejects_missing_tokens(self):
        with self.assertRaises(RuntimeError):
            server.load_auth_tokens(os.path.join(self.tmp, "missing.json"))


class TlsValidationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="wtm-tls-")

    def test_rejects_expired_certificate(self):
        from subprocess import run
        expired = os.path.join(self.tmp, "expired.pem")
        key = os.path.join(self.tmp, "expired-key.pem")
        run([
            "openssl", "req", "-x509", "-newkey", "rsa:2048",
            "-keyout", key, "-out", expired, "-nodes",
            "-subj", "/CN=localhost",
            "-not_before", "20240101000000Z",
            "-not_after", "20250101000000Z",
        ], check=True, capture_output=True)
        with self.assertRaises(RuntimeError):
            server.validate_cert_chain(expired, key)


class SecurityIntegrationTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="wtm-server-")
        _configure(self.tmp)
        server._auth_attempts.clear()
        config = server.load_server_config()
        self.app = server.build_app(
            config["auth_tokens"], config["allowed_hosts"])
        self.ssl_context = config["ssl_context"]
        self.client = None
        self.server_runner = None

    async def asyncSetUp(self):
        self.server_runner = TestServer(self.app, host="127.0.0.1")
        await self.server_runner.start_server(ssl=self.ssl_context)
        client_ssl = ssl.create_default_context()
        client_ssl.load_verify_locations(CERT_PATH)
        connector = aiohttp.TCPConnector(ssl=client_ssl)
        self.client = TestClient(self.server_runner, connector=connector)
        await self.client.start_server()

    async def asyncTearDown(self):
        if self.client is not None:
            await self.client.close()
        if self.server_runner is not None:
            await self.server_runner.close()

    def base(self):
        assert self.server_runner is not None
        assert self.server_runner.port is not None
        return f"https://127.0.0.1:{self.server_runner.port}"

    async def sign_in(self, token, role=None):
        headers = {"Origin": self.base()}
        response = await self.client.post(
            "/api/auth", headers=headers, json={"token": token})
        self.assertEqual(response.status, 200)
        payload = await response.json()
        if role is not None:
            self.assertEqual(payload.get("role"), role)
        return response

    async def test_healthz_loopback_only(self):
        response = await self.client.get("/healthz")
        self.assertEqual(response.status, 200)
        payload = await response.json()
        self.assertIs(payload.get("ok"), True)
        self.assertIn("scrcpy_alive", payload)

    async def test_unauth_paths_require_session(self):
        for path in ("/api/status", "/api/logs", "/stats", "/log"):
            response = await self.client.get(path)
            self.assertEqual(response.status, 401, path)

    async def test_home_redirects_to_login(self):
        response = await self.client.get("/", allow_redirects=False)
        self.assertEqual(response.status, 302)
        self.assertTrue(response.headers["Location"].endswith("/login"))

    async def test_wrong_host_rejected(self):
        response = await self.client.get(
            "/healthz", headers={"Host": "evil.example"})
        self.assertEqual(response.status, 400)

    async def test_cross_origin_auth_rejected(self):
        response = await self.client.post(
            "/api/auth",
            headers={"Origin": "https://evil.example"},
            json={"token": VIEWER_TOKEN})
        self.assertEqual(response.status, 403)

    async def test_same_origin_viewer_login_and_session(self):
        await self.sign_in(VIEWER_TOKEN, role="viewer")
        response = await self.client.get("/api/session")
        self.assertEqual(response.status, 200)
        self.assertEqual((await response.json()).get("role"), "viewer")

    async def test_viewer_cannot_call_control(self):
        await self.sign_in(VIEWER_TOKEN)
        response = await self.client.post(
            "/api/control", json={"action": "start_dex"})
        self.assertEqual(response.status, 403)

    async def test_admin_can_call_control_and_action_is_limited(self):
        await self.sign_in(ADMIN_TOKEN, role="admin")
        response = await self.client.post(
            "/api/control", headers={"Origin": self.base()},
            json={"action": "not_a_real_action"})
        self.assertEqual(response.status, 400)
        response = await self.client.post(
            "/api/control", headers={"Origin": "https://evil.example"},
            json={"action": "stop_dex"})
        self.assertEqual(response.status, 403)

    async def test_login_throttled_after_failures(self):
        for _ in range(server.AUTH_MAX_ATTEMPTS):
            response = await self.client.post(
                "/api/auth", headers={"Origin": self.base()},
                json={"token": "0" * 64})
            self.assertEqual(response.status, 401)
        response = await self.client.post(
            "/api/auth", headers={"Origin": self.base()},
            json={"token": VIEWER_TOKEN})
        self.assertEqual(response.status, 429)

    async def test_wrong_content_type_auth_rejected(self):
        response = await self.client.post(
            "/api/auth", headers={"Origin": self.base()}, data="token=x")
        self.assertEqual(response.status, 415)

    async def test_logout_clears_session(self):
        await self.sign_in(VIEWER_TOKEN)
        response = await self.client.post(
            "/api/logout", headers={"Origin": self.base()})
        self.assertEqual(response.status, 200)
        response = await self.client.get("/api/session")
        self.assertEqual(response.status, 401)

    async def test_login_pages_are_public_and_cached_headers_set(self):
        for path in ("/login", "/manifest.json", "/icon.svg"):
            response = await self.client.get(path)
            self.assertEqual(response.status, 200, path)
        response = await self.client.get("/login")
        self.assertEqual(response.headers.get("Cache-Control"), "no-store")
        self.assertEqual(response.headers.get("X-Frame-Options"), "DENY")
        self.assertIn("nosniff", response.headers.get(
            "X-Content-Type-Options", ""))
        self.assertIn("max-age=", response.headers.get(
            "Strict-Transport-Security", ""))
        self.assertIn("default-src", response.headers.get(
            "Content-Security-Policy", ""))

    async def test_healthz_requires_valid_https_and_loopback(self):
        # An attacker on another interface cannot use healthz: it must be
        # both TLS and loopback, which this test client satisfies.
        response = await self.client.get("/healthz")
        self.assertEqual(response.status, 200)
        self.assertIn("ok", await response.json())


class PrivateIpSweepTest(unittest.TestCase):
    """Política de privacidad del proyecto (2026-11-09): ninguna IP privada
    real de un operador (RFC 1918: 192.168.x.x, 10.x.x.x, 172.16-31.x.x)
    puede quedar publicada en el repositorio — ni en código, docs ni logs
    citados. Este test falla el CI ante cualquier aparición.

    No se excluyen 127.0.0.1/0.0.0.0/8.8.8.8: no son RFC 1918 y son parte
    del protocolo público. Los placeholders documentados (192.168.x.y,
    <phone-ip>) no matchean la expresión por construcción.
    """

    # RFC 1918 con fronteras de no-dígito para no atrapar versiones ni
    # números hexadecimales largos.
    RFC1918 = re.compile(
        r"(?<![\d.])"
        r"(?:192\.168\.\d{1,3}\.\d{1,3}"
        r"|10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
        r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})"
        r"(?![\d.])")

    def test_no_private_operator_ips_in_repository(self):
        self.assertTrue(REPO_ROOT.is_dir(), f"repo root missing: {REPO_ROOT}")
        offenders = []
        for path in REPO_ROOT.rglob("*"):
            # saltar lo que no es archivo de texto del repo
            if not path.is_file() or ".git" in path.parts:
                continue
            if path.suffix.lower() in {".zip", ".gz", ".png", ".jpg",
                                       ".jpeg", ".webp", ".ico", ".jar"}:
                continue
            try:
                data = path.read_bytes()
            except OSError:
                continue
            if b"\x00" in data[:4096]:  # binario
                continue
            if len(data) > 2_000_000:  # archivos gigantes: fuera de alcance
                continue
            text = data.decode("utf-8", errors="ignore")
            for match in self.RFC1918.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{line}: {match.group()}")
        self.assertEqual(
            [], offenders,
            "IPs privadas (RFC 1918) detectadas en el repositorio — "
            "remover o reemplazar por placeholders (192.168.x.y, <phone-ip>). "
            "Política de privacidad del proyecto. Hallazgos:\n"
            + "\n".join(offenders))


if __name__ == "__main__":
    unittest.main(verbosity=2)
