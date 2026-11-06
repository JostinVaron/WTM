# GitHub publication and release status

## Public repository

The repository is published at
[JostinVaron/WTM](https://github.com/JostinVaron/WTM) and is public. Its `main` branch
contains the project overview, architecture and deployment notes, dependency inventory,
hardware requirements, measured performance, security policy, contribution/governance
guidance, GitHub issue templates and — since 2026-11-06 — the hardened runtime sources
under `runtime/termux/` with their installer, tests and certification notes.

This is a source publication and prototype handoff, not an app release. The repository
does not include an APK, third-party server binaries, logs, certificates, private keys
or ADB authorization material. The installer downloads the official scrcpy server
artifact (pinned SHA-256) and generates all credentials on the device. The runtime has
host-level automated tests and a completed independent security review, but has not
been exercised on the real device pair; see
[certification](certification.md).

The project is licensed: software under [PolyForm Noncommercial 1.0.0](../LICENSE)
and documentation under [CC BY-NC 4.0](../LICENSE-docs.txt). Both permit
non-commercial use, modification and sharing with attribution; they are not
OSI-approved "open source" licenses (see [Licensing](../LICENSING.md)).

## Security and privacy gate for future changes

Before adding files to this public repository:

- inspect the full diff and all files for passwords, tokens, private keys, certificates,
  pairing data, serial numbers, personal network addresses, raw logs and private paths;
- inspect the commit history as well as the current tree; deleting a secret in a later
  commit does not remove it from history;
- publish only information the owner intends to make public, and use placeholders for
  per-install network values;
- the published runtime must keep passing its test suites and respect the security
  controls documented in [SECURITY.md](../SECURITY.md);
- establish source provenance, third-party notices and a project license before
  redistributing implementation code or binaries.

The license gate is resolved: software is PolyForm Noncommercial 1.0.0 and
documentation is CC BY-NC 4.0 (see [Licensing](../LICENSING.md)).

Never put GitHub passwords, personal access tokens, SSH private keys, ADB keys or
pairing secrets in this repository, an issue, a commit message or this chat. Authenticate
through GitHub's official interface and use a protected credential store on any machine
used for Git operations.

## Issues and releases

The repository includes issue forms, but no issues should be fabricated. Recommended
follow-up issues include:

1. secure/authenticated control plane and WebSocket pairing;
2. Termux-independent service feasibility and Android permission model;
3. reproducible install/dependency manifest;
4. sustained high-motion decoder/performance investigation;
5. app-switch freeze/visual-artifact test plan;
6. lifecycle/reconnect tests after reboot, force-stop and Wi-Fi changes;
7. project license and third-party notice review.

There is no GitHub Release. Do not create one until the gates in
[project governance](project-governance.md) are met. The public documentation snapshot
is not a downloadable software release.
