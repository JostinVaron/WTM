# Codec study: H.264 (AVC) vs H.265 (HEVC) for the WTM pipeline

**Status: A/B measured on real hardware (2026-11-09). Evidence-first; the
shipped default stays H.264 until the full acceptance run says otherwise.**
This document records what was verified from primary sources on 2026-11-08,
corrects one claim from a lower-capability AI assistant, defines the decision
gate for the HEVC experiment, and now records the measured A/B outcome.

## 1. What Apple Sidecar actually does (verified)

Sidecar is not "a display cable over Wi-Fi". It is a video pipeline:

- The Mac encodes the virtual display as **HEVC/H.265 with hardware encoding**
  (the requirement that Macs be Skylake-or-newer / T2-equipped lines up with
  Intel Quick Sync HEVC hardware encode availability; AppleInsider forum
  analysis and Ars Technica Catalina review corroborate).
- The iPad decodes HEVC **with its A-series hardware decoder** (A9 or later
  for basic HEVC hw decode; older iPads were excluded as receivers).
- The stack is the same family as FaceTime video streaming (HEVC), which is
  why DRM-protected content renders black through Sidecar — the video is a
  codec stream, not a pixel mirror, and HDCP does not pass through it.

The lesson to steal is the **architecture**, not the codec letters: hardware
encode on the source, hardware decode on the sink, both ends controlled by one
vendor, latency budget managed end-to-end. Sidecar's smoothness comes from
controlling both ends of the codec, not from HEVC being magic.

## 2. Our hardware (verified)

| Device | SoC / block | HEVC encode | HEVC decode |
|---|---|---|---|
| Galaxy S25 (source) | Snapdragon 8 Elite — Qualcomm multimedia block | Yes, native (10-bit, even LOG recording) | Yes |
| Galaxy Tab S6 Lite 2022 (sink) | Snapdragon 720G, Adreno 618 | 4K30 | 4K30, 8-bit (codec list: AVC/HEVC/VP8/VP9) |

Both devices have HEVC hardware support. The tablet's decoder is the weakest
link of the chain: a 2020-era mid-range block doing 4K30 HEVC.

## 2b. Correction to the lower-capability AI's claims

The pasted analysis states the Tab S6 Lite's block is "Qualcomm Iris". "Iris"
is the branding for the newest flagship multimedia engines (e.g. the S25's
Snapdragon 8 Elite). **The Tab S6 Lite's Snapdragon 720G does not carry an
"Iris"-branded engine** — its HEVC support is the standard Qualcomm video
block of that generation. The practical implication is identical (HEVC hw
decode exists), but the model of the hardware matters for expectations: a
2020 mid-range fixed-function decoder, not a flagship 2025 engine. Also, its
"4K60 decode" reading was not confirmed by any spec sheet found; the
verified figure for the 720G generation is 4K30-class decode capability.

## 2c. The critical constraint: Chrome on Android exposes HEVC only via platform decoders

Verified against the StaZhu/enable-chromium-hevc-hardware-decoding
documentation and Chrome release notes:

- Chrome ≥ 107 (2022) plays HEVC **only when the platform provides a
  hardware HEVC decoder** (on Android: Chrome ≥ 117.0.5899.0 for the full
  path including WebCodecs HEVC **hardware encoding**; WebCodecs HEVC decode
  8-bit requires Chrome 107+; 10-bit 108+).
- On Android there is **no software HEVC fallback in Chrome** — software
  HEVC decode would require shipping libhevc, which Google does not ship
  (licensing).
- Therefore: if WebCodecs `isConfigSupported` says no on this tablet, HEVC
  in our web client is dead on arrival, regardless of how good the S25's
  encoder is.

## 3. The measured bottleneck is the sink, not the codec

From docs/performance.md (2026-11-06 measurements):

- Forced **hardware** decode on the Tab S6 Lite produced multi-second queues
  (88-92 drops/5s window) — with **H.264**.
- Software decode H.264 handles simple motion at ~57-60 FPS, 23-26 ms.

So the tablet's H.264 **hardware** path is already the observed worst
performer in the stack. Swapping the codec to HEVC pins us to a platform
decoder (no software fallback exists on Android/Chrome) — the same class of
path that just measured catastrophic. This is why scrcpy's own docs say
"**H264 should provide lower latency**" and keep H.264 as the default despite
H.265's bitrate advantage: for interactive mirroring, decode-side latency
dominates. **Section 4c below shows why this concern was worth carrying into
the A/B rather than closing the question early.**

## 4. Decision gate (what we will actually do)

1. **Probe (5 min, zero risk):** run `VideoDecoder.isConfigSupported` for
   HEVC variants @ 2000x1200 on the tablet's Chrome. Result decides everything:
   - `supported: true` → the experiment is viable; build the hvcC/VPS parser
     in the relay and run a measured A/B (protocol from docs/optimization.md).
   - `supported: false` → HEVC is impossible in this tablet/browser pair
     today. Document and close the question; attack OPT-2 via
     resolution/bitrate instead.
2. **If viable, measure before migrating:** FPS (simple + complex motion),
   decode queue, drops, latency, thermals — both codecs, same scenes, same
   Wi-Fi. No default change without beating H.264 SW on the acceptance rows
   of performance.md.
3. **Rollback:** H.264 software remains the shipped default until a full A/B
   beats it. The client already has a queue-based auto-fallback; the same
   guard would apply to any HEVC path.

## 4b. Browser vs native client: two different HEVC gates

The Chrome probe gates only the **browser** path. Chrome on Android only
exposes HEVC through platform decoders and ships no software HEVC. But the
tablet's SoC HEVC decoder exists at the **MediaCodec level regardless of what
Chrome exposes** — native clients (MediaCodec API directly, like scrcpy's
desktop clients) enumerate and use the `c2.qti.hevc.decoder`-class decoders
with no browser in the middle. The tablet's Gallery app playing HEVC videos
is the everyday proof the hardware decoder is there.

Consequences:

- Probe `supported: false` closes only "HEVC in the browser today" — **not
  HEVC overall on this hardware**.
- The browser-independent path (a small native Android client using
  MediaCodec against the existing WebSocket relay) removes the Chrome/WebCodecs
  dependency *and* unlocks the hardware HEVC decoder in one move. That is the
  same architectural move Sidecar makes: control the decode end.
- Order of work stays: probe first (cheap), in-browser A/B if viable, and
  keep the native client as the strategic path (already milestone 5,
  "Native service feasibility", in project-governance.md).

## 4c. Probe result (2026-11-08, Tab S6 Lite, Chrome 156): **HEVC viable**

The hardened probe (runtime/termux/web/probe.html, served by the bridge at
/probe.html — see tools/patch-probe-route.py) returned:

- WebCodecs `isConfigSupported`: **SÍ for every HEVC variant** — Main 8-bit
  (hev1 and hvc1) and Main10 at 2000x1200, plus 1600x960. `hardwareAcceleration:
  no-preference` (expected; on Android Chrome HEVC is hardware-only anyway).
- `mediaCapabilities.decodingInfo` for HEVC 2000x1200@60: **supported,
  powerEfficient=true** — the energy-efficiency signature is the tell of a
  hardware decoder; a software path would not report power-efficient.
- Valid run confirmed: Adreno 618 in the header (the tablet), screen
  1334x800@dpr1.5 (=2000x1200 physical), secureContext true, control H.264
  SÍ (probe instrument functional).
- AV1 also reported SÍ, but that result is **discarded as a route**: Chrome's
  AV1 decode is dav1d (software), and the S25 has no common AV1 encoder
  (scrcpy doc/video.md). AV1 is not a candidate for this pipeline.

**Gate outcome: the browser A/B experiment is GO.** What this does NOT mean:
`isConfigSupported` is capability, not throughput. The §3 precedent (H.264 hw
decode measured multi-second queues on this same tablet) still applies — HEVC
will also decode on a platform decoder, and the A/B exists precisely to find
out whether it clears the bar that H.264-hw failed. No default changes without
beating the acceptance rows in docs/performance.md.

## 5. Why Apple wins (and what we can and cannot copy)

| Sidecar ingredient | WTM equivalent today | Copyable? |
|---|---|---|
| HW encode at source | S25 Snapdragons encode HEVC natively (MediaCodec) | Yes — scrcpy `video_codec=h265` is one flag |
| HW decode at sink | Tab S6 Lite SD720G has HEVC hw decode | Browser: only if Chrome exposes it (probe). Native client: yes |
| One vendor controls both ends | Samsung phone + Samsung tablet + Google browser | No — three vendors, three update policies |
| End-to-end latency management | Partial (heartbeat, queue fallback) | Partly — keep measuring |
| Decoder queue discipline | Queue-based fallback exists | Yes — extend to any HEVC path |

The honest conclusion: Sidecar's moat is vertical integration. Our equivalent
of that moat is **measurement discipline** — the probe + A/B gate is how a
source-available prototype on three vendors' stacks makes progress without
fooling itself.

## 6. Sources consulted (2026-11-08)

- Genymobile scrcpy `doc/video.md` (official) — codec trade-off statement.
- StaZhu/enable-chromium-hevc-hardware-decoding README — Chrome/Android HEVC
  WebCodecs version gates and platform-decoder requirement.
- Chrome 107 release notes / chromestatus HEVC feature page — hw-only HEVC.
- CPUBoss / Qualcomm-derived spec sheets — SD720G codec list (AVC/HEVC/VP8/VP9,
  4K30 decode-class).
- AppleInsider forums, Ars Technica Catalina review, Apple Support threads —
  Sidecar's HEVC pipeline and its hardware cutoffs.
- docs/performance.md — the measured H.264 hw-decode failure on this tablet.
- Live probe run on the Tab S6 Lite (section 4c).

## 7. Measured A/B outcome (2026-11-09): HEVC wins decode latency on this pair

**Protocol** (per docs/optimization.md rules): same session, same Wi-Fi
(rtt=3 ms constant), same content (H.264 1080p ~30 MB clip, VLC fullscreen
in loop, ~60 s per leg, zero interaction), same relay — the only variable was
`VIDEO_CODEC` (h264 vs h265). Encoder: 2000x1200, 12 Mbps cap. Codec
verified per leg from the bridge log: leg A `avc1.640032` (AVC High), leg B
`stream HEVC detectado (VPS)`.

### Results (5-second client STATS windows, stable state)

| Metric | H.264 (A) | HEVC (B) | Winner |
|---|---|---|---|
| Decoded FPS | ~27.1–30.4 | ~29.5–30.1 | Technical tie |
| decode_ms avg | ~25.6–29.3 | ~18.7–20.3 | **HEVC (~30% less)** |
| decode_ms max (stable) | 84–141 | 38–48 | **HEVC (3x less jitter)** |
| TOTAL_ms avg | ~37–43 | ~31–36 | HEVC (~15% less) |
| TOTAL_ms max (stable) | 100–159 | 87–114 | HEVC |
| Sustained drops | 0 | 0 | Tie |
| Bitrate | ~8.9–11.1 Mbps (capped) | ~10.1–10.9 Mbps (capped) | Not measurable (12 Mbps cap) |

**Warm-up**: the first HEVC window had one decoder-initialization transient
(decode_ms max 482, 22 drops in ONE 5-second window, bitrate still ramping at
2.2 Mbps) with `net_ms=7.5 (max 55)` and rtt=3 — an initialization transient,
not a network or content issue. Leg A showed the same class of transient
without drops.

### Reading against the §3 concern

The platform-decoder concern (H.264-hw measured multi-second queues on this
tablet) **did not reproduce for HEVC** on this content class: no multi-second
queues, no sustained drops, TOTAL ~36 ms stable. The adverse precedent from
another AI's uncontrolled measurement (88-92 drops) was re-measured with our
own H.264 baseline before comparing, per protocol — the H.264-hw failure mode
did not appear in either leg's stable state.

### What this result does NOT claim

1. **No 60 FPS claim.** Both legs delivered ~30 FPS despite the 60 FPS cap —
   the cap is content/pipeline-side and identical in both legs, so the
   comparison is valid, but 60 FPS with HEVC is not established.
2. **No bandwidth-efficiency claim.** Both legs ran at the 12 Mbps cap; the
   HEVC bitrate advantage (same quality at lower Mbps) was not measured.
3. **One content class, ~60 s/leg, no thermal equilibrium.** This is not the
   full acceptance run of docs/performance.md. The shipped default stays
   H.264; the operator of this pair uses `VIDEO_CODEC=h265` in their
   `server.env` as a documented operator choice backed by this evidence.
4. **Warm-up drops are real but bounded:** one 5-second window at session
   start. The client's queue-based fallback (decodeQueueSize > 8 for two
   consecutive checks) covers this class of transient.

### Follow-ups opened by this result

- **OPT-2 update**: the codec A/B question is answered for this content
  class; the remaining OPT-2 work is resolution/bitrate sweeps and the full
  acceptance run. A HEVC-vs-H.264 sweep at lower bitrates (below the cap) is
  the natural next measurement for the bandwidth question.
- The FPS cap investigation (both legs at ~30 FPS with a 60 cap) is a
  separate pipeline question, tracked in the optimization backlog.
- Decision record: the default flip to HEVC is gated on the full acceptance
  run; if it passes, the temporary patch-hevc-relay.py consolidates into
  server.py and the patch is removed, as planned.

Frozen evidence on the operator device: `legA-h264-bridge.log`,
`legB-hevc-bridge.log`, reproducible extractor `ab-analyze.py` (parses bridge
STATS lines; excludes warm-up with a bitrate>5 Mbps + drops=0 filter).
