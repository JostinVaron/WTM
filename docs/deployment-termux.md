# Current prototype deployment: Termux + Wireless ADB

This procedure describes the existing prototype, not a one-click installer or a
Termux-independent product. It assumes the project has been installed manually in
`$HOME/dex` on the source phone.

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
