# Releases and packages

## What a release contains

A WTM release is a **source package** (`.tar.gz`) of the Termux runtime plus
its licenses and the core documentation. It is not an APK: there is no signed
Android application yet, and none is claimed.

The package includes:

- `LICENSE`, `LICENSE-docs.txt`, `LICENSING.md` — software and documentation
  licenses;
- `runtime/termux/` — server, web clients, launcher/watchdog scripts,
  `install.sh`, `update-network.sh`, tests and the on-device certification
  script;
- `docs/certification.md`, `docs/deployment-termux.md`, `SECURITY.md`.

It does **not** include: the scrcpy server binary (the installer downloads the
official release and verifies its SHA-256), TLS keys, tokens, or any
per-install material. Every installation generates its own credentials.

## Versioning

Releases use `vX.Y.Z` tags on `main`. The packaged runtime version is recorded
in `runtime/termux/VERSION` and bumped before tagging. The current version is
experimental and does not imply production stability or hardware
certification.

## Automated release process

Pushing a `v*` tag triggers `.github/workflows/release.yml`:

1. runs the full server test suites (security + live process);
2. builds `wtm-runtime-<version>.tar.gz` and its SHA-256 manifest;
3. publishes both files as assets of the GitHub Release;
4. uploads the package as a workflow artifact for inspection.

`.github/workflows/ci.yml` runs the same suites plus shell/Python/JavaScript
syntax checks and a secret sweep on every push and pull request.

## Manual release (fallback)

If Actions are unavailable, build and publish from a machine with `gh`:

```sh
VERSION=0.1.0
tar -czf "wtm-runtime-${VERSION}.tar.gz" \
    --transform "s,^,wtm-runtime-${VERSION}/," \
    LICENSE LICENSE-docs.txt LICENSING.md README.md SECURITY.md \
    runtime/termux/server.py runtime/termux/web \
    runtime/termux/*.sh runtime/termux/*.py \
    runtime/termux/shortcuts runtime/termux/boot runtime/termux/tests \
    runtime/termux/VERSION \
    docs/certification.md docs/deployment-termux.md
sha256sum "wtm-runtime-${VERSION}.tar.gz" > "wtm-runtime-${VERSION}.tar.gz.sha256"
gh release create "v${VERSION}" "wtm-runtime-${VERSION}.tar.gz" \
    "wtm-runtime-${VERSION}.tar.gz.sha256" \
    --title "WTM runtime v${VERSION}" --notes "See docs/certification.md"
```

## Installation from a release

On the source phone, in Termux:

```sh
curl -L -o wtm.tar.gz \
  https://github.com/JostinVaron/WTM/releases/download/v0.1.0/wtm-runtime-0.1.0.tar.gz
tar -xzf wtm.tar.gz
cd wtm-runtime-0.1.0/runtime/termux
bash install.sh
```

Always verify the published SHA-256 before extracting, and remember that
`install.sh` downloads the official scrcpy server and generates per-install
credentials.
