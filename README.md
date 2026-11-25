# Wireless Tactile Monitor (WTM)

**A low-latency, touch-enabled Android virtual-display streaming prototype.**

WTM is the project name for a tested S25-to-Galaxy Tab S6 Lite workflow. It creates a
2000 x 1200 Android virtual display on the phone, captures and encodes that display with
the scrcpy Android server, relays HEVC (H.265) video by default (H.264 selectable) through a Python service running natively in
Termux, and renders it in Chrome on the tablet using WebCodecs. Pointer and keyboard
events travel in the reverse direction.

## Quick start

Tested pair: Samsung Galaxy S25 (source phone, Termux + ADB over TCP) and Galaxy
Tab S6 Lite (viewer, Chrome). The package is a source installer — there is no APK.

**Recommended transport:** connect both devices to the source phone's hotspot
(the phone acts as its own AP; the tablet joins that network). Measured on
2026-11-09, the same pipeline and content class that delivered ~30 FPS over the
owner's reference router delivered sustained ~60 FPS over the hotspot with
`rtt=3 ms` (see [performance](docs/performance.md)). Router Wi-Fi remains
supported; it was the measured bottleneck in the reference network.

```sh
# 1. On the source phone, in Termux:
curl -L -o wtm.tar.gz \
  https://github.com/JostinVaron/WTM/releases/download/v0.4.0/wtm-runtime-0.4.0.tar.gz
tar -xzf wtm.tar.gz
cd wtm-runtime-0.4.0/runtime/termux
bash install.sh        # installs packages, verifies the scrcpy server, generates tokens

# 2. Start DeX with the one-tap wizard (Termux:Widget "DeX" shortcut, or):
bash ~/.shortcuts/DeX
#    The wizard recovers ADB after a reboot (guided Wireless Debugging
#    dialog if port 5555 is down), detects the hotspot IP, syncs server.env,
#    verifies the tablet, starts the service, launches the native WTM Viewer
#    on the tablet when ADB is available (and updates its saved URL), then
#    shows the URL + TLS fingerprint in a persistent dialog.

# 3. On the tablet, open the WTM Viewer app. The wizard updates its saved
#    URL and launches it; it auto-connects and checks the TLS fingerprint.
#    Browser fallback: open the URL shown by the wizard, compare the
#    fingerprint, and enter the viewer token from
#    ~/.config/wtm/auth-tokens.json (the wizard no longer prints tokens).
```

**Stop:** Termux:Widget "Exit-Dex" shortcut (or `bash ~/.shortcuts/Exit-Dex`).

Full walkthrough: [deployment guide](docs/deployment-termux.md) · what a release
contains: [releases](docs/releases.md) · verified evidence:
[certification](docs/certification.md).

> **Project status: experimental, device-specific prototype with a published,
> tested runtime.** The repository includes the hardened Termux runtime under
> [runtime/termux/](runtime/termux/) with an installer, automated tests and a
> completed independent security review; there is **no** signed Android app,
> supported device matrix, or verified 60 FPS guarantee. The on-device
> certification on the S25 was completed on 2026-11-06 (20/20 device checks, live browser session); see [certification](docs/certification.md). The production path requires
> Termux and ADB over TCP (local wireless debugging or USB); see the deployment guide.
> The pre-hardening prototype that used to run on the owner's phone predates the
> hardening published here; the S25 has since been migrated to the hardened
> runtime (v0.1.2, certified 2026-11-06) — see [SECURITY.md](SECURITY.md).

## What is currently demonstrated

- The test pair is a Samsung Galaxy S25 (`SM-S931B`, Android 17 / API 37) and Galaxy Tab
  S6 Lite Wi-Fi (`SM-P613`, Android 14 / API 34).
- The phone's scrcpy virtual display is configured as 2000 x 1200 at 60 Hz. The
  encoder is configured for HEVC (H.265) by default since v0.3.0 (H.264
  selectable via `VIDEO_CODEC=h264` in `server.env`), a 12 Mbps target and a
  maximum of 60 FPS.
- Measured 2026-11-09 on the phone-hotspot transport: real 4K60 content was
  delivered at sustained ~59.5–60.1 decoded FPS with one dropped frame in
  ~2.5 minutes and constant `rtt=3 ms`; the encoder bitrate reached the 12 Mbps
  cap (9.6–12.9 Mbps observed). The same pipeline over the owner's reference
  router delivered ~30 FPS — the router was the transport bottleneck, not the
  pipeline (see [performance](docs/performance.md), 2026-11-09).
- Earlier router Wi-Fi tests (2026-11-06): simple motion reached ~57.6-60
  decoded FPS with 23-26 ms mean capture-to-decoder latency in short windows;
  detailed/high-motion content collapsed the tablet to 13-14 decoded FPS with
  1.4-1.6 s of decoder delay in one test. App-switch windows measured about 27-46 FPS.
- For this tablet, WebCodecs software decoding performed substantially better than the
  forced hardware path in the observed A/B test. The browser defaults to software.
- **Post-reboot start is verified on this pair (2026-11-10): the DeX wizard
  recovered the session after 4 of 4 real reboots of the S25** (issue #15),
  including full adbd fallback to USB mode and hotspot IP changes; the only
  manual step is the guided Wireless Debugging dialog when `adb tcpip 5555`
  is unreachable. The wizard prints the exact URL and TLS fingerprint after
  every start, because the hotspot IP changes on every reboot.
- Touch/input forwarding and the virtual-display pipeline have been used on this pair,
  but there is no formal compatibility or long-duration reliability certification.
- Bluetooth mouse routing to the virtual display has been restored and verified working
  on this pair (2026-11-07): the phone's paired physical mouse drives the virtual
  display only — no ghost input on the phone screen, with the real Samsung cursor.
  See [bluetooth-mouse-routing.md](docs/bluetooth-mouse-routing.md).
- **S Pen end-to-end over UHID** (2026-11-25): fingers keep using `INJECT_TOUCH`;
  the pen is a real HID digitizer (`WTM Pen`) with 4096 pressure levels,
  tilt/orientation, eraser and hover. Palm rejection runs in the viewer, the
  relay and the display server, and `ACTION_HOVER_EXIT` releases the pen from
  the phone's input range immediately so touch/mouse stay responsive after
  drawing. PenProbe measures tool type, pressure, tilt and flags on the virtual
  display. See [s-pen-input.md](docs/s-pen-input.md). This is engineering
  verification on this pair, not a hardware certification.
- **Current pinned profile (2026-11-25)**: `WTM_PIN_PROFILE=1` applies
  2000x1200@60 and 35 Mbps only after verifying the live scrcpy cmdline
  (`cap_ack: applied=true`). On the current pair the viewer HUD reports about
  5 ms render latency, ~4 ms RTT and zero `rdrop`/`qdrop` on a static probe;
  this is video-path telemetry, not a pen-to-photon measurement.
- HEVC decoding capability on the tablet was probed and confirmed (2026-11-08, Chrome
  156), and the H.264-vs-HEVC A/B was then **measured on this pair (2026-11-09)**:
  for the tested content class, HEVC decoded ~30% faster with ~3x lower jitter peaks
  and zero sustained drops. Since v0.3.0 the shipped default is HEVC, after a
  long production session on this pair validated it; the evidence and caveats are in
  [hevc-study.md §7](docs/hevc-study.md).

Measurements are conditional on scene complexity, transport, device temperature, foreground
state, browser behavior, and battery/thermal management. See
[Performance and test evidence](docs/performance.md) and the
[optimization backlog](docs/optimization.md).

## Architecture at a glance

```text
S25 Android virtual display (2000 x 1200)
        │
        ├─ scrcpy-server 5.0.1 / Android MediaCodec / HEVC (default) or H.264
        │  abstract video + control sockets
        ▼
Termux ADB local forward (TCP 1234)
        ▼
Python 3 + aiohttp relay in Termux (HTTPS 8443, WebSocket)
        │
        │ phone hotspot (recommended) or Wi-Fi LAN
        ▼
Chrome on Tab S6 Lite (WebCodecs + canvas)
        │
        └─ touch / pointer / keyboard events return over WebSocket
           → relay → scrcpy control socket → virtual display
```

ADB is used to launch/manage the phone-side scrcpy server and to forward its local
abstract sockets to the Termux relay. The tablet's video path is IP over the phone
hotspot or Wi-Fi; Bluetooth is not used for video.

## Documentation

- [Architecture and lifecycle](docs/architecture.md)
- [Dependencies and installation inventory](docs/dependencies.md)
- [Hardware, operating-system, and network requirements](docs/requirements.md)
- [Performance measurements and acceptance criteria](docs/performance.md)
- [Performance and optimization backlog](docs/optimization.md)
- [Codec study: H.264 vs H.265/HEVC (probe + measured A/B)](docs/hevc-study.md)
- [Current Termux deployment and reproduction guide](docs/deployment-termux.md)
- [Runtime release candidate](docs/runtime-release-candidate.md)
- [Bluetooth mouse routing (historical subsystem)](docs/bluetooth-mouse-routing.md)
- [Certification and tests](docs/certification.md)
- [Threat model](docs/threat-model.md)
- [Releases and packages](docs/releases.md)
- [Security and privacy](SECURITY.md)
- [Licensing](LICENSING.md)
- [Code of conduct](CODE_OF_CONDUCT.md)
- [Instructions for AI assistants](AGENTS.md)
- [Issue, milestone, and release policy](docs/project-governance.md)
- [GitHub publication/authentication](docs/publishing.md)
- [Contribution guide](CONTRIBUTING.md)

## Important limitations

1. **Termux is in the runtime path.** The relay, local ADB server/forward, logs, launcher,
   and watchdog are Termux processes. Force-stopping Termux or Android killing it can
   interrupt the stream. `nohup` and a wake lock do not make a process an Android system
   service.
2. **ADB must be available and authorized.** Android can disable Wireless
   Debugging or change its TCP port after a reboot. Saved pairing keys do not guarantee
   that the debugging service remains enabled. A USB connection is an alternative ADB
   transport where supported. Since v0.4.0 the DeX wizard automates post-reboot
   ADB recovery (fixed port 5555, guided Wireless Debugging dialog otherwise;
   verified 4/4 on the S25), but it cannot enable Developer Options or accept
   the pairing dialog for the user.
3. **60 FPS is a cap, not an end-to-end guarantee.** The phone can produce near-60 FPS
   under some scenes while Chrome drops far below that rate under complex motion. The
   2026-11-09 hotspot measurement is one short run on one pair, not a soak.
4. **Hardened runtime deployed and certified.** The hardened runtime in
   [runtime/termux/](runtime/termux/) (cookie auth, Origin/Host checks, TLS fail-closed,
   admin-only control) was independently reviewed before publication. On 2026-11-06 it
   was deployed on the S25 (v0.1.2) and passed the on-device certification: 20/20
   checks including a live browser session (login with certificate fingerprint, video
   decode, injected input). Keep it off untrusted networks regardless;
   see [Security](SECURITY.md) and [certification](docs/certification.md).
5. **No signed Android app exists.** `main` includes the relay source, browser
   clients, launcher/watchdog scripts, an idempotent Termux installer that downloads
   the official scrcpy server with a pinned checksum, and automated security tests
   (26/26 passing) plus a completed independent security review and the completed
   on-device certification. This is a source-available prototype, not a Play-Store
   style product. See [certification](docs/certification.md).
6. **The tablet browser must support WebCodecs.** The web client decodes video
   exclusively via the WebCodecs API (`VideoDecoder`) — there is no MSE or
   `<video>` fallback. **Chrome (or another Chromium-based browser with WebCodecs
   enabled) is required; Firefox does not support WebCodecs H.264 decoding and shows a
   black screen with `0 fps, 0.0 Mbps` and the `sin WebCodecs` overlay note.** On a
   Galaxy Tab S6 Lite, updated Chrome works; keep the tablet's Chrome up to date.
   The HEVC path has an additional constraint: on Android, Chrome exposes HEVC only
   through platform (hardware) decoders — there is no software fallback — so the HEVC
   default additionally requires a hardware HEVC decoder (confirmed present on the
   tested tablet by the 2026-11-08 probe; see [hevc-study.md](docs/hevc-study.md)).
7. **The virtual display starts empty (black), and vendor launchers may refuse to run
   on it.** An Android virtual display is created blank. The runtime launcher script
   starts the HOME intent on the newly created display; on Samsung One UI the stock
   launcher (`com.sec.android.app.launcher`) rejects launching on a virtual display
   from the shell (`SecurityException ... launchDisplayId`), so the script falls back
   to launching the exported Settings activity as initial content. Apps that set
   `FLAG_SECURE` (banking, DRM apps) render as black by design and cannot be captured.
   In addition, the One UI Recents/task-switcher does not work on virtual displays: the
   recents view opens on the default display and appears empty, so task switching must
   use a keyboard task-switcher shortcut or relaunching apps.
8. **Tablet input is touch-and-injection; the phone-paired physical mouse is routed
   natively when available.** Touch on the tablet streams touch events through the
   browser session into the scrcpy control socket; pointer events (mouse paired to
   the tablet, or touchpad) are injected with `POINTER_ID_MOUSE` and show a cursor in
   the virtual display. For a Bluetooth mouse paired with the **phone**, the runtime's
   primary path is the native input-device association
   (`route-input.sh` + `jvm/InputAssoc`/`InputFocus` via `app_process`, calling the
   Android 14+ hidden `IInputManager.addUniqueIdAssociationByDescriptor` /
   `setDisplayIdForPointerIcon`): Android itself re-targets the mouse to the virtual
   display, so there is no ghost input on the phone screen and the real system cursor
   renders inside the DeX. This was verified working on the S25 on 2026-11-07; it uses
   hidden APIs and must be re-applied on every session start (the runtime does this
   automatically). If the association cannot be applied (jars missing or the API
   absent on an Android update), the runtime falls back to `mouse-route.py`
   (`getevent` → machine-local `/api/mouse` endpoint → injection), which works but
   without exclusivity: the pointer may also move on the phone's physical screen.
   Disable all mouse routing with `MOUSE_ROUTE=0` in `server.env`. See
   [bluetooth-mouse-routing.md](docs/bluetooth-mouse-routing.md).
9. **Hotspot + Wi-Fi + mobile data changes the addressing model.** With the phone's
   hotspot active alongside other networks, the phone has multiple interfaces with
   IPs (`swlan0` for the hotspot, `wlan0` for Wi-Fi, `rmnet` for data), and the
   Termux `ip` binary cannot read interface addresses on modern Android (netlink is
   restricted for apps) — so the installer and `update-network.sh` detect interfaces
   through the adb loopback connection (`adb shell ip`), preferring the hotspot
   interface (`swlan0`) first. Never hardcode the phone's hotspot address; when in
   doubt, pass it explicitly with `WTM_ALLOWED_HOSTS` (install) or as an argument
   (`update-network.sh 10.x.x.x`). The hotspot path was verified working on
   2026-11-09, but hotspot client isolation, DHCP lease behavior, and battery cost
   of a sustained AP role are not characterized.

## Hardware requirements: what we can and cannot claim

The only tested baseline is the pair above. It is not scientifically valid to infer a
minimum RAM or CPU threshold from one device pair. The requirements document therefore
labels the observed baseline separately from provisional recommendations and unknowns.

The current source project is licensed for non-commercial use: software under
[PolyForm Noncommercial 1.0.0](LICENSE) and documentation under
[CC BY-NC 4.0](LICENSE-docs.txt). These are source-available licenses, not
OSI-approved "open source"; commercial use requires separate permission.
See [licensing](LICENSING.md) and third-party notices in
[dependencies](docs/dependencies.md).

## Repository status

- Public repository: [JostinVaron/WTM](https://github.com/JostinVaron/WTM)
- Version: **v0.4.0** — published runtime, source-only (see
  [Releases](https://github.com/JostinVaron/WTM/releases))
- License: software [PolyForm Noncommercial 1.0.0](LICENSE), documentation
  CC BY-NC 4.0](LICENSE-docs.txt) — see [Licensing](LICENSING.md)
- Supported installations: **none formally**
- Latest tested configuration: **S25 + Tab S6 Lite, 2000 x 1200 / configured 60 FPS,
  phone-hotspot transport**
- Downloadable app: **not available**; source-only Termux installer in `runtime/termux/`
- Automated tests: 26/26 security/live suites passing (host), plus the 18-test HEVC
  bitstream suite (18/18 verified on-device in Termux (S25) and integrated in CI); independent
  security review completed
- On-device certification: **completed 2026-11-06, 20/20 PASS** (see [certification](docs/certification.md))
- Post-reboot start: **verified 4/4 on the S25 (2026-11-10) via the DeX wizard** (issue #15)
- CI: `.github/workflows/ci.yml`; releases: `.github/workflows/release.yml`
- Public issue templates: available in `.github/ISSUE_TEMPLATE/`
- Release artifacts: source packages under
  [Releases](https://github.com/JostinVaron/WTM/releases), built from `v*` tags

See [Project governance](docs/project-governance.md) for what must be completed before
creating a public release.
