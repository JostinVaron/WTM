# Hardware, software and network requirements

## Tested device baseline

All values below are read from the two devices used for this prototype on 2026-11-06.
They describe one test pair; they are not a validated compatibility matrix.

| Role | Model / identifier | SoC property | CPU | Android | Physical display | Reported RAM |
|---|---|---|---|---|---|---|
| Source / server | Samsung Galaxy S25, SM-S931B | `SM8750`; `ro.hardware=qcom` | 8 logical CPU IDs (`0-7`); ARM64 | Android 16, API 36 | 1080 x 2340; supported refresh modes include 60, 80 and 120 Hz; the observed active mode was 60 Hz | `/proc/meminfo` `MemTotal` 11,380,216 kB (~10.85 GiB) |
| Viewer | Samsung Galaxy Tab S6 Lite Wi-Fi, SM-P613 | `SM7125`; `ro.hardware=qcom` | 8 logical CPU IDs (`0-7`); ARM64 | Android 14, API 34 | 1200 x 2000; observed 60 Hz | `/proc/meminfo` `MemTotal` 3,590,124 kB (~3.42 GiB) |

The active virtual display is configured separately as 2000 x 1200 at 60 Hz and 240 dpi.
The phone panel resolution and tablet panel resolution do not change this virtual-display
mode. The tablet browser scales the stream to fit its physical display.

GPU model, codec block throughput, sustained clocks, RAM pressure over long sessions,
device skin-temperature thresholds and battery health were not fully characterized.
`ro.soc.model` values are recorded as returned; marketing chipset/GPU names are
intentionally not inferred from them.

## Minimum, recommended and unknown values

The project has only been exercised on the baseline pair above. Therefore:

- **Empirically validated minimum:** unknown. No lower-RAM or lower-core device matrix
  has been run.
- **Conservative prototype target:** use a source device at least in the tested S25 class
  (64-bit ARM Android, 8 logical cores, ~11 GiB reported MemTotal) and a viewer at least
  in the tested tablet class (~3.4 GiB MemTotal, 60 Hz display, current Chrome/WebCodecs).
  This is a conservative planning target, not a proven minimum.
- **Recommended for future testing:** at least 8 GB marketed RAM on the source and 4 GB
  marketed RAM on the viewer, modern hardware H.264 or HEVC encoding on the source, a
  current Chrome release with WebCodecs, and thermal headroom. This recommendation is an
  engineering estimate and needs testing on additional devices.
- **Processor:** 64-bit ARM Android is the only observed architecture. Eight logical
  CPUs are the tested baseline; no lower-core threshold has been established.
- **Graphics:** no specific GPU can be declared required from available measurements.
  The source requires an Android H.264 or HEVC encoder usable by scrcpy; the shipped
  default is hardware HEVC. On the viewer, decode results are codec-dependent: forced
  hardware H.264 decoding was catastrophic on this tablet, while platform HEVC decoding
  via WebCodecs at the production bitrate stayed within the per-frame decode budget in
  the 2026-11-09 measurements (see [codec study](hevc-study.md)).

Do not advertise these estimates as guaranteed compatibility. A future release needs a
published device matrix with pass/fail results.

## Latency goal and measurement scope

The project has previously stated an aspirational goal of about 20 ms average
capture-to-decoder latency. This is not a validated requirement or release gate. The
available browser metric does not include physical display scanout and must not be
presented as photon-to-photon latency. See [performance evidence](performance.md).

## Operating system and app requirements

### Source phone

- Samsung Android 16/API 36 is the observed baseline; lower versions are unverified.
- Termux installed natively (not only inside PRoot) with the project files and runtime
  packages documented in [dependencies](dependencies.md).
- **ADB from Termux to the phone itself is mandatory.** adb is the only rootless mechanism
  Android provides to create the virtual display and launch the scrcpy server; the
  pipeline cannot start without it. Two supported modes:
  - **adbd TCP mode on the fixed port 5555** (`adb tcpip 5555`): stable address and port,
    but it **does not survive phone reboots** — Android reverts adbd to USB mode on every
    boot, so it must be re-seeded after each reboot via USB (one manual step; see the
    post-reboot runbook in [deployment](deployment-termux.md)).
  - **Wireless Debugging** (Android 11+): the toggle persists across reboots, but the
    listening port is random per boot and is discovered automatically by
    `runtime/termux/find-adb-port.py`; pairing is one-time. On the tested One UI build the
    toggle is turned off when Wi-Fi client connectivity drops, which makes it unreliable
    while the phone hosts the hotspot.
  - Automated recovery and honest abort reporting are tracked in
    [issue #15](https://github.com/JostinVaron/WTM/issues/15). Until it ships, after every
    source-phone reboot exactly one manual step is unavoidable without root.
- Android background/battery policy must allow Termux to run; Termux:Boot and
  Termux:Widget are optional integrations, not OS-level service guarantees.
- Android must permit the scrcpy shell process to create the requested display and use
  MediaCodec on that model/build.

### Viewer tablet

- Android 14/API 34 is the observed baseline; other Android builds are unverified.
- Chrome with WebCodecs enabled and the site loaded in a secure context (HTTPS).
- A native MediaCodec viewer to replace Chrome is planned
  ([issue #16](https://github.com/JostinVaron/WTM/issues/16)) but is not shipped; until
  then Chrome/WebCodecs remains the viewer requirement.
- A touch display; pointer/touch interaction is sent through the web client.
- The current local TLS certificate is self-signed; users must not train themselves to
  bypass arbitrary certificate warnings. A release must provide authenticated pairing
  and a trusted certificate or another secure origin design.

## Network

- Both devices need reliable local IP connectivity to the relay. The current lab layout
  used the S25 as the relay host and the tablet as an HTTPS/WebSocket client.
- Current production encoder configuration is **hardware HEVC (H.265) at 35 Mbps CBR**
  (`i-frame-interval=1`), selected on 2026-11-09 by a descending bitrate ladder measured
  on real hardware: at 70 Mbps the viewer's decoder saturated on complex 4K60 content
  (sustained decode_ms 165-281 ms against a 16.7 ms/frame budget) while at 35 Mbps decode
  stayed in budget (18-22 ms) with no perceived quality loss on the 2000x1200 viewer
  panel; the link itself sustained ~68 Mbps with rtt 3-5 ms. The earlier 12 Mbps H.264
  default is superseded; H.264 remains a documented rollback. This telemetry is evidence
  for this device pair, not a network capacity guarantee for other scenes or hardware.
- A stable 5 GHz Wi-Fi LAN or Wi-Fi Direct/hotspot arrangement is a reasonable target
  for testing; the exact AP, channel, interference and sustained throughput were not
  recorded, so this is not a certified requirement.
- The minimum usable bandwidth, packet-loss threshold, jitter tolerance and latency under
  congestion are not yet established.
- Bluetooth is not part of the tested pipeline. It may be suitable for low-rate control
  or discovery, but Bluetooth tethering/PAN has not been measured for WTM and is not
  recommended as the 2K60 video transport.

## Physical and thermal use

The source phone both renders/captures and encodes the virtual display. Sustained screen
capture, encoding, Wi-Fi, charging and high-motion content produce heat. Tests stopped
when the phone surface reached a conservative 43 °C threshold, despite external cooling.
The threshold is a test stop rule, not a Samsung safety specification. Stop testing if
Android reports severe thermal status, the device becomes uncomfortably hot, charging
behaves abnormally, or the battery swells/damages.

Use the manufacturer's charging and thermal guidance. A cooling fan does not negate
device thermal sensors or battery safety protections.

## Capacity formula for future hardware sizing

The uncompressed 2000 x 1200 frame contains 2.4 million pixels. At 60 frames/s the
pipeline must process up to 144 million pixels/s before accounting for capture, color
conversion, encode/decode, browser copy/composition and display presentation.
This arithmetic is not a benchmark. Future sizing should be based on sustained measured
FPS, queue depth, tail latency, dropped frames, power and thermal equilibrium on each
candidate device.
