# Current prototype deployment: Termux + Wireless ADB

This procedure describes the existing prototype, not a one-click installer or a
Termux-independent product. It assumes the project has been installed manually in
`$HOME/dex` on the source phone.

A release-candidate installer and hardened runtime exist on branch
`executable-release-candidate`; they are **not published and not deployed**. See
[runtime release candidate](runtime-release-candidate.md) for what changed and what
still blocks publication.

## Important: this repository cannot deploy the service by itself

The public repository is a documentation-only handoff. The following runtime files are
not included, so cloning this repository does not create a runnable WTM installation:

- `$HOME/.shortcuts/start-dex.sh` and `$HOME/.shortcuts/stop-dex.sh`;
- `$HOME/.termux/boot/01-dex.sh`;
- `$HOME/dex/dex.sh`, `scrcpy-phone.sh`, `server.py`, `index.html`, `panel.html`,
  `route-input.sh`, `watchdog.sh` and `find-adb-port.py`;
- the scrcpy server artifact and per-install TLS certificate/key.

These are described as components of the separately installed test setup. No verified
installer, clean-device setup procedure, or safe redistribution bundle is available.
Do not treat example paths or commands below as proof that the omitted files can be
obtained from this repository.

## Prerequisites

1. The source phone and tablet are on a trusted local network.
2. Termux is installed on the S25 and Android battery/background restrictions have been
   reviewed.
3. ADB, Python and `aiohttp` are installed in Termux.
4. The scrcpy 5.0.1 server artifact is present at the project path and its redistribution
   terms have been checked.
5. The S25's Developer Options and Wireless Debugging are enabled. Pair the ADB client
   once with the Android pairing flow; do not publish pairing keys.
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

The equivalent current bridge page is:

```text
https://<S25-LAN-IP>:8443/
```

The installed prototype uses a self-signed certificate. The certificate warning is not
a production trust model; do not disable certificate validation globally.

## Stop

The Termux:Widget stop shortcut is:

```text
$HOME/.shortcuts/stop-dex.sh
```

It marks the watchdog as intentionally stopped, removes the ADB forward, terminates the
scrcpy display process where reachable, stops the relay and releases the wake lock.
Verify that `/api/status` no longer responds and no scrcpy virtual display remains.

## Boot and recovery behavior

`$HOME/.termux/boot/01-dex.sh` attempts to obtain a wake lock and start
`$HOME/dex/watchdog.sh`. The watchdog looks for the S25 among ADB devices and scans for
the local ADB TCP service when missing. It restarts the bridge only when the phone is
available.

This is best-effort recovery. It cannot:

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

- Wireless Debugging disabled or port changed.
- ADB authorization revoked.
- Termux force-stopped, reclaimed, updated or battery-restricted.
- Wi-Fi changes IP/subnet or isolates clients.
- Relay killed while the viewer still has a WebSocket.
- scrcpy server exits, virtual display is removed or Android resets the encoder.
- Tablet Chrome sleeps, reloads or loses WebCodecs state.

Do not claim restart-proof behavior unless each case is exercised and measured on the
published support matrix.

## Release-candidate installer (unpublished)

On the `executable-release-candidate` branch, `runtime/termux/install.sh` is an
idempotent Termux installer that:

1. verifies it runs in Termux on the source phone and detects the LAN IP for the
   allowed host list (override with `WTM_ALLOWED_HOSTS`);
2. installs missing Termux packages (`python`, `aiohttp`, `openssl`, `curl`);
3. copies the package sources into `$HOME/dex`;
4. downloads the official scrcpy server v5.0.1 and verifies SHA-256
   `764eb6f79811d5211fe9df341120882ba9994c7a61b897d7bf3fb662e53bc536`;
5. generates per-install viewer/admin tokens, a TLS certificate/key, and a
   `server.env` under `~/.config/wtm/` (never overwriting existing credentials);
6. installs Termux:Widget shortcuts and the Termux:Boot hook.

After installation the bridge requires a browser login: open
`https://<phone-ip>:8443/`, accept the per-install certificate in Chrome, and enter one
of the generated tokens. The admin token unlocks the control panel
(`https://<phone-ip>:8443/panel`); the viewer token allows watching and input but not
start/stop actions. Local health checks use the unauthenticated loopback-only
`https://127.0.0.1:8443/healthz`.

This flow has not been exercised on the real device pair yet. Until the license and
review gates in [runtime-release-candidate](runtime-release-candidate.md) are met, do
not publish or promote it as a supported installation path.
