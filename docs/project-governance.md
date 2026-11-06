# Issues, milestones and release policy

## Repository maturity

WTM is currently an experimental prototype/handoff. A GitHub repository should not
imply that a downloadable app, supported device matrix or stable release exists. The
initial repository should publish documentation and reviewed, licensed source only.

## Suggested initial milestones

1. **Reproducible prototype** — source tree, dependency manifest, setup scripts, no
   embedded credentials, deterministic start/stop and a clean install guide.
2. **Lifecycle and reconnection** — documented behavior on Termux process kill, reboot,
   Wireless Debugging disabled, port change, Wi-Fi switch and screen lock.
3. **Security and pairing** — authentication, certificate provisioning, network
   exposure policy, input authorization and threat model.
4. **Performance baseline** — telemetry definitions, repeatable moving test content,
   sustained FPS/latency/thermal/power matrix and app-switch recovery.
5. **Native service feasibility** — prove display capture, virtual display, input and
   background lifecycle without relying on ADB/Termux before promising a native product.
6. **Release candidate** — reproducible Android build, signed artifacts, license/notice
   audit, supported-device matrix, upgrade/uninstall tests and release checklist.

## Issue process

Use the bug report form for reproducible failures and the feature request form for
proposals. Performance reports should include:

- exact source/viewer model and Android build;
- WTM commit/tag and Termux/ADB/scrcpy/browser versions;
- resolution/FPS/codec settings;
- Wi-Fi topology and measured RSSI/throughput if available;
- source FPS, client input/decoded FPS, latency percentiles and dropped frames;
- test duration, foreground app/content type and temperature/thermal status;
- whether the result was repeated.

Never attach private keys, ADB keys/pairing data, raw unredacted logs, personal IP
addresses or screenshots containing private information.

## Release policy

Do not create a release until all of these are true:

- the license and third-party notices are reviewed by the owner;
- the source tree is reproducible and contains no credentials, private certificates,
  device-specific logs or unlicensed binaries;
- supported Android/device combinations and known limitations are published;
- install, upgrade, recovery, stop and uninstall procedures are tested;
- sustained performance and thermal acceptance criteria are met or the release is
  accurately labelled as experimental;
- artifacts are signed, hashes are published, and no secret signing key is committed;
- a rollback plan and security-reporting path are available.

GitHub issues, labels and releases should be created by an authenticated repository
owner after the repository is established. This documentation does not fabricate
completed issue IDs, milestones or release tags.
