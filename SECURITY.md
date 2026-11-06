# Security policy

## Current security status

WTM is an experimental local-network prototype, not a security-reviewed product.
The current relay uses a self-signed TLS certificate and has no documented application
authentication. A source audit of the active Termux server also found an unauthenticated
HTTP control route that launches start/stop/input-routing scripts with the Termux user's
privileges. Treat this as a privileged remote-control surface, not merely a video viewer.
An older work-copy daemon also exposes control over plain HTTP. Neither service is ready
for exposure to an untrusted network.

Run only on a trusted, isolated LAN. Do not port-forward the service to the public
Internet, use an untrusted hotspot, or share its private key.

The documentation-only WTM repository must not include the active server source or
instructions that make this control surface publicly reachable. The control route must
be redesigned and security-reviewed before implementation code is published or a
downloadable product is offered.

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

## Reporting a vulnerability

Until a private reporting address is configured by the repository owner, do not post
secret-bearing proof-of-concept material publicly. Contact the repository owner through
a verified private channel and include only the minimum information needed to reproduce
the issue.

## Required pre-release security work

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
