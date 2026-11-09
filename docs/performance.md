# Performance and test evidence

## Measurement definitions

- **Configured FPS** is the `max_fps`/stream configuration (currently 60). It is only a
  ceiling.
- **Source FPS** is the scrcpy relay's count of H.264/HEVC access units read during its
  sampling interval.
- **Client input FPS** is the browser's count of frames received.
- **Client decoded FPS** is the WebCodecs output frame count per interval.
- **Latency** is the browser's estimated capture-timestamp-to-decoder-output path. It
  excludes parts of physical panel scanout and is not a photon-to-photon measurement.
- **Dropped** counts client-side dropped frames in the relevant interval. Server-side
  counters are separate.
- **Transport** (2026-11-09 onward) must be declared with every number: phone
  hotspot (phone as AP) vs router Wi-Fi vs other. The 2026-11-09 tests show the
  same content can deliver ~30 or ~60 FPS purely by changing the link.

Most samples below are five-second windows. Short tests do not establish thermal
equilibrium or a long-term percentile.

## Observed tests on 2026-11-06 (router Wi-Fi)

| Scenario | Source / client observation | Latency / drops | Interpretation |
|---|---|---|---|
| Relatively simple controlled motion, 15 s | Two later windows: source ~57-60 FPS; decoded client 57.6-60 FPS | 23-26 ms mean; zero client drops in those windows | Near-60 is possible for some scenes and short periods, not a sustained guarantee |
| Initial window of the same motion test | Client ~34.9 FPS | ~34.4 ms mean; 1 drop | Startup/scene/test transitions are not perfectly smooth |
| Forced WebCodecs hardware decode A/B | Client about 13-15 FPS; backlog grew to several seconds | Roughly 88-92 dropped frames per five-second window; decode delay reached multiple seconds | Poor result on this tablet. Restarting Chrome with software decode recovered to ~58.8 FPS |
| Firefox ↔ Settings app-switch test | Transition windows varied ~26.8-45.9 decoded FPS; source varied ~26.7-57.3 FPS | ~33-45 ms mean; 1-3 drops/window | App changes are a measurable weakness; no screen recording or visual ghost-pixel inspection was performed |
| Detailed/high-motion test, 20 s | Source reached ~58-59 FPS in later windows; client fell to ~13-14 FPS | Decode delay ~1.4-1.6 s; ~65-66 dropped frames/window; total reported latency ~1.47-1.65 s; transient client throughput telemetry ~62-70 Mbps | End-to-end decode/render path cannot keep up with this content on the tested viewer/configuration |
| Thermal-aborted test | One run stopped after 10 s at  44.8 °C reported skin temperature | preceding interval ~44.8 FPS, ~53 ms mean, 5 drops, peak ~203 ms | Not a completed stable run |

The phone was manually cooled and charging during follow-up tests. A later 20-second
motion test completed at a peak reported skin temperature of 42.2 °C and Android thermal
status 2 (moderate); additional load tests were stopped. These observations are not a
recommendation to operate at those temperatures.

## Observed codec A/B on 2026-11-09 (H.264 vs HEVC, router Wi-Fi, same session, ~60 s per leg)

Protocol: same Wi-Fi (rtt=3 ms), same content (H.264 1080p clip, VLC fullscreen loop),
same relay, single variable `VIDEO_CODEC` (h264 vs h265); encoder 2000x1200, 12 Mbps cap.
Full data and caveats in [hevc-study.md §7](hevc-study.md).

| Metric (stable state) | H.264 | HEVC | Reading |
|---|---|---|---|
| decode_ms avg | ~25.6–29.3 | ~18.7–20.3 | HEVC ~30% less decode cost |
| decode_ms max | 84–141 | 38–48 | HEVC 3x lower jitter peaks |
| TOTAL_ms avg | ~37–43 | ~31–36 | HEVC ~15% lower total latency |
| Sustained drops | 0 | 0 | Both stable; HEVC had one warm-up window (22 drops, init transient, net_ms low) |
| Decoded FPS | ~27.1–30.4 | ~29.5–30.1 | Tie; both below the 60 FPS cap (then unidentified) |
| Bitrate | ~8.9–11.1 Mbps | ~10.1–10.9 Mbps | Both at cap — bandwidth efficiency not measured |

This is a single content class, short run: not the acceptance run; the shipped default
remains H.264 (see hevc-study.md §7 “What this result does NOT claim”).

## Observed 60 FPS test on 2026-11-09 (HEVC, phone hotspot transport, real 4K60 content)

Protocol: phone hotspot (phone as AP, tablet as client; no router in the path),
`VIDEO_CODEC` default (HEVC, v0.3.0), encoder 2000x1200, 12 Mbps cap, 60 FPS
configured. Content: a 4K 60 FPS public sample clip (Big Buck Bunny) played fullscreen
on the virtual display for ~2.5 minutes. Metrics from the client `STATS` lines and
relay `[hevc]` lines, five-second windows.

| Metric | Value (sustained windows) | Reading |
|---|---|---|
| Delivered FPS | ~59.5–60.1 (input and decoded agree) | The full pipeline delivers the configured 60 on real content over this link |
| Client drops | 1 in ~2.5 min (~0.01%) | Isolated window (TOTAL_ms max 198 ms spike); no sustained degradation |
| rtt | 3 ms constant | Direct hotspot link, one hop |
| net_ms avg | ~8–10 ms (peaks 55–157 ms isolated) | Transport headroom is not the limit |
| decode_ms avg | ~16–17 ms (peaks 24–107 ms isolated) | At the 16.7 ms/frame budget edge; sustained, no backlog |
| TOTAL_ms avg | ~24–27 ms | Capture-to-decode consistent with earlier 23–26 ms observations |
| Bitrate | 9.6–12.9 Mbps | **Hits the 12 Mbps cap**; content requests more than the cap allows |

A valley of ~15–45 FPS with `mbps=0.33` corresponded to static/paused content —
expected: the scrcpy capture path only encodes on display updates.

**What this run does NOT claim:** no thermal equilibrium (short run), no soak, no
complex-motion content class, no packet-loss characterization, and one transport
(one phone hotspot, one reference router for the earlier comparison). It does,
however, close the transport question for this pair: the same pipeline and content
class that delivered ~30 FPS over the reference router delivered 60 here.

## Main conclusions

1. **Software decode is the safe default for the tested tablet.** Forcing hardware
   decoding created multi-second queues. The client now defaults to software and includes
   a queue-based fallback from explicitly requested hardware when `decodeQueueSize > 8`
   for two consecutive one-second checks.
2. **The bottleneck is content- and transport-dependent.** Simple motion can reach near
   the 60 FPS cap; detailed/high-motion content overwhelms the tested tablet decoder/renderer
   on router Wi-Fi; the same pipeline sustains 60 FPS on real 4K60 content over the phone
   hotspot. The earlier "30 FPS cap" (2026-11-09 codec A/B) was the reference router's
   transport limit, not a pipeline limit — see OPT-8 in
   [optimization.md](optimization.md).
3. **The codec A/B (2026-11-09) shows HEVC winning decode latency on this pair for the
   tested content class, with zero sustained drops** — the H.264-hw multi-second-queue
   failure mode did not reproduce for HEVC. The shipped default is HEVC since v0.3.0
   (validated by a long production session on this pair); see
   [hevc-study.md §7](hevc-study.md).
4. **The 12 Mbps encoder cap is now a measured constraint.** Real 4K60 content reached
   12.9 Mbps against the 12 Mbps cap with net headroom to spare; a bitrate ladder is the
   next measurement (see OPT-5).
5. **The previously stated ~20 ms average capture-to-decoder goal is not met reliably.** Windows on both transports averaged 23–27 ms. This is an
   aspirational software-metric goal, not a photon-to-photon measurement or a release gate.
6. **No OLED-like color/depth claim is supported.** The stream uses H.264/HEVC 4:2:0 and the
   virtual display advertises SDR. Colorimetry, HDR pass-through, panel calibration,
   transfer function and end-to-end bit depth have not been comprehensively measured.
7. **No 60 FPS certification exists.** There is no long-duration 60-minute soak, thermal
   steady-state run, packet-loss test, multi-network test, or instrumented panel
   presentation test. The 2026-11-09 hotspot run is a single short test on one pair.

## Why static screens report low FPS

The scrcpy encoder/capture path emits frames in response to display updates. A static
screen may yield very few frames even when the stream is healthy. FPS must be tested
with known motion. Conversely, a high source FPS does not prove that the browser
decoded/rendered/presented all frames.

## Acceptance criteria for a future release

A release claiming “2K60” should pass, on every published device pair:

- resolution: exactly 2000 x 1200 encoded frames;
- at least 57 decoded/presented FPS over each one-minute moving-content window for
  30 minutes, with a predeclared method for measuring presentation;
- fewer than 0.1% client frame drops per five-minute moving-content interval;
- capture-to-decode latency p95 <= 40 ms and p99 <= 80 ms, with a separate photon-to-
  photon measurement if claiming end-to-end panel latency;
- app-switch recovery without persistent stale frames, black/frozen output or manual
  browser reload;
- stable performance over the supported transports (hotspot and Wi-Fi), with measured
  packet loss, jitter, bandwidth, RSSI and channel;
- temperature, battery drain and power consumption reported over the full soak;
- successful reconnect after Termux process loss and phone reboot if those are claimed
  as supported scenarios.

These are proposed release gates, not already achieved results.
