# Performance and optimization workstream

This is the working backlog for performance engineering. Measured evidence,
baseline observations and release acceptance criteria live in
[performance.md](performance.md); this page tracks what we intend to change
next, why, and how each item will be verified. Nothing here is a claim about
current behavior — only performance.md makes claims.

## Baseline summary (see performance.md for full data)

| Scenario | Observed (2026-11-06) |
|---|---|
| Simple controlled motion | ~57–60 decoded FPS, 23–26 ms mean latency, zero drops (short windows) |
| App switching | ~27–46 decoded FPS, 33–45 ms mean |
| High-motion / detailed content | Client collapses to ~13–14 FPS with 1.4–1.6 s decoder backlog |
| Forced hardware decode (tablet) | Multi-second queues; software decode is the default |

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
   the tested content class; the shipped default stays H.264 pending the full
   acceptance run. Remaining open questions under OPT-2: HEVC-vs-H.264 sweep
   BELOW the 12 Mbps cap (bandwidth efficiency was not measurable at the cap),
   and complex-motion HEVC behavior (the A/B used one content class). See
   hevc-study.md §7 and performance.md (2026-11-09).**

### OPT-3 — Wi-Fi power-save heartbeat tuning
The client sends a 25 ms heartbeat to keep the tablet radio out of power-save
(`hb` parameter). Open questions: actual duty-cycle cost, whether 40–50 ms
achieves the same latency, and whether the server-side echo can be piggybacked
onto the next frame instead of a standalone message.

### OPT-4 — App-switch FPS dips
Transition windows drop to ~27–46 FPS with occasional stale frames. Candidate:
an IDR request on detected scene change (the relay already has `request_key`),
and measuring whether the dip is encoder-side (source FPS drops too) or
client-side.

### OPT-5 — Encoder settings sweep
CRF/preset/GOP/bitrate are currently fixed defaults. A documented sweep on
the S25 (thermal state held constant) should produce a recommended profile per
content class. No change ships without a repeated A/B.

### OPT-6 — Photon-to-photon instrumentation
Current latency metrics end at decoder output (see performance.md caveats).
A true panel measurement (photodiode or high-FPS camera) is required before any
end-to-end latency claim. Until then: no latency numbers in marketing copy.

### OPT-7 — Reconnect and IDR behavior
After client reconnect the relay serves a cached keyframe. Untested at scale:
behavior under Wi-Fi roaming, phone reboot mid-session, and Chrome tab
suspension on the tablet. Define expected UX and verify.

### OPT-8 — FPS cap investigation (~30 delivered with 60 configured)
Observed in the 2026-11-09 codec A/B: both legs delivered ~30 decoded FPS while
the encoder is configured for 60 — identical in both legs, so the A/B is valid,
but the cap's location (content frame rate vs encoder vs bridge) is
unidentified. Investigate before any 60 FPS claim.

## How to measure (rules)

- Client: `https://<host>:8443/?lat=40` runs the built-in latency test
  (flash-based, capture-to-draw); the two-finger overlay and `STATS` client log
  lines every 5 s give fps/mbps/latency breakdowns.
- Server: `/api/status`, `/healthz`, `$PREFIX/tmp/rd-bridge.log`, and
  `tests/device-smoke.sh` for the on-device checklist.
- Five-second windows are not thermal or long-term evidence; declare the method
  before quoting any number, and never extrapolate a good window into a
  guarantee (see performance.md acceptance criteria).

## Completed

- 2026-11-07 — Startup mouse-association retry storm fixed (see OPT-1);
  software decode default and queue-based HW fallback (see performance.md).
- 2026-11-09 — Measured codec A/B H.264 vs HEVC on this pair: HEVC wins decode
  latency for the tested content class; default stays H.264 pending the
  acceptance run. Relay HEVC path merged (issue #4 closed); evidence in
  hevc-study.md §7 and performance.md.
