# Dependencies and installation inventory

This is an inventory of the tested Termux prototype, not a promise that every dependency
is packaged or installed by a single installer.

## Runtime components

| Component | Evidence / observed version | Required role |
|---|---|---|
| Samsung Android on source phone | Android 16, API 36 on SM-S931B | Virtual display, MediaCodec, `app_process`, ADB daemon and scrcpy server |
| Samsung Android on viewer tablet | Android 14, API 34 on SM-P613 | Chrome/WebCodecs client and touch display |
| Termux | Native Android app on the S25; active project at `$HOME/dex` | Hosts shell scripts, ADB client, relay and TLS listener |
| Termux Python | Python 3.14.8 observed | Runs `server.py` |
| `aiohttp` | 3.14.5 observed | Async HTTP, HTTPS and WebSocket server |
| Android Debug Bridge | Android-tools / ADB 37.0.0 observed | Device discovery, server launch, control and TCP-to-abstract-socket forwarding |
| `curl` / OpenSSL | 8.23.0 / 3.6.5 observed | Health checks and TLS support |
| Bash, coreutils, procps, awk/sed/grep | Used by launcher/watchdog scripts | Process discovery, logs and orchestration |
| scrcpy Android server | Version 5.0.1 in the active implementation | Virtual display capture, H.264 encoding and input/control protocol |
| Chrome on Android | Must provide WebCodecs in a secure context | H.264 decode and canvas rendering |
| Bash and Android shell tools | Used by launcher scripts | `bash`, `awk`, `grep`, `sed`, `tr`, `ps`, `setsid`, `app_process`, `am`, `wm`, `dumpsys`, `input` and related utilities |
| `curl` | Used by scripts for health checks and local HTTPS requests | Startup/status checks |
| Termux:Widget | Optional UI integration, if installed and granted shortcut access | Start/stop one-tap shortcuts |
| Termux:Boot | Optional boot integration | Launches a watchdog after Android boot, subject to OS policy and ADB availability |
| Termux:API command package | 0.60.0 observed | Provides optional `termux-wake-lock`, `termux-wake-unlock` and toast commands |
| Termux:API Android companion app | Not installed on the observed S25 | Required by Android for API plug-in actions such as toast; without it those actions are unavailable |
| Bluetooth mouse association helper | Experimental Java/app_process helper in active scripts | Optional routing of a paired physical mouse; device-descriptor- and Android-version-dependent, not a video dependency |

Termux:Widget and Termux:Boot Android apps were present on the observed S25. The optional
Termux:API companion Android app was not present even though the Termux command package
was installed.

Versions above were read from the active installation on 2026-11-06. They are an
environment snapshot, not tested minimum versions.

## ADB-specific requirements

- Wireless Debugging must be enabled on the source phone and the Termux ADB client must
  be authorized.
- Pairing is distinct from connecting: a saved pairing key does not imply that
  Wireless Debugging is currently active or that the current connect port is unchanged.
- The project uses ADB local forwarding from TCP 1234 to a `localabstract:scrcpy_...`
  socket. This forward is owned by the Termux-side ADB server.
- Optional physical Bluetooth mouse routing uses Android input-manager behavior. It is
  not Bluetooth PAN/tethering and does not carry H.264 frames.
- Android USB debugging could be used for administration if a compatible host/cable
  arrangement exists, but it does not remove the need for the relay or define a USB video
  path to the tablet.
- Root is not part of the observed setup. The server is launched with Android's shell
  UID through ADB.
- Never put an ADB private key, pairing secret, Wi-Fi password or other credential into
  the repository.

## Build and test tools present in the project

The source directory also contains historical or experimental components that are not
the active scrcpy path:

- C/X11 components such as `capd.c`, `anim60.c`, `dltest.c` and `mcprobe.c`;
- Java/Android probes and encoders under `jvm/`;
- X11/Xvfb, FFmpeg and x264 helpers for the older desktop-capture pipeline;
- Python utilities and browser-side JavaScript tests.

These are not all runtime prerequisites for the active S25 virtual-display path. In
particular, do not tell users to install FFmpeg, Xvfb, x264, a JDK, an NDK, or Python
Xlib to run the current scrcpy backend unless a future build/test target explicitly
requires them. A maintainer must audit the old scripts and source-to-package mapping
before declaring a clean installer.

## Dependency and artifact hygiene

- The 5.0.1 server JAR is a third-party component; its upstream source, exact build,
  integrity hash, license and redistribution terms must be recorded before bundling it.
- The current project has PEM TLS material. A private key must never be published. A
  future setup should generate a per-install certificate or establish a trusted pairing
  flow instead of sharing one private key.
- No complete dependency lockfile or reproducible installation manifest is currently
  established.
- There is no signed Android APK/AAB or official WTM release package.

## Verification commands for a maintainer

These read-only commands inventory a local Termux setup:

```sh
python --version
python -c 'import aiohttp; print(aiohttp.__version__)'
adb version
adb devices -l
pm list packages | grep -E 'com\.termux(\.|$)'
```

Do not paste output containing serials, IP addresses, certificates, pairing information,
or personal paths into a public issue.

## Authoritative platform references

- [Android Debug Bridge (ADB)](https://developer.android.com/tools/adb)
- [Android foreground services](https://developer.android.com/develop/background-work/services/foreground-services)
- [Android MediaProjection](https://developer.android.com/media/grow/media-projection)
- [W3C WebCodecs specification](https://www.w3.org/TR/webcodecs/)

Android foreground services require a visible notification and remain subject to system
and user stop restrictions. MediaProjection is a user-consented capture API; it must not
be assumed to reproduce the current scrcpy virtual-display and input behavior without a
device-level prototype.
