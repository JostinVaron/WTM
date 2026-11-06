# Runtime release candidate (branch `executable-release-candidate`)

This branch packages the active Termux runtime as a repeatable install, adds
per-install credentials and session authentication, and records the security
tests that back those changes. It is **not yet deployed** on the prototype
phone. Licensing: [PolyForm Noncommercial 1.0.0](../LICENSE) for software,
[CC BY-NC 4.0](../LICENSE-docs.txt) for documentation — see
[Licensing](../LICENSING.md).

## What changed compared to the published documentation

| Area | Published prototype (as deployed) | Release candidate |
|---|---|---|
| Server auth | None; WebSocket/control/log endpoints open | Cookie session (`viewer`/`admin` tokens), `/login`, `/api/auth`, throttled by IP after 6 failures in 5 minutes |
| Control endpoint | Any action, shell string routing | Admin-only; two fixed actions (`start_dex`, `stop_dex`); JSON content type required |
| Transport | HTTPS with optional HTTP fallback | HTTPS-only; server refuses to start without valid, unexpired TLS material |
| Host/Origin | Any host accepted, bound to 0.0.0.0 | Host allowlist (`WTM_ALLOWED_HOSTS`); same-site Origin required for POST/WS |
| Health checks | `/stats`, `/api/status` used by scripts | New `/healthz` (machine-local, no auth) used by scripts; `/api/*` requires a session |
| Secrets | Shared cert/key inside the project tree | Generated per install under `~/.config/wtm/` (tokens, cert, key, `server.env`), outside the repository |
| Certificate trust | Click-through certificate warning | Per-install SHA-256 fingerprint shown at install, on `/login`, in the panel and on `/healthz`; users must compare on first connect |
| Listener | Bound to 0.0.0.0 | Binds to the detected Wi-Fi IP by default; `update-network.sh` refreshes bind and certificate when the network changes; `0.0.0.0` only as an explicit override |
| Install | Manual copy of an existing tree | `runtime/termux/install.sh`: idempotent, installs Termux packages, copies sources, downloads the official scrcpy server with pinned SHA-256, pins `aiohttp==3.14.5`, generates credentials, installs Termux:Widget shortcuts and Termux:Boot hook |
| Device model | Hard-coded `SM-S931B` | `WTM_PHONE_MODEL` (default `SM-S931B`); multiple matching devices abort instead of guessing |
| Client UI | `panel.html` offered a mouse-routing button | Mouse-routing control removed (server no longer supports it); login-aware clients redirect to `/login` on 401; logout button |

## Files in the candidate

- `runtime/termux/server.py` — hardened relay (TLS, auth, allowlist, role checks).
- `runtime/termux/web/index.html`, `runtime/termux/web/panel.html` — login-aware clients.
- `runtime/termux/dex.sh`, `scrcpy-phone.sh`, `watchdog.sh`, `start_phone.sh`,
  `update-network.sh`, `find-adb-port.py` — launcher/watchdog scripts reading
  `~/.config/wtm/server.env`.
- `runtime/termux/shortcuts/start-dex.sh`, `runtime/termux/shortcuts/stop-dex.sh`,
  `runtime/termux/boot/01-dex.sh` — Termux integration points.
- `runtime/termux/install.sh` — idempotent Termux installer.
- `runtime/termux/tests/test_server_security.py` — 18 automated tests.
- `runtime/termux/tests/test_server_live.py` — 8 process-level tests with real TLS.
- `runtime/termux/tests/device-smoke.sh` — on-device certification script (Termux).

## Provenance of the only binary dependency

`install.sh` downloads the official scrcpy Android server v5.0.1 from
<https://github.com/Genymobile/scrcpy/releases/download/v5.0.1/scrcpy-server-v5.0.1>
and verifies SHA-256 `764eb6f79811d5211fe9df341120882ba9994c7a61b897d7bf3fb662e53bc536`
before use. That hash was verified on 2026-11-06 against the upstream release asset
and matches the artifact already deployed on the prototype. scrcpy is published by
Genymobile under Apache-2.0; WTM is licensed as described in
[Licensing](../LICENSING.md).

## Automated validation performed

- `python3 -m unittest tests.test_server_security` — 18/18 pass (auth config,
  TLS fail-closed, Host allowlist, Origin enforcement, login/session flow,
  role separation, throttling, security headers, healthz access rules).
- `python3 -m unittest tests.test_server_live` — 8/8 pass (real server
  process, real TLS, healthz, fingerprint on login, WebSocket gating, viewer
  limits, admin control spawn, logout).
- `bash -n` on all ten shell scripts (including `tests/device-smoke.sh`).
- `python3 -m py_compile` on the Python sources.
- `node --check` on the inline JavaScript of both browser clients.
- scrcpy server checksum verified against the upstream release asset.

The tests run on a host Python 3.14 with aiohttp 3.14.5; they do not exercise
Termux, ADB, MediaCodec or a real client. The on-device certification covers
those and passed on the S25 on 2026-11-06; see
[certification](certification.md) for the full evidence table.

## Publication status

The candidate sources were merged to `main` and published after:

1. **License** — selected by the owner: PolyForm Noncommercial 1.0.0 for
   software, CC BY-NC 4.0 for documentation.
2. **Independent security review** — completed; no CRITICAL/HIGH findings.
   The two MEDIUM findings (certificate click-through MITM path, 0.0.0.0
   default bind) and the LOW findings were fixed and re-tested.
3. **On-device acceptance** — completed 2026-11-06. v0.1.2 was installed on
   the S25 and passed `device-smoke.sh` 20/20, including a live browser
   session with fingerprint-verified login, video decode and injected input.
   The runtime is hardware-certified for the tested pair, not for arbitrary
   devices.

Do not claim this code is "impenetrable" or "secure". It has concrete,
documented controls and passing tests for those controls, nothing more.
