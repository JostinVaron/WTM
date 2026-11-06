#!/usr/bin/env python3
"""Live process-level certification for the WTM server.

Boots the real server.py as a subprocess with real TLS material and a real
socket, then exercises the documented flows with a raw HTTP client:

  - /healthz responds on loopback without a session
  - /login shows the certificate fingerprint
  - viewer login -> cookie -> /api/status, /stats
  - viewer cannot call /api/control (403)
  - admin login -> /api/control spawns the fixed action
  - WebSocket handshake without a session is rejected (401)
  - wrong Origin and wrong Host are rejected

This test does not need Termux, ADB, X11 or a real encoder; the streaming
supervisor retries harmlessly when its backend is unavailable.
"""
import json
import os
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_DIR))

VIEWER_TOKEN = "c" * 64
ADMIN_TOKEN = "d" * 64


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def cert_pair(tmpdir):
    cert = os.path.join(tmpdir, "cert.pem")
    key = os.path.join(tmpdir, "key.pem")
    subprocess.run([
        "openssl", "req", "-x509", "-newkey", "rsa:2048",
        "-keyout", key, "-out", cert, "-days", "2", "-nodes",
        "-subj", "/CN=localhost",
        "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1",
    ], check=True, capture_output=True)
    os.chmod(key, 0o600)
    return cert, key


class LiveServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.mkdtemp(prefix="wtm-live-")
        cert, key = cert_pair(cls.tmpdir)
        auth_file = os.path.join(cls.tmpdir, "auth-tokens.json")
        with open(auth_file, "w", encoding="utf-8") as handle:
            json.dump({"viewer": VIEWER_TOKEN, "admin": ADMIN_TOKEN}, handle)
        os.chmod(auth_file, 0o600)
        cls.port = free_port()
        env = dict(os.environ)
        env.update({
            "PORT": str(cls.port),
            "WTM_BIND_HOST": "127.0.0.1",
            "WTM_ALLOWED_HOSTS": "",
            "WTM_AUTH_FILE": auth_file,
            "CERT": cert,
            "KEY": key,
            "TMPDIR": cls.tmpdir,
            "DISPLAY": ":98",
            "BACKEND": "auto",
        })
        cls.proc = subprocess.Popen(
            [sys.executable, str(SERVER_DIR / "server.py")],
            cwd=str(SERVER_DIR), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        cls.base = f"https://127.0.0.1:{cls.port}"
        cls.cert = cert
        deadline = time.time() + 20
        while time.time() < deadline:
            if cls.proc.poll() is not None:
                out = cls.proc.stdout.read().decode("utf-8", "replace")
                raise RuntimeError(f"server exited early:\n{out}")
            try:
                ctx = ssl.create_default_context()
                ctx.load_verify_locations(cls.cert)
                with urllib.request.urlopen(  # noqa: S310 (test only)
                        f"{cls.base}/healthz", context=ctx, timeout=2) as resp:
                    if resp.status == 200:
                        break
            except Exception:
                pass
            time.sleep(0.3)
        else:
            raise RuntimeError("server did not become healthy in time")

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        try:
            cls.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            cls.proc.kill()
            cls.proc.wait()
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def request(self, path, method="GET", headers=None, data=None,
                cookie=None):
        url = self.base + path
        req = urllib.request.Request(url, method=method)
        ctx = ssl.create_default_context()
        ctx.load_verify_locations(self.cert)
        for key, value in (headers or {}).items():
            req.add_header(key, value)
        if cookie:
            req.add_header("Cookie", cookie)
        if data is not None:
            req.add_header("Content-Type", "application/json")
            req.data = data.encode("utf-8")
        try:
            with urllib.request.urlopen(  # noqa: S310 (test only)
                    req, context=ctx, timeout=10) as resp:
                return resp.status, resp.headers, resp.read().decode(
                    "utf-8", "replace")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.headers, exc.read().decode(
                "utf-8", "replace")

    def login(self, token):
        status, headers, _body = self.request(
            "/api/auth", method="POST",
            headers={"Origin": self.base},
            data=json.dumps({"token": token}))
        self.assertEqual(status, 200)
        cookie = headers.get("Set-Cookie", "").split(";")[0]
        self.assertTrue(cookie.startswith("wtm_session="))
        return cookie

    def test_healthz_loopback_without_session(self):
        status, _headers, body = self.request("/healthz")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertIs(payload.get("ok"), True)
        self.assertIn("scrcpy_alive", payload)
        self.assertRegex(payload.get("tls_fingerprint", ""), r"^([0-9A-F]{2}:){31}[0-9A-F]{2}$")

    def test_login_page_shows_fingerprint(self):
        status, _headers, body = self.request("/login")
        self.assertEqual(status, 200)
        self.assertIn("Huella SHA-256", body)

    def test_protected_paths_require_session(self):
        for path in ("/api/status", "/stats", "/api/logs"):
            status, _headers, _body = self.request(path)
            self.assertEqual(status, 401, path)

    def test_websocket_requires_session(self):
        status, _headers, _body = self.request(
            "/ws", headers={
                "Upgrade": "websocket",
                "Connection": "Upgrade",
                "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ==",
                "Sec-WebSocket-Version": "13",
            })
        self.assertEqual(status, 401)

    def test_viewer_login_status_and_role_limits(self):
        cookie = self.login(VIEWER_TOKEN)
        status, _headers, body = self.request("/api/status", cookie=cookie)
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertIn("tls_fingerprint", payload)
        self.assertIn("scrcpy_alive", payload)
        status, _headers, _body = self.request(
            "/api/control", method="POST",
            headers={"Origin": self.base},
            data=json.dumps({"action": "stop_dex"}), cookie=cookie)
        self.assertEqual(status, 403)

    def test_admin_control_spawns_fixed_action(self):
        cookie = self.login(ADMIN_TOKEN)
        status, _headers, body = self.request(
            "/api/control", method="POST",
            headers={"Origin": self.base},
            data=json.dumps({"action": "start_dex"}), cookie=cookie)
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertIn("result", payload)

    def test_wrong_origin_and_host_rejected(self):
        status, _headers, _body = self.request(
            "/api/auth", method="POST",
            headers={"Origin": "https://evil.example"},
            data=json.dumps({"token": VIEWER_TOKEN}))
        self.assertEqual(status, 403)
        status, _headers, _body = self.request(
            "/healthz", headers={"Host": "evil.example"})
        self.assertEqual(status, 400)

    def test_logout_and_session_status(self):
        cookie = self.login(VIEWER_TOKEN)
        status, _headers, body = self.request("/api/session", cookie=cookie)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body).get("role"), "viewer")
        status, headers, _body = self.request(
            "/api/logout", method="POST",
            headers={"Origin": self.base}, cookie=cookie)
        self.assertEqual(status, 200)
        # Browser behavior: logout expires the cookie client-side. The raw
        # test client must adopt the expired cookie it received.
        cleared = headers.get("Set-Cookie", "").split(";")[0]
        status, _headers, _body = self.request("/api/session", cookie=cleared)
        self.assertEqual(status, 401)


if __name__ == "__main__":
    unittest.main(verbosity=2)
