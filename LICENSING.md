# Licensing

## What this project is licensed under

- **Software** (`runtime/`, scripts, server, installer, tests): [PolyForm
  Noncommercial License 1.0.0](LICENSE), with Jostin Varon as licensor.
- **Documentation** (README, docs/, SECURITY.md, guides): [Creative Commons
  Attribution-NonCommercial 4.0 International](LICENSE-docs.txt).

Both licenses permit free use, study, modification and sharing for
non-commercial purposes, with attribution to the author. Commercial use
requires separate permission from the licensor.

## Important nuance: "source-available", not OSI "open source"

The owner requested "libre uso sin fines comerciales, código abierto". These
goals are in partial tension: the Open Source Initiative (OSI) definition of
"open source" forbids discriminating against fields of endeavor, so no license
with a non-commercial restriction can be OSI-approved. This project is
therefore **source-available with a non-commercial restriction**, not
OSI-certified "open source". The source is public, readable, forkable and
testable, but redistribution is limited to non-commercial purposes.

If the owner later wants a fully OSI-approved license, the natural choices are
MIT, Apache-2.0 or GPL-3.0-only; changing the license is a single-file decision
(see [governance](docs/project-governance.md)).

## Third-party components

- **scrcpy** (Genymobile) — the `scrcpy-server` artifact downloaded by the
  installer is Apache-2.0. WTM does not redistribute that artifact; the
  installer downloads the official release and verifies its SHA-256.
- **aiohttp, Python, OpenSSL, curl** and the other runtime dependencies keep
  their own upstream licenses; see [dependencies](docs/dependencies.md).

The WTM files remain under the WTM licenses regardless of third-party
licenses of dependencies.

## Author

Licensor: **Jostin Varon** — <https://github.com/JostinVaron/WTM>

The Required Notice lines in [LICENSE](LICENSE) record this authorship.
