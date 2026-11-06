# Certification of operation

This page records what has been verified for the WTM release candidate, how it
was verified, and what remains unverified. It is kept honest: checks that have
not been performed on real hardware are listed as pending.

## Automated verification performed (evidence on 2026-11-06)

| Suite | Scope | Result |
|---|---|---|
| `runtime/termux/tests/test_server_security.py` | 18 tests: auth config validation, TLS fail-closed (expired cert rejected), Host allowlist, same-site Origin enforcement, cookie login/session, viewer/admin role separation, login throttling, content-type validation, logout, security headers, loopback-only `/healthz` | **18/18 OK** |
| `runtime/termux/tests/test_server_live.py` | 8 tests: boots the real `server.py` process with real TLS and a real socket; healthz, login page fingerprint, protected endpoints, WebSocket gating, viewer login → status, admin control spawn, wrong Origin/Host rejection, logout | **8/8 OK** |
| Shell syntax | `bash -n` on `dex.sh`, `install.sh`, `scrcpy-phone.sh`, `watchdog.sh`, `start_phone.sh`, `update-network.sh`, `shortcuts/start-dex.sh`, `shortcuts/stop-dex.sh`, `boot/01-dex.sh`, `tests/device-smoke.sh` | **OK** |
| Python syntax | `py_compile` on `server.py`, `find-adb-port.py`, both test modules | **OK** |
| Browser JavaScript | `node --check` on the inline scripts of `index.html` and `panel.html` | **OK** |
| Supply chain | Official scrcpy server v5.0.1 downloaded from the Genymobile release and its SHA-256 compared against the pinned value in `install.sh` | **Match: `764eb6f7…53bc536`** |
| Secret sweep | Repository scan for private keys, GitHub tokens, non-loopback IPs, serials, MACs and 64-hex tokens across the candidate sources | **Clean** (only the public scrcpy checksum matched) |

Test environment for the Python suites: Python 3.14, aiohttp 3.14.5, OpenSSL
3.5.5, Linux. The suites run the real HTTP/TLS/WebSocket stack; they do not
exercise Termux, ADB, MediaCodec or a real Chrome client.

## Independent security review

A read-only security review of the complete candidate (server, clients,
scripts, installer, tests) was performed on 2026-11-06, including adversarial
probes (Host spoofing, Origin attacks, WebSocket handshake abuse, encoded
paths). Result: **no CRITICAL or HIGH findings**. Two MEDIUM findings were
fixed before publication:

1. **MITM/token-theft via instructed certificate click-through** — fixed by
   generating and displaying a per-install certificate SHA-256 fingerprint
   (`install.sh` prints it, `/login` and the panel show it, `/healthz` reports
   it); users are instructed to compare fingerprints on first connect and to
   refuse mismatches.
2. **Binding 0.0.0.0 by default** — fixed by defaulting `WTM_BIND_HOST` to the
   detected Wi-Fi IP at install time, plus `update-network.sh` to refresh the
   bind/certificate when the network changes. `0.0.0.0` remains an explicit,
   documented override.

LOW findings also fixed: `/log` now strips control characters, `aiohttp` is
pinned to the tested version, and the wireless-debugging exposure is
documented.

## On-device certification (pending real-hardware run)

`runtime/termux/tests/device-smoke.sh` is a non-destructive certification
script to run in Termux on the source phone:

```sh
cd ~/dex
bash tests/device-smoke.sh
```

It verifies the full installed chain: config permissions, token quality, TLS
validity and fingerprint match, ADB device discovery, scrcpy server presence,
`/healthz`, admin login, `/api/status` (including `scrcpy_alive: true` and the
2000x1200@60 stream config), viewer 403 on control, unknown action 400,
session-less 401s and WebSocket gating. Output and evidence are written to
`$PREFIX/tmp/wtm-certification.log`.

**Status on 2026-11-06: not yet executed on the S25.** The parent environment
for this work has no ADB client, so the script was syntax-validated but the
hardware run is pending. Until that run passes, do not claim the deployed
phone is certified.

## What this certification does NOT cover

- Real Chrome/WebCodecs decode on the tablet (the 2K60 performance evidence
  in [performance](performance.md) remains the reference; no new measurement
  was taken during this work).
- Long-duration stability, thermal behavior, reboot recovery or Wi-Fi
  handover.
- Any guarantee of security: passing these tests means the implemented
  controls behave as specified, not that the system is invulnerable. See
  [threat-model](threat-model.md) for the complete model, including residual
  risks.

## Independent external reviews

| Date | Reviewer | Scope | Result | Notes |
|---|---|---|---|---|
| 2026-11-06 | Independent AI review (external) | Repository-wide | Reported missing LICENSE, source code, SECURITY.md, CONTRIBUTING.md, requirements and installer | **Stale snapshot**: the review cited the pre-runtime documentation state. All cited gaps were already resolved (LICENSE, `runtime/termux/` sources, SECURITY.md, CONTRIBUTING.md, `.gitignore`). Two legitimate items were adopted: `CODE_OF_CONDUCT.md`, `AGENTS.md` (AI-assistant protocol), `docs/threat-model.md` and `runtime/termux/requirements.txt` |
| 2026-11-06 | Independent security review (read-only, adversarial probes) | Release-candidate runtime | No CRITICAL/HIGH; 2 MEDIUM, 3 LOW — all fixed before publication | Evidence in [runtime-release-candidate](runtime-release-candidate.md) and SECURITY.md |
| 2026-11-06 | Independent AI re-audit (external, v2) — commit `69d53c3` | Repository-wide post-governance | 16 findings: verified all governance changes present; flagged stale README status text, CI secret sweep failing open when `rg` is missing, hardcoded watchdog port, divergent server default port (8090 vs 8443), absent private vulnerability channel | Fixes landed on branch `fix/runtime-consistency`: README status sync, `watchdog.sh` loads `server.env`, `SECURITY.md` token-rotation procedure and corrected throttle description, this review record. CI sweep fix blocked by missing `workflow` OAuth scope in this session; the one-line `server.py` port default remains proposed in the PR description |

## Certification criteria status

The acceptance gates proposed in [performance](performance.md) (resolution, 57 FPS
over 30-minute windows, drop rate, latency percentiles, Wi-Fi conditions, thermal
reporting, reconnect) have **not** been executed. No long-duration soak test exists
yet. Until one passes, no release may claim "2K60".
