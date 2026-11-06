# Security policy

## Current security status

WTM is an experimental local-network prototype, not a security-reviewed product. A
repository-content security sweep was completed on 2026-11-06; this was not a full product
security assessment.

Two runtimes exist and must not be confused:

1. **The prototype currently deployed on the S25** still uses a self-signed
   certificate with no application authentication for its WebSocket, control
   or log endpoints. Its control route can launch start/stop/input-routing
   scripts with the Termux user's privileges, and it can fall back to plain
   HTTP. Treat it as a privileged remote-control surface, not merely a video
   viewer, and keep it off untrusted networks.
2. **The published runtime in `runtime/termux/`** is the hardened release
   candidate: cookie sessions with separate viewer/admin tokens, same-site
   Origin enforcement, Host allowlist, TLS fail-closed startup, machine-local
   unauthenticated `/healthz`, fixed admin-only control actions, login
   throttling, per-install credentials and a certificate-fingerprint
   verification flow. An independent read-only security review on 2026-11-06
   found no CRITICAL or HIGH findings; its MEDIUM and LOW findings were fixed
   before publication. Details and evidence:
   [certification](docs/certification.md) and
   [runtime release candidate](docs/runtime-release-candidate.md).

Both runtimes must be used only on a trusted, isolated LAN. Do not port-forward
the service to the public Internet, use an untrusted hotspot, or share its private
key. On the hardened runtime, always compare the certificate fingerprint shown by
the login page with the value printed at install time, and refuse to enter tokens
on a mismatch.

## Sensitive material — never publish

- TLS private keys (`key.pem`, `*.key`, private PKCS#12/keystore files);
- ADB private keys, pairing secrets and device authorization files;
- passwords, tokens, cookies, QR codes, pairing codes and Wi-Fi credentials;
- raw logs containing IP addresses, device serials, account names or local paths;
- generated device certificates unless explicitly approved and safe to share;
- Android signing keys and release credentials.

Use fresh credentials for each development or user installation. Do not commit any
secret and then assume that deleting it in a later commit makes it safe; rotate the
exposed credential.

## Token rotation procedure (hardened runtime)

Tokens are static per-install shared secrets. There is no in-process revocation;
rotation regenerates the credential set. To rotate on the phone, in Termux:

```sh
rm -f ~/.config/wtm/auth-tokens.json
bash ~/dex/install.sh   # regenerates tokens and prints the new ones
bash ~/dex/tests/device-smoke.sh   # re-verify the install before use
```

`install.sh` preserves existing TLS material and configuration, so rotation does
not require new certificate-fingerprint comparison; only the login tokens change.
All browsers holding the old cookie must log in again with the new token.

## Reporting a vulnerability

Private vulnerability reporting is enabled. Report vulnerabilities confidentially
through GitHub:

- <https://github.com/JostinVaron/WTM/security/advisories/new>

Include only the minimum information needed to reproduce the issue and never post
secret-bearing proof-of-concept material publicly. Prefer the advisory flow over
public issues for anything security-related.

The threat model with assets, attackers, implemented controls and residual risks is in
[docs/threat-model.md](docs/threat-model.md). Security findings should map to a threat
and an asset and must be reproducible without touching the owner's live devices.

## Required pre-release security work

The hardened runtime in `runtime/termux/` covers the items below with
implemented controls and passing tests; they are not a guarantee:

- authenticate and authorize every WebSocket/control session;
- bind listeners only to explicitly intended interfaces;
- document allowed origins, certificate trust and pairing;
- protect against cross-site WebSocket requests and unauthorized input injection;
- remove shell-script execution from unauthenticated HTTP request handling;
- rate-limit and validate input/API payloads;
- review TLS certificate lifecycle and private-key storage;
- audit ADB exposure and ensure no remote ADB server is opened to the LAN;
- scan dependency/artifact provenance and licenses;
- test behavior when Wi-Fi changes, peers disconnect, and permissions are revoked.

Remaining known gaps in the published runtime: tokens are static shared secrets
with client-side logout only (rotate with the procedure above), the
certificate is self-signed (trust rests on the fingerprint comparison), the
login throttle is per peer IP with a 5-minute window (expired entries are
pruned on each sign-in attempt), and
the deployment on the real S25 has not been exercised with the on-device
certification script. Track these in issues before any production claim.

## Release-candidate hardening status

The branch `executable-release-candidate` (merged to `main`) packages the hardened
runtime described above. Its controls have automated tests and completed an
independent security review but have **not** been exercised on the prototype phone
yet. They do not make the system "impenetrable"; they reduce specific documented
risks. See [runtime release candidate](docs/runtime-release-candidate.md).
