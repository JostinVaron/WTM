# Current prototype deployment: Termux + Wireless ADB

This procedure describes the existing prototype, not a one-click installer or a
Termux-independent product. It assumes the project has been installed in
`$HOME/dex` on the source phone.

The hardened runtime and its installer live in `runtime/termux/` on `main`.
**The S25 was migrated to the hardened runtime (v0.1.2) on 2026-11-06 and passed
the on-device certification (20/20 checks).** See
[runtime release candidate](runtime-release-candidate.md) for what changed
and [certification](certification.md) for the evidence.

## Important: two deployment paths

**Path A — published installer (source, Termux).** `main` includes
`runtime/termux/install.sh`, which installs the hardened runtime from this
repository: it installs Termux packages, copies the sources, downloads the
official scrcpy server v5.0.1 with a pinned SHA-256, generates per-install
tokens and TLS material, and installs Termux:Widget/Termux:Boot hooks. This
path has host-level automated tests, a completed security review, and — since
2026-11-06 — a completed on-device certification on the S25 (v0.1.2, 20/20
checks including a live browser session).

**Path B — historical pre-hardening prototype.** The service that ran on the
S25 before 2026-11-06 was installed manually before the installer existed. It
was unauthenticated (no login, no Origin/Host checks) and is described below
as historical context. **This prototype has been superseded on the S25 by the
hardened runtime via Path A.**

## Prerequisites

1. The source phone and tablet are on a trusted local network.
2. Termux is installed on the S25 and Android battery/background restrictions have been
   reviewed.
3. ADB, Python and `aiohttp` are installed in Termux.
4. The scrcpy 5.0.1 server artifact is present at the project path and its redistribution
   terms have been checked.
5. **ADB reachable from Termux is mandatory** — adb is the only rootless mechanism Android
   provides to create the virtual display and launch the scrcpy server. Two modes are
   supported:
   - **adbd TCP mode on the fixed port 5555** (`adb tcpip 5555`, seeded once over USB or
     from an existing ADB session): stable address and port, but **it does not survive
     phone reboots** and must be re-seeded after every reboot (see the post-reboot
     runbook below).
   - **Wireless Debugging** (Developer Options): the toggle persists across reboots, but
     the listening port is random per boot; `runtime/termux/find-adb-port.py` discovers
     it automatically. Pair the ADB client once with the Android pairing flow; do not
     publish pairing keys. On the tested One UI build this toggle turns itself off when
     Wi-Fi client connectivity drops, so it is unreliable while the phone hosts the
     hotspot.
   Either way, after every source-phone reboot exactly one manual step is currently
   unavoidable without root: re-seed the fixed port (USB) or re-enable the Wireless
   Debugging toggle. Tracked in [issue #15](https://github.com/JostinVaron/WTM/issues/15).
6. Termux:Widget is installed and the shortcut scripts are executable. Termux:Boot is
   optional and must be configured separately.
7. The tablet has Chrome with WebCodecs and can reach the phone's relay IP/port.

## Start

The one-tap shortcut is:

```text
$HOME/.shortcuts/start-dex.sh
```

It requests a wake lock when available and calls:

```sh
cd "$HOME/dex"
bash ./dex.sh
```

`dex.sh` confirms the phone model over ADB, starts or reconnects the scrcpy server,
ensures the relay is listening, waits for three consecutive readiness checks and attempts
to route pointer input.

### Current production encoder configuration

Since the 2026-11-09 bitrate ladder (see [codec study](hevc-study.md) and
[performance evidence](performance.md)), the shipped configuration is hardware
**HEVC (H.265) at 35 Mbps CBR** with `i-frame-interval=1`, 2000x1200 @ 60 fps.
Measured evidence: at 70 Mbps the viewer's hardware decoder saturated on complex 4K60
content (sustained decode_ms 165-281 ms against a 16.7 ms/frame budget); at 35 Mbps
decode stayed in budget (18-22 ms) with no perceived quality loss on the viewer panel,
and the link sustained ~68 Mbps with rtt 3-5 ms. H.264 remains the documented rollback
(`VIDEO_CODEC=h264`).

The equivalent current bridge page is:

```text
https://<S25-LAN-IP>:8443/
```

The installed runtime uses a self-signed certificate with a published SHA-256
fingerprint. The certificate warning is not a production trust model; verify the
fingerprint as described below and do not disable certificate validation globally.

## Stop

The Termux:Widget stop shortcut is:

```text
$HOME/.shortcuts/stop-dex.sh
```

It marks the watchdog as intentionally stopped, removes the ADB forward, terminates the
scrcpy display process where reachable, stops the relay and releases the wake lock.
Verify that `/api/status` no longer responds and no scrcpy virtual display remains.

## Post-reboot recovery runbook (ADB does not survive reboots)

`adb tcpip 5555` — the fixed-port adbd TCP mode this deployment uses — **does not
survive phone reboots**: Android reverts adbd to USB mode on every boot. The Wireless
Debugging toggle does not solve it by itself: One UI turns it off when Wi-Fi client
connectivity drops, and its listening port is random per boot. Incident evidence and
the automation plan are tracked in
[issue #15](https://github.com/JostinVaron/WTM/issues/15).

Failure cascade when this bites (observed 2026-11-09): reboot → adbd back in USB
mode → no ADB device → scrcpy server never starts → relay never starts → no
`rd-bridge.log`, no `healthz` response, viewer black/dead. The widget toast only says
the start failed; the explicit reason is in `$PREFIX/tmp/rd-start.log`.

After every source-phone reboot, exactly **one manual step** is currently unavoidable
without root (adb is the only rootless mechanism to create the virtual display).
Choose either route:

**Route A — re-seed the fixed port (USB cable, ~2 minutes):**

```sh
# from any PC with adb, phone connected over USB:
adb tcpip 5555
# unplug; back on the phone tap the start widget — or, if the Termux:Boot
# watchdog is running, recovery is automatic within ~10 s
```

**Route B — Wireless Debugging toggle (no cable; requires Wi-Fi client
connectivity, so it is unreliable while the phone hosts the hotspot):**

```text
Developer Options → enable Wireless Debugging (pairing is one-time)
```

Then tap the start widget: `find-adb-port.py` discovers the per-boot port and
`dex.sh` connects automatically. The watchdog performs the same discovery every
10 s, so with the toggle on, recovery is hands-off after the toggle itself.

**Diagnostics after a failed start (in order):**

```sh
adb devices -l                        # empty → ADB is the broken link
adb connect 127.0.0.1:5555            # refused → adbd not in TCP mode
tail -n 40 $PREFIX/tmp/rd-start.log   # explicit abort reason
adb shell "ip -4 addr show swlan0"    # hotspot IP changed → update-network.sh
```

Only after ADB is back do the remaining checks make sense: `healthz` on the
configured bind host and the phone-side scrcpy log. If the hotspot IP changed
with the reboot, run `bash update-network.sh` before starting, and re-verify
the new certificate fingerprint on the tablet.

## Boot and recovery behavior

`$HOME/.termux/boot/01-dex.sh` attempts to obtain a wake lock and start
`$HOME/dex/watchdog.sh`. The watchdog looks for the S25 among ADB devices and scans for
the local ADB TCP service when missing. It restarts the bridge only when the phone is
available.

This is best-effort recovery. It cannot:

- re-seed adbd TCP mode after a phone reboot (see the post-reboot runbook above);
- turn Wireless Debugging on after Android disables it;
- accept a new pairing request or user consent;
- guarantee the current connect port is discoverable;
- revive Termux after Android force-stop;
- preserve the Termux-owned ADB forward after the ADB daemon dies;
- guarantee execution under vendor battery management.

If start fails, inspect the Termux logs under `$PREFIX/tmp/rd-start.log`,
`rd-bridge.log`, `rd-watchdog.log` and the phone-side
`/data/local/tmp/scrcpy.log`. Redact IPs, serials, pairing output and private paths before
sharing logs.

## Validate operation

On the phone/Termux shell:

```sh
adb devices -l
curl -k https://127.0.0.1:8443/api/status
```

Expected status fields include `scrcpy_alive: true`, one or more WebSocket clients,
`config.width: 2000`, `config.height: 1200` and `config.fps: 60`. The FPS field in the
configuration is not measured throughput. Use moving content and inspect per-window
client/source metrics.

## Known interruption cases

- Source phone rebooted: adbd reverts to USB mode and the fixed TCP port dies; see the
  post-reboot runbook above.
- Wireless Debugging disabled or port changed.
- ADB authorization revoked.
- Termux force-stopped, reclaimed, updated or battery-restricted.
- Wi-Fi changes IP/subnet or isolates clients.
- Relay killed while the viewer still has a WebSocket.
- scrcpy server exits, virtual display is removed or Android resets the encoder.
- Tablet Chrome sleeps, reloads or loses WebCodecs state.

Do not claim restart-proof behavior unless each case is exercised and measured on the
published support matrix.

## Published installer (source, hardware-certified on the S25)

`runtime/termux/install.sh` is an idempotent Termux installer that:

1. verifies it runs in Termux on the source phone and detects the LAN IP for the
   allowed host list and listener bind (override both with `WTM_ALLOWED_HOSTS` and
   `WTM_BIND_HOST`);
2. installs missing Termux packages (`python`, `aiohttp==3.14.5`, `openssl`, `curl`);
3. copies the package sources into `$HOME/dex`;
4. downloads the official scrcpy server v5.0.1 and verifies SHA-256
   `764eb6f79811d5211fe9df341120882ba9994c7a61b897d7bf3fb662e53bc536`;
5. generates per-install viewer/admin tokens, a TLS certificate/key, a
   `server.env` and a certificate fingerprint file under `~/.config/wtm/`
   (never overwriting existing credentials);
6. installs Termux:Widget shortcuts and the Termux:Boot hook.

After installation the bridge requires a browser login: open
`https://<phone-ip>:8443/`, **compare the certificate fingerprint shown on the
login page with the value printed at install time** (`~/.config/wtm/cert-fingerprint.txt`),
and only then enter one of the generated tokens. A mismatch means the connection may be
intercepted: do not enter tokens. The admin token unlocks the control panel
(`https://<phone-ip>:8443/panel`); the viewer token allows watching and input but not
start/stop actions. Local health checks use the unauthenticated machine-local
`https://127.0.0.1:8443/healthz`.

When the phone changes network/IP, run `bash update-network.sh` in `$HOME/dex` to
refresh the listener bind and regenerate the certificate for the new IP, then verify
the new fingerprint on the tablet.

This installer flow has host-level automated tests, a completed security review, and
has been executed on the real device pair: the S25 was installed with v0.1.2 on
2026-11-06 and passed `bash tests/device-smoke.sh` 20/20, including a live browser
session. See [certification](certification.md).
