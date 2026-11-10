# Releases and packages

## What a release contains

A WTM release is a **source package** (`.tar.gz`) of the Termux runtime plus
its licenses and the complete documentation set. It is not an APK: there is no signed
Android application yet, and none is claimed.

The package includes:

- `LICENSE`, `LICENSE-docs.txt`, `LICENSING.md` — software and documentation
  licenses;
- `README.md`, `SECURITY.md`, `AGENTS.md`, `CODE_OF_CONDUCT.md`,
  `CONTRIBUTING.md` — top-level project documents;
- `runtime/termux/` — server, web clients (including the codec probe page),
  launcher/watchdog scripts, `install.sh`, `update-network.sh`, tests and the
  on-device certification script;
- `runtime/termux/route-input.sh`, `mouse-route.py`, `jvm/`, `native/`,
  `tools/` — the Bluetooth mouse routing subsystem (native association as the
  primary path, `mouse-route.py` as fallback, idempotent patch tooling);
- `docs/` — the complete documentation set (architecture, bluetooth-mouse
  routing, certification, dependencies, deployment, HEVC study, optimization,
  performance, project governance, publishing, releases, requirements,
  runtime release candidate, threat model).

It does **not** include: the scrcpy server binary (the installer downloads the
official release and verifies its SHA-256), TLS keys, tokens, or any
per-install material. Every installation generates its own credentials.

## Versioning

Releases use `vX.Y.Z` tags on `main`. The packaged runtime version is recorded
in `runtime/termux/VERSION` and bumped before tagging. The current version is
experimental and does not imply production stability or hardware certification
beyond the completed on-device run on the tested pair.

## What changed in v0.4.0

- **DeX wizard as the single guided start path** (issue #15): the new
  `shortcuts/DeX` shortcut recovers ADB after a reboot (fixed port
  5555, guided Wireless Debugging fallback), detects the hotspot IP,
  syncs `server.env`, checks the tablet, starts the service to
  `scrcpy_alive` and shows the exact URL, TLS fingerprint and any
  configured token in a persistent dialog (also copied to the
  clipboard). Verified 4/4 on real S25 reboots (2026-11-10).
  `shortcuts/Exit-Dex` stops the session; the old `start-dex.sh` and
  `stop-dex.sh` shortcuts remain as compatibility wrappers.
- **`install.sh` section 6** installs the four shortcuts plus the
  validated internal scripts (`bin/start-dex.sh`, `bin/stop-dex.sh`)
  under `~/.config/wtm/bin/`.
- **CI** now syntax-checks the wizard, `Exit-Dex` and `bin/`; the
  release tarball packages `runtime/termux/bin/`.
- Docs synchronized: README quick start, deployment guide,
  dependencies, requirements, certification and SECURITY.md.

## What changed in v0.3.1

- **Hotspot as the recommended transport** (PR #11): `install.sh` prefers
  `swlan0` (One UI hotspot interface) and syncs `server.env` host/bind lines
  when the network changes; `stop-dex.sh` reports "already stopped" instead of
  dying silently; the bridge log now carries timestamps.
- **IP detection via `adb shell ip`** (PR #13): the Termux `ip` binary cannot
  read netlink on modern Android, so `swlan0` detection never executed. Both
  `install.sh` and `update-network.sh` now query interfaces through the adb
  loopback connection (Android's shell user can read netlink), hotspot first,
  with the UDP default-route probe as a warned last resort.
- **Fail-closed private IP sweep** (PR #11): no RFC 1918 operator IP may ever
  land in the repository; enforced by `runtime/termux/tests/test_server_security.py`.
- **Measured 60 FPS evidence** (issue #4 follow-up, OPT-8 closed): a full
  on-device run documented in `docs/performance.md` (59.5–60.1 fps, 1 drop,
  rtt 3 ms) proving the earlier ~30 fps cap was the reference router, not the
  pipeline.

## Automated release process

Pushing a `v*` tag triggers `.github/workflows/release.yml`:

1. runs the full server test suites (security + live process);
2. builds `wtm-runtime-<version>.tar.gz` and its SHA-256 manifest;
3. publishes both files as assets of the GitHub Release;
4. uploads the package as a workflow artifact for inspection.

`.github/workflows/ci.yml` runs the same suites plus shell/Python/JavaScript
syntax checks and a fail-closed secret sweep on every push and pull request.

## Manual release (fallback)

If Actions are unavailable, build and publish from a machine with `gh`:

```sh
VERSION=0.4.0
tar -czf "wtm-runtime-${VERSION}.tar.gz" \
    --transform "s,^,wtm-runtime-${VERSION}/," \
    LICENSE LICENSE-docs.txt LICENSING.md README.md SECURITY.md \
    AGENTS.md CODE_OF_CONDUCT.md CONTRIBUTING.md \
    runtime/termux/server.py runtime/termux/hevc.py runtime/termux/mouse-route.py \
    runtime/termux/find-adb-port.py runtime/termux/web \
    runtime/termux/dex.sh runtime/termux/install.sh \
    runtime/termux/scrcpy-phone.sh runtime/termux/route-input.sh \
    runtime/termux/start_phone.sh runtime/termux/watchdog.sh \
    runtime/termux/update-network.sh runtime/termux/jvm \
    runtime/termux/native runtime/termux/tools \
    runtime/termux/shortcuts runtime/termux/bin runtime/termux/boot runtime/termux/tests \
    runtime/termux/VERSION runtime/termux/requirements.txt \
    docs/architecture.md docs/bluetooth-mouse-routing.md \
    docs/certification.md docs/dependencies.md \
    docs/deployment-termux.md docs/hevc-study.md docs/optimization.md \
    docs/performance.md docs/project-governance.md docs/publishing.md \
    docs/releases.md docs/requirements.md \
    docs/runtime-release-candidate.md docs/threat-model.md
sha256sum "wtm-runtime-${VERSION}.tar.gz" > "wtm-runtime-${VERSION}.tar.gz.sha256"
gh release create "v${VERSION}" "wtm-runtime-${VERSION}.tar.gz" \
    "wtm-runtime-${VERSION}.tar.gz.sha256" \
    --title "WTM runtime v${VERSION}" --notes "See docs/certification.md"
```

## Installation from a release

On the source phone, in Termux:

```sh
curl -L -o wtm.tar.gz \
  https://github.com/JostinVaron/WTM/releases/download/v0.4.0/wtm-runtime-0.4.0.tar.gz
tar -xzf wtm.tar.gz
cd wtm-runtime-0.4.0/runtime/termux
bash install.sh
# Start: bash ~/.shortcuts/DeX - the wizard recovers ADB after a
#        reboot and shows the URL + TLS fingerprint.
# Stop:  bash ~/.shortcuts/Exit-Dex
```

Always verify the published SHA-256 before extracting, and remember that
`install.sh` downloads the official scrcpy server and generates per-install
credentials.
