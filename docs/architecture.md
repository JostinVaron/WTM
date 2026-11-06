# WTM architecture and lifecycle

## Scope and terminology

WTM names the prototype that presents an Android virtual display from a Galaxy S25 on a
Galaxy Tab S6 Lite. In this document:

- **Phone / source**: Samsung Galaxy S25 SM-S931B.
- **Tablet / viewer**: Samsung Galaxy Tab S6 Lite Wi-Fi SM-P613.
- **Virtual display**: an Android display created by the scrcpy server, configured as
  2000 x 1200 pixels at 240 dpi and 60 Hz.
- **Relay**: the Python `server.py` process in Termux.
- **Viewer**: the web client in Chrome, using WebSocket, WebCodecs and a canvas.

“DeX” is used conversationally for the phone-side desktop experience. The technically
verified implementation primitive is a scrcpy-created Android virtual display and an
Android display/task environment. Do not assume this is equivalent to Samsung's supported
DeX-over-wireless product or a native external-monitor mode.

## Processes and data flow

### 1. Start request on the phone

The Termux:Widget shortcut
`$HOME/.shortcuts/start-dex.sh` obtains a Termux wake lock where available and runs
`$HOME/dex/dex.sh`. The script identifies the S25 by `ro.product.model=SM-S931B`, rather
than assuming that the first connected ADB device is the phone.

The start script verifies that the scrcpy server and the local HTTP API are available.
`stop-dex.sh` writes a stop marker, removes the video forward, terminates the bridge and
attempts to terminate the phone-side display process.

### 2. ADB and phone-side scrcpy server

The ADB client installed in Termux connects to the phone's Android Debug Bridge endpoint.
On the tested phone this was Wireless Debugging over the phone's Wi-Fi interface. ADB is
used to:

- query the phone model and Android properties;
- upload `scrcpy-server` to `/data/local/tmp/scrcpy-server.jar` if absent;
- launch `com.genymobile.scrcpy.Server` through Android `app_process`;
- establish a host-side `adb forward` from TCP port 1234 to the scrcpy abstract socket;
- carry the scrcpy control channel used for input injection.

The launcher requests `new_display=2000x1200/240`, `raw_stream=true`,
`max_fps=60`, `video_bit_rate=12000000`, `audio=false`, and H.264 codec options with
approximately one-second keyframe intervals. These are configuration values, not
performance guarantees.

### 3. Capture, encode, and forwarding

The scrcpy Android server captures the virtual display and encodes H.264 with Android's
MediaCodec implementation. Video and control are conveyed through scrcpy's abstract local
sockets. The Termux ADB daemon owns the TCP forward; the stream relay connects to local
TCP port 1234 and parses the H.264 Annex-B stream into access units.

If Termux/ADB is stopped, the forward disappears even if an Android-side process briefly
remains alive. Consequently, the phone-side display producer alone is not a complete,
independent session.

### 4. Relay and viewer

`server.py` is an asyncio/aiohttp process in Termux. In scrcpy mode it:

- reads framed H.264 from the forwarded socket;
- extracts SPS/PPS and sends WebCodecs decoder configuration to the client;
- converts access units to AVCC framing and relays them over a WebSocket;
- tracks basic server/client counters and accepts input/heartbeat messages;
- passes pointer, touch, key and scroll messages to the scrcpy control connection.

The service also serves a web UI/API over HTTPS (port 8443 in the current configuration).
The certificate currently used by the local installation is self-signed. The tablet
opens the HTTPS page in Chrome; WebCodecs requires a secure context. The client decodes
H.264 and draws frames onto a full-screen canvas. Current default decoder preference is
`prefer-software`, based on the observed tablet A/B test.

The active server binds the web service to all interfaces and exposes unauthenticated
WebSocket input and HTTP control and log endpoints. The control operation can start/stop
helper scripts in the Termux context; the log endpoint returns recent local diagnostics.
This is a serious pre-release security issue; the prototype must remain on a trusted
isolated LAN. The current launcher supplies local TLS material, but the server can fall
back to plain HTTP if TLS is not configured, so port 8443 alone does not prove that a
connection is encrypted. Do not include the active server source in a public release
until these interfaces are redesigned, authenticated and reviewed. See
[Security](../SECURITY.md).

### 5. Return input path

Pointer/touch/keyboard data travels from the tablet page to the relay over WebSocket, then
over scrcpy's binary control channel to Android's virtual display. The exact Android
focus/task behavior depends on Samsung's Android/DeX implementation and the app running
on the virtual display. Physical edge gestures on the S25 were not formally tested as
part of the documented benchmark.

An optional helper also associates a paired Bluetooth mouse with the virtual display
using Android input-manager internals. This is a separate pointer-input feature; it does
not carry the video stream and is not required for touch input sent from the tablet.
The helper depends on device-specific input descriptors and Android hidden APIs, so it
must be treated as experimental.

## Lifecycle and failure boundaries

| Component | Owner/runtime | What keeps it alive | Failure effect |
|---|---|---|---|
| Termux widget | Android app shortcut | User taps it; Termux must be allowed to run | No start request if app/shortcut is stopped or unavailable |
| ADB client/daemon and forward | Termux process | Termux process lifetime and active ADB transport | Relay cannot reach scrcpy sockets if daemon/forward disappears |
| scrcpy server | Android `app_process`, shell UID | Android process policy; scrcpy session and sockets | Virtual display and H.264 source stop |
| `server.py` relay | Python in Termux | Termux process and Android background policy | HTTPS/WebSocket viewer disconnects |
| watchdog | Bash/Python helpers in Termux | Termux process; optional Termux:Boot startup | Can retry only while it is alive and ADB can be re-established |
| Chrome viewer | Android app on tablet | Browser foreground/background policy | Viewer pauses or disconnects; server may remain up |

`nohup` only detaches a process from a terminal's hangup signal. A Termux wake lock can
reduce CPU sleep; neither prevents Android force-stop, package restrictions, process
reclamation, reboot, a lost Wireless Debugging service, or a changing ADB port.

The project has a Termux:Boot script that attempts to start the watchdog at boot. This
does not enable Wireless Debugging, renew user consent, or guarantee a network endpoint.
Boot behavior also depends on the Termux:Boot app being installed, opened at least once,
unrestricted by battery policy, and permitted by the OS.

## Network topology

The current arrangement has two different transports:

1. ADB over the phone's local/Wi-Fi debugging endpoint is used for device administration
   and the local socket forward.
2. The tablet browser connects to the relay over the local IP network using HTTPS and a
   WebSocket.

The ADB TCP endpoint and the viewer-facing HTTPS endpoint are separate services. Bluetooth
may be used by an optional paired mouse helper, but is not in the video path. No benchmark
has demonstrated Bluetooth as a viable replacement for the video transport.

## Source-of-truth and deployment drift

The Termux deployment tree under `$HOME/dex` and the separate Linux
`remote-display/` work copy are not the same version. The work copy contains historical
Xvfb/PRoot tools and experimental files and must not be deployed without a file-by-file
comparison. This documentation describes the active Termux architecture and separately
labels historical paths. The documentation repository does not publish runtime code.

## Target architecture for a Termux-independent product

A robust downloadable product would need a native Android component, rather than a shell
widget, as the lifecycle owner:

- a user-started Android foreground service with an explicit persistent notification;
- capture through a supported Android API (MediaProjection consent is user-mediated and
  must be evaluated against the desired virtual-display/DeX behavior);
- a documented encoder, decoder, display, input-injection and permission model;
- a supported LAN transport and authenticated pairing;
- recovery and reconnect behavior that does not rely on ADB `forward`;
- explicit behavior on screen lock, reboot, battery saver, force-stop and permission
  revocation.

A foreground service improves survival of ordinary background process pressure; it is
not immortal and cannot bypass a user force-stop or Android's permission model. The
ability to create an independent DeX-like virtual desktop and inject input without ADB
or privileged permissions remains an unresolved product/OS feasibility question.
