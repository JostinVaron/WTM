# Wireless Tactile Monitor (WTM)

**A low-latency, touch-enabled Android virtual-display streaming prototype.**

WTM is the project name for a tested S25-to-Galaxy Tab S6 Lite workflow. It creates a
2000 x 1200 Android virtual display on the phone, captures and encodes that display with
the scrcpy Android server, relays H.264 through a Python service running natively in
Termux, and renders it in Chrome on the tablet using WebCodecs. Pointer and keyboard
events travel in the reverse direction.

> **Project status: experimental, device-specific prototype.** This repository is a
> technical handoff and reproducibility specification, not a released Android app.
> There is no signed installer, supported device matrix, formal product security review,
> release build, or verified 60 FPS guarantee. The production path requires Termux and
> Wireless ADB.
> The Termux deployment tree and the separate local work copy differ; this handoff does
> not claim to be a source-code snapshot of either one.

## What is currently demonstrated

- The test pair is a Samsung Galaxy S25 (`SM-S931B`, Android 16 / API 36) and Galaxy Tab
  S6 Lite Wi-Fi (`SM-P613`, Android 14 / API 34).
- The phone's scrcpy virtual display is configured as 2000 x 1200 at 60 Hz. The
  encoder is configured for H.264, a 12 Mbps target and a maximum of 60 FPS.
- Short, relatively simple motion tests reached about 57.6-60 decoded FPS in Chrome, with
  measured mean capture-to-decoder latency around 23-26 ms and no client drops in some
  five-second windows.
- Detailed motion and app changes caused major regressions: the tablet fell to 13-14
  decoded FPS with 1.4-1.6 seconds of decoder delay and 65-66 dropped frames in
  five-second windows in one test. App-switch windows measured about 27-46 FPS.
- For this tablet, WebCodecs software decoding performed substantially better than the
  forced hardware path in the observed A/B test. The browser defaults to software.
- Touch/input forwarding and the virtual-display pipeline have been used on this pair,
  but there is no formal compatibility or long-duration reliability certification.

Measurements are conditional on scene complexity, Wi-Fi, device temperature, foreground
state, browser behavior, and battery/thermal management. See
[Performance and test evidence](docs/performance.md).

## Architecture at a glance

```text
S25 Android virtual display (2000 x 1200)
        │
        ├─ scrcpy-server 5.0.1 / Android MediaCodec / H.264
        │  abstract video + control sockets
        ▼
Termux ADB local forward (TCP 1234)
        ▼
Python 3 + aiohttp relay in Termux (HTTPS 8443, WebSocket)
        │
        │ Wi-Fi LAN
        ▼
Chrome on Tab S6 Lite (WebCodecs + canvas)
        │
        └─ touch / pointer / keyboard events return over WebSocket
           → relay → scrcpy control socket → virtual display
```

ADB is used to launch/manage the phone-side scrcpy server and to forward its local
abstract sockets to the Termux relay. The tablet's video path is IP over Wi-Fi; Bluetooth
is not used for video.

## Documentation

- [Architecture and lifecycle](docs/architecture.md)
- [Dependencies and installation inventory](docs/dependencies.md)
- [Hardware, operating-system, and network requirements](docs/requirements.md)
- [Performance measurements and acceptance criteria](docs/performance.md)
- [Current Termux deployment and reproduction guide](docs/deployment-termux.md)
- [Runtime release candidate (unpublished)](docs/runtime-release-candidate.md)
- [Security and privacy](SECURITY.md)
- [License status](NO-LICENSE.md)
- [Issue, milestone, and release policy](docs/project-governance.md)
- [GitHub publication/authentication](docs/publishing.md)
- [Contribution guide](CONTRIBUTING.md)

## Important limitations

1. **Termux is in the runtime path.** The relay, local ADB server/forward, logs, launcher,
   and watchdog are Termux processes. Force-stopping Termux or Android killing it can
   interrupt the stream. `nohup` and a wake lock do not make a process an Android system
   service.
2. **Wireless ADB must be available and authorized.** Android can disable Wireless
   Debugging or change its TCP port after a reboot. Saved pairing keys do not guarantee
   that the debugging service remains enabled.
3. **60 FPS is a cap, not an end-to-end guarantee.** The phone can produce near-60 FPS
   under some scenes while Chrome drops far below that rate under complex motion.
4. **The deployed prototype has no security boundary.** The prototype currently
   deployed on the S25 uses a self-signed certificate and an unauthenticated control
   endpoint capable of starting/stopping Termux scripts. Do not expose it to untrusted
   networks; see [Security](SECURITY.md). A hardened release candidate exists on branch
   `executable-release-candidate` (cookie auth, Origin/Host checks, TLS fail-closed,
   admin-only control) but is not published, not deployed and not independently
   reviewed; see [runtime release candidate](docs/runtime-release-candidate.md).
5. **This repository has no published runtime bundle.** The `main` branch carries the
   documentation handoff only. The release-candidate branch adds the relay source,
   browser clients, launcher/watchdog scripts, an idempotent Termux installer that
   downloads the official scrcpy server with a pinned checksum, and automated security
   tests — but it cannot be published until the owner chooses a license and the
   hardening passes independent review.

## Hardware requirements: what we can and cannot claim

The only tested baseline is the pair above. It is not scientifically valid to infer a
minimum RAM or CPU threshold from one device pair. The requirements document therefore
labels the observed baseline separately from provisional recommendations and unknowns.

The current source project has no approved open-source license. Until the owner selects
one and third-party notices are reviewed, do not redistribute implementation code or
build artifacts as an official WTM release. See [license status](NO-LICENSE.md).

## Repository status

- Public repository: [JostinVaron/WTM](https://github.com/JostinVaron/WTM)
- Version: **unreleased prototype / documentation baseline**
- Supported installations: **none formally**
- Latest tested configuration: **S25 + Tab S6 Lite, 2000 x 1200 / configured 60 FPS**
- Downloadable app: **not available**
- Public issue templates: available in `.github/ISSUE_TEMPLATE/`
- Release artifacts: **none**

See [Project governance](docs/project-governance.md) for what must be completed before
creating a public release.
