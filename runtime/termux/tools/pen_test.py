#!/usr/bin/env python3
"""Dispara el trazo sintetico UHID del relay (PenProbe) sin tocar la tablet.

Uso:
    python3 pen_test.py [--url https://IP:PUERTO] [--timeout 4]

Lee el token viewer de ~/.config/wtm/auth-tokens.json y la config de
~/.config/wtm/server.env. Nunca imprime el token.
"""
import argparse
import asyncio
import json
import os
import ssl
import sys
from pathlib import Path

import aiohttp

HOME = Path(os.environ.get("HOME", str(Path.home())))
AUTH = HOME / ".config" / "wtm" / "auth-tokens.json"
ENVF = HOME / ".config" / "wtm" / "server.env"


def load_env():
    cfg = {}
    try:
        text = ENVF.read_text()
    except FileNotFoundError:
        return cfg
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        cfg[key.strip()] = value.strip().strip('"').strip("'")
    return cfg


def viewer_token():
    try:
        return json.loads(AUTH.read_text())["viewer"]
    except Exception as exc:  # noqa: BLE001 - mensaje claro al operador
        sys.exit(f"pen_test: no pude leer el token viewer ({exc})")


async def run(url, token, timeout, flash=False):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    async with aiohttp.ClientSession() as session:
        async with session.post(url + "/api/auth", json={"token": token},
                                ssl=ctx, headers={"Origin": url}) as resp:
            if resp.status != 200:
                sys.exit(f"pen_test: auth {resp.status}")
        ws_url = url.replace("https://", "wss://")
        async with session.ws_connect(
                ws_url + "/ws", ssl=ctx,
                headers={"Origin": url,
                         "Cookie": f"wtm_session={token}"}) as ws:
            await ws.send_str('{"t":"flash"}' if flash else '{"t":"pen_test"}')
            loop = asyncio.get_event_loop()
            end = loop.time() + timeout
            while loop.time() < end:
                try:
                    await asyncio.wait_for(ws.receive(), timeout=1.0)
                except asyncio.TimeoutError:
                    pass
    what = "flash de latencia (X11)" if flash else "trazo sintetico"
    print(f"pen_test: {what} enviado a {url}")


def main():
    cfg = load_env()
    host = cfg.get("WTM_BIND_HOST", "127.0.0.1")
    if host in ("0.0.0.0", "::", ""):
        host = "127.0.0.1"
    port = cfg.get("PORT", "8443")
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=f"https://{host}:{port}")
    ap.add_argument("--timeout", type=float, default=4.0)
    ap.add_argument("--flash", action="store_true",
                    help="backend X11: flash de latencia (no aplica a scrcpy)")
    args = ap.parse_args()
    asyncio.run(run(args.url.rstrip("/"), viewer_token(), args.timeout,
                    args.flash))


if __name__ == "__main__":
    main()
