# Performance and optimization workstream

This is the working backlog for performance engineering. Measured evidence,
baseline observations and release acceptance criteria live in
[performance.md](performance.md); this page tracks what we intend to change
next, why, and how each item will be verified. Nothing here is a claim about
current behavior — only performance.md makes claims.

## Baseline summary (see performance.md for full data)

| Scenario | Observed |
|---|---|
| Simple controlled motion (router Wi-Fi, 2026-11-06) | ~57–60 decoded FPS, 23–26 ms mean latency, zero drops (short windows) |
| High-motion / detailed content (router Wi-Fi, 2026-11-06) | Client collapses to ~13–14 FPS with 1.4–1.6 s decoder backlog |
| Codec A/B (router Wi-Fi, 2026-11-09) | Both legs ~30 decoded FPS with 60 configured (cap then unidentified) |
| Real 4K60 content, hotspot transport (2026-11-09) | ~59.5–60.1 FPS sustained, 1 drop in ~2.5 min, rtt=3 ms, decode ~16–17 ms |

## Transport verdict: hotspot (phone as AP) over router Wi-Fi

Measured on 2026-11-09 on the tested pair: the same pipeline, same devices,
same content class that delivered ~30 FPS over the owner's router Wi-Fi
delivered sustained ~60 FPS over the phone's 5 GHz hotspot with `rtt=3 ms`
and `net_ms ~8–10 ms`. The previously unidentified FPS cap (OPT-8) was the
router transport, not the pipeline. The hotspot direct link is now the
**recommended transport** for this workflow; router Wi-Fi remains a supported
fallback but is a measured bottleneck on the reference network (see
performance.md, 2026-11-09). No claim is made about other routers or
networks: this is one measured pair, one reference router.

## Active backlog

### OPT-1 — DeX startup latency until the mouse controls the virtual display
User-reported (2026-11-07): perceptible lag between starting the DeX and the
mouse driving the tablet desktop.

- **Root cause found and fixed (2026-11-07):** `route-input.sh` treated the
  historical jar's success output (`OK addUniqueIdAssociation...`) as a
  failure, so `dex.sh` retried the association up to 10 times (~10 s) and then
  also started the backup router unnecessarily. Fix: success detection accepts
  both output formats (`ASSOC_OK` and `OK <method>`), plus the displayId is
  now parsed from the scrcpy uniqueId tail.
- **Remaining work:** measure the actual time from widget tap to "mouse drives
  the DeX" after the fix (expect: a single association attempt, bounded by
  adb round-trips). The native association is per-display and must be
  re-applied on every start; if residual latency still matters, candidate
  mitigations are: run the association concurrently with the readiness loop
  instead of after it, and cache the mouse descriptor between runs.
- **Verify with:** `tail -f $PREFIX/tmp/rd-bridge.log` during start; the
  `asociación nativa del mouse aplicada (intento N)` line should appear with
  N=1 within ~2 s of stream-ready.

### OPT-2 — Decoder backlog on complex motion
The Tab S6 Lite software decoder collapses under detailed/high-motion content
(see performance.md). Candidate directions, in order:

1. A/B `max_size` below 2000 (e.g. 1600) — cheaper pixels may beat more pixels.
2. Encoder profile/level and `video_bit_rate` sweep on the S25 (MediaCodec).
3. Revisit hardware decode on the tablet behind a strict queue guard (the
   current auto-fallback exists; the question is whether a bounded HW path can
   beat SW on this panel at all).
4. ~~Consider `codec=h265` A/B — decode cost vs. bitrate, tablet permitting.~~
   **Answered (2026-11-09): the measured A/B ran on this pair — HEVC wins
decode latency (~30% less, 3x lower jitter peaks, zero sustained drops) for
   the tested content class; v0.3.0 made HEVC the shipped default after a long
   production session validated it on this pair (2026-11-09). Remaining open questions under OPT-2: HEVC-vs-H.264 sweep
   BELOW the 12 Mbps cap (bandwidth efficiency was not measurable at the cap),
   and complex-motion HEVC behavior (the A/B used one content class). See
   hevc-study.md §7 and performance.md (2026-11-09).**

### OPT-3 — Wi-Fi power-save heartbeat tuning
The client sends a 25 ms heartbeat to keep the tablet radio out of power-save
(`hb` parameter). Open questions: actual duty-cycle cost, whether 40–50 ms
achieves the same latency, and whether the server-side echo can be piggybacked
onto the next frame instead of a standalone message. NOTE (2026-11-09): the
transport switch to the phone hotspot may change the relevant radio
power-save behavior; re-measure on the hotspot link.

### OPT-4 — App-switch FPS dips
Transition windows drop to ~27–46 FPS with occasional stale frames (measured
on router Wi-Fi). Candidate: an IDR request on detected scene change (the
relay already has `request_key`), and measuring whether the dip is
encoder-side (source FPS drops too) or client-side. Re-measure on the hotspot
link, where the transport headroom is now larger.

### OPT-5 — Encoder settings sweep
CRF/preset/GOP/bitrate are currently fixed defaults. A documented sweep on
the S25 (thermal state held constant) should produce a recommended profile per
content class. No change ships without a repeated A/B. **New justification
(2026-11-09, hotspot test):** real 4K60 content pushed the encoder to the
12 Mbps cap (observed 9.6–12.9 Mbps, rtt and net headroom abundant) — the
first sweep target is a bitrate ladder (12 → 16 → 20 → 24 Mbps) to quantify
what quality the link can carry without drops or latency regression.

### OPT-6 — Photon-to-photon instrumentation
Current latency metrics end at decoder output (see performance.md caveats).
A true panel measurement (photodiode or high-FPS camera) is required before any
end-to-end latency claim. Until then: no latency numbers in marketing copy.

### OPT-7 — Reconnect and IDR behavior
After client reconnect the relay serves a cached keyframe. Untested at scale:
behavior under Wi-Fi roaming, phone reboot mid-session, and Chrome tab
suspension on the tablet. Define expected UX and verify.

### OPT-8 — FPS cap investigation (~30 delivered with 60 configured) — **CLOSED**
Observed in the 2026-11-09 codec A/B (router Wi-Fi): both legs delivered ~30
decoded FPS while the encoder is configured for 60. **Resolution (same day,
hotspot migration): the same pipeline delivered sustained 59.5–60.1 FPS with
real 4K60 content over the phone's 5 GHz hotspot — zero code changes.** The
cap was the router transport, not the pipeline: the encoder, relay and
decoder were never the limiter at 60 FPS for the tested content class.
Evidence: performance.md (2026-11-09 hotspot test). Follow-up work moved to
OPT-5 (the encoder now runs against the 12 Mbps cap).

## How to measure (rules)

- Client: `https://<host>:8443/?lat=40` runs the built-in latency test
  (flash-based, capture-to-draw); the two-finger overlay and `STATS` client log
  lines every 5 s give fps/mbps/latency breakdowns.
- Server: `/api/status`, `/healthz`, `$PREFIX/tmp/rd-bridge.log`, and
  `tests/device-smoke.sh` for the on-device checklist.
- Five-second windows are not thermal or long-term evidence; declare the method
  before quoting any number, and never extrapolate a good window into a
  guarantee (see performance.md acceptance criteria).
- Declare the transport (hotspot vs router Wi-Fi vs other) with every number:
  the 2026-11-09 pair of tests shows the same content can deliver 30 or 60 FPS
  purely by changing the link. Never quote FPS without the transport.

## Completed

- 2026-11-07 — Startup mouse-association retry storm fixed (see OPT-1);
  software decode default and queue-based HW fallback (see performance.md).
- 2026-11-09 — Measured codec A/B H.264 vs HEVC on this pair: HEVC wins decode
  latency for the tested content class; the default later flipped to HEVC in v0.3.0. Relay HEVC path merged (issue #4 closed); evidence in
  hevc-study.md §7 and performance.md.
- 2026-11-09 — v0.3.0: HEVC became the shipped default (measured A/B plus a
  long production session on this pair); patch-hevc-relay.py was removed after
  consolidating its patch into server.py. Documented rollback: `VIDEO_CODEC=h264`.
- 2026-11-09 — Transport migration to the phone hotspot completed and measured:
  sustained 60 FPS on real 4K60 content where the reference router delivered
  ~30 (OPT-8 closed as a transport finding). Hotspot is the recommended
  transport; see performance.md.
