# Threat model

Scope: the published hardened runtime in `runtime/termux/`, running on the
source phone inside Termux, serving the tablet over the local Wi-Fi. The
deployed prototype (pre-hardening) is documented separately in
[SECURITY.md](../SECURITY.md) and is out of scope here.

## Assets

| Asset | Confidentiality | Integrity | Availability |
|---|---|---|---|
| Video stream of the phone's virtual display | High (private screen content) | Medium | High (the product) |
| Input channel (touch/pointer/keyboard into the phone) | Medium | High (injecting input = controlling the device) | High |
| Viewer/admin tokens (`~/.config/wtm/auth-tokens.json`) | Critical (admin = full control) | High | Low |
| TLS private key (`~/.config/wtm/key.pem`) | Critical | High | Low |
| Control actions (start/stop DeX) | Low | High | Medium |
| Device diagnostics (`/api/logs`, `/api/status`) | Medium | Medium | Low |

## Attackers

1. **LAN attacker** (same Wi-Fi, no credentials): passive listener and active
   MITM/impersonator. The main adversary.
2. **Compromised or malicious viewer** (someone with a viewer token): can
   watch and inject input; must not escalate to admin.
3. **Local attacker on the phone** (other apps or a person with the device):
   can read files the Termux user can read; mitigations are file permissions
   and the Android sandbox.
4. **Remote attacker on the Internet**: only reachable if the port is
   forwarded or the phone joins an untrusted network; out of the supported
   deployment, but covered by fail-closed defaults.

## Controls implemented (with test evidence)

| Threat | Control | Where | Tested |
|---|---|---|---|
| Plaintext interception / downgrade | HTTPS-only; server refuses to start without valid TLS; TLS >= 1.2; no HTTP fallback | `server.py` `load_server_config`/`validate_cert_chain` | `test_server_security.py` (TLS fail-closed), `test_server_live.py` |
| MITM impersonation | Per-install certificate SHA-256 fingerprint shown at install, on `/login`, in the panel and `/healthz`; user compares out-of-band | `install.sh`, `server.py` `pem_fingerprint` | live tests + `device-smoke.sh` checks |
| Unauthenticated access | Cookie session login with 256-bit viewer/admin tokens; all non-public paths require a session | `server.py` `security_middleware` | security + live suites |
| Login brute force | 6 failed attempts per peer per 5 minutes | `api_auth_handler` | `test_login_throttled_after_failures` |
| Cross-site WebSocket/CSRF | Same-site Origin required for all POST/WS; Host allowlist | `security_middleware` | security + live suites |
| Unauthorized control | `/api/control` admin-only, fixed actions (`start_dex`, `stop_dex`), no shell construction | `api_control_handler` | viewer-403 and action-limit tests |
| Information leak via health endpoint | `/healthz` machine-local only, unauthenticated, limited to `ok`, `scrcpy_alive` and the public certificate fingerprint. Machine-local means loopback **or** a peer whose source IP equals the listener IP (needed because the hardened listener binds the Wi-Fi IP, and the on-device scripts check health from there) | `health_handler` + `is_local_peer` | security + live suites + on-device smoke |
| Host header/DNS-rebinding | Allowlist of Host values (loopback defaults + `WTM_ALLOWED_HOSTS`) | `security_middleware` | `test_wrong_host_rejected` |
| Exposure on unknown networks | Default bind = detected Wi-Fi IP, not 0.0.0.0; `update-network.sh` refreshes it | `install.sh`, `update-network.sh` | shell checks in CI |
| Log injection | `/log` strips C0 control characters | `log_handler` | code review (security report) |
| Supply chain | scrcpy official artifact pinned by SHA-256; `aiohttp` pinned to tested version | `install.sh` | checksum verified 2026-11-06 |
| Secret leakage in repo | `.gitignore` covers keys/tokens; CI secret sweep | `.github/workflows/ci.yml` | CI green |

## Residual risks (accepted, documented)

- **Static shared tokens with client-side logout.** The cookie expiry is
  browser-side; a leaked token stays valid until regenerated. Mitigation:
  regenerate credentials by re-running credential generation or reinstalling.
- **Self-signed certificate.** Trust rests on the fingerprint comparison;
  failure to compare enables the MITM threat above. A CA-signed certificate
  or one-time tablet cert import would be stronger.
- **LAN attacker between fingerprint check and use.** The fingerprint flow
  detects a substituted certificate only if the user compares. Automated
  pinning in the client is a possible future improvement.
- **Health endpoint accepts peers whose source IP equals the listener IP.**
  `/healthz` is machine-local but the hardened listener binds the Wi-Fi IP, so
  on-device scripts connect from that address. A peer that can spoof that
  source IP could read `ok`, `scrcpy_alive` and the (public) certificate
  fingerprint without completing a TCP handshake bidirectionally; nothing
  actionable is exposed. Keep the port closed to untrusted networks.
- **Wireless debugging (adbd) surface.** Required for the pipeline. Android's
  pairing protects silent takeover, but an attacker can trigger an on-screen
  authorization prompt; a single accidental "Allow" grants a full ADB shell.
  Keep wireless debugging off on untrusted networks.
- **No multi-user identities.** Tokens are shared secrets, not accounts;
  revoking one viewer means rotating the token everyone uses.
- **Device diagnostics readable by any authenticated session** (`/api/logs`,
  `/api/status`). Viewer role can read them; do not put secrets in logs.

## Out of scope / not protected

- The deployed pre-hardening prototype on the S25 (see SECURITY.md).
- A phone with wireless debugging enabled on an untrusted network.
- A rooted device or an attacker with physical access to the phone.
- The historical Linux/Xvfb work copy, which is not part of this repository.

## Review history

- 2026-11-06: independent read-only security review of the release candidate.
  Result: no CRITICAL/HIGH findings; 2 MEDIUM and 3 LOW, all fixed before
  publication. See [certification](certification.md) and SECURITY.md.
