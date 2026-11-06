# Performance and test evidence

## Measurement definitions

- **Configured FPS** is the `max_fps`/stream configuration (currently 60). It is only a
  ceiling.
- **Source FPS** is the scrcpy relay's count of H.264 access units read during its
  sampling interval.
- **Client input FPS** is the browser's count of frames received.
- **Client decoded FPS** is the WebCodecs output frame count per interval.
- **Latency** is the browser's estimated capture-timestamp-to-decoder-output path. It
  excludes parts of physical panel scanout and is not a photon-to-photon measurement.
- **Dropped** counts client-side dropped frames in the relevant interval. Server-side
  counters are separate.

Most samples below are five-second windows. Short tests do not establish thermal
equilibrium or a long-term percentile.

## Observed tests on 2026-11-06

| Scenario | Source / client observation | Latency / drops | Interpretation |
|---|---|---|---|
| Relatively simple controlled motion, 15 s | Two later windows: source ~57-60 FPS; decoded client 57.6-60 FPS | 23-26 ms mean; zero client drops in those windows | Near-60 is possible for some scenes and short periods, not a sustained guarantee |
| Initial window of the same motion test | Client ~34.9 FPS | ~34.4 ms mean; 1 drop | Startup/scene/test transitions are not perfectly smooth |
| Forced WebCodecs hardware decode A/B | Client about 13-15 FPS; backlog grew to several seconds | Roughly 88-92 dropped frames per five-second window; decode delay reached multiple seconds | Poor result on this tablet. Restarting Chrome with software decode recovered to ~58.8 FPS |
| Firefox ↔ Settings app-switch test | Transition windows varied ~26.8-45.9 decoded FPS; source varied ~26.7-57.3 FPS | ~33-45 ms mean; 1-3 drops/window | App changes are a measurable weakness; no screen recording or visual ghost-pixel inspection was performed |
| Detailed/high-motion test, 20 s | Source reached ~58-59 FPS in later windows; client fell to ~13-14 FPS | Decode delay ~1.4-1.6 s; ~65-66 dropped frames/window; total reported latency ~1.47-1.65 s; transient client throughput telemetry ~62-70 Mbps | End-to-end decode/render path cannot keep up with this content on the tested viewer/configuration |
| Thermal-aborted test | One run stopped after 10 s at 44.8 °C reported skin temperature | preceding interval ~44.8 FPS, ~53 ms mean, 5 drops, peak ~203 ms | Not a completed stable run |

The phone was manually cooled and charging during follow-up tests. A later 20-second
motion test completed at a peak reported skin temperature of 42.2 °C and Android thermal
status 2 (moderate); additional load tests were stopped. These observations are not a
recommendation to operate at those temperatures.

## Main conclusions

1. **Software decode is the safe default for the tested tablet.** Forcing hardware
   decoding created multi-second queues. The client now defaults to software and includes
   a queue-based fallback from explicitly requested hardware when `decodeQueueSize > 8`
   for two consecutive one-second checks.
2. **The bottleneck is content-dependent.** Simple motion can reach near the 60 FPS cap;
   detailed/high-motion content overwhelms the tested tablet decoder/renderer.
3. **The 12 Mbps encoder setting is not sufficient evidence for quality or required
   network throughput.** An observed client telemetry spike reached 62-70 Mbps during a
   difficult-motion test; its instantaneous nature and instrumentation need validation.
4. **The 20 ms target is not met reliably.** Some short windows averaged 23-26 ms, app
   transitions averaged 33-45 ms, and complex scenes reached seconds of decode backlog.
5. **No OLED-like color/depth claim is supported.** The stream uses H.264 4:2:0 and the
   virtual display advertises SDR. Colorimetry, HDR pass-through, panel calibration,
   transfer function and end-to-end bit depth have not been comprehensively measured.
6. **No 60 FPS certification exists.** There is no long-duration 60-minute soak, thermal
   steady-state run, packet-loss test, multi-network test, or instrumented panel
   presentation test.

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
- stable performance over the supported Wi-Fi conditions, with measured packet loss,
  jitter, bandwidth, RSSI and channel;
- temperature, battery drain and power consumption reported over the full soak;
- successful reconnect after Termux process loss and phone reboot if those are claimed
  as supported scenarios.

These are proposed release gates, not already achieved results.
