# GitHub publication and release status

## Public repository

The documentation repository is published at
[JostinVaron/WTM](https://github.com/JostinVaron/WTM) and is public. Its `main` branch
contains the project overview, architecture and deployment notes, dependency inventory,
hardware requirements, measured performance, security policy, contribution/governance
guidance and GitHub issue templates.

This is a documentation handoff, not a source-code or app release. The repository does
not include the active Termux runtime, an APK, third-party server binaries, logs,
certificates, private keys or ADB authorization material. The device-specific prototype
must not be treated as a reproducible installation until the runtime source, dependency
provenance and installation process are reviewed and published safely.

The project has no selected license; see [No license](../NO-LICENSE.md). Public
availability does not grant permission to redistribute or reuse the material.

## Security and privacy gate for future changes

Before adding files to this public repository:

- inspect the full diff and all files for passwords, tokens, private keys, certificates,
  pairing data, serial numbers, personal network addresses, raw logs and private paths;
- inspect the commit history as well as the current tree; deleting a secret in a later
  commit does not remove it from history;
- publish only information the owner intends to make public, and use placeholders for
  per-install network values;
- keep runtime code unpublished until its unauthenticated WebSocket/control/log
  interfaces and TLS behavior are redesigned and independently reviewed;
- establish source provenance, third-party notices and a project license before
  redistributing implementation code or binaries.

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
