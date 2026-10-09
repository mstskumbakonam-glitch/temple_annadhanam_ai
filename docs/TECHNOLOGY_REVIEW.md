# Computer-vision technology review (October 2026)

Goal: pick what to use for a CPU-first temple crowd demo, preserving the existing
FastAPI + PostgreSQL + React design. Choices are made on **measured** results where we
could measure (see EVALUATION.md), and on licence, maintenance and hardware cost
otherwise. "Newer" alone was never a reason to switch.

How sources were used: vendor and paper links below are the references for each option.
Published accuracy/speed figures from those pages were **not re-verified in this
session** (the pages could not be opened from the build environment); where this
document states a number, it is our own measurement unless marked "vendor".

## Summary of decisions

| Area | Current choice | Decision | Why |
|---|---|---|---|
| Person detector | Ultralytics YOLO (v8n documented) | **Keep Ultralytics; YOLO11n or YOLO26n; tune image size per camera** | Same code path runs v8/11/26 (verified: real-model tests pass with YOLOv8n and YOLO26n). Measured: image size and threshold matter far more than model generation. |
| Tracker | ByteTrack (Ultralytics implementation, per-camera instance) | **Keep ByteTrack** | Accurate enough at ≥ 5 FPS in simulation (0–3 % crossing error); no appearance model needed, so no biometric-like data. |
| Re-identification across cameras | none | **Do not add** | No requirement that justifies it; privacy and lawful-basis questions; would need evaluation data. |
| Crowd counting in packed areas | box counting only | **Add later, behind a flag, after temple data exists** | Box detectors cannot count people they cannot see (main measured error). |
| Queue length / wait | none | **Implemented**: zone count + dwell + Little's-law estimate | Simple, explainable, no extra model. |
| Entry/exit | none | **Implemented**: directional virtual line on feet point, hysteresis, cooldown | Standard approach; measured in simulation. |
| Camera health / reconnect | already good | **Kept**, plus continuity epochs so tracks never jump across a gap | Fixed a real artifact found in testing. |
| Alert delivery | none | **Implemented**: debounced alerts in PostgreSQL + dashboard | Push to phones/SMS is a later step (needs provider + policy). |

## 1. Person detection

| Option | Licence | CPU fit | Notes |
|---|---|---|---|
| **Ultralytics YOLOv8 / YOLO11 / YOLO26** (n, s) | AGPL-3.0, or paid Ultralytics Enterprise licence | Good (nano: 70–80 ms per full-HD frame at 640 px on 2 vCPU, measured) | YOLO26 (Jan 2026) is end-to-end/NMS-free and aimed at edge CPUs (vendor). Already integrated; weights load offline. |
| RF-DETR (Roboflow) | Apache-2.0 for the base models (vendor) | Heavier on CPU (transformer) | Strong accuracy claims (vendor); would need a new detector adapter and a fresh evaluation. Good candidate if the AGPL licence is a problem. |
| RT-DETR / D-FINE family | Apache-2.0 (most implementations) | Heavier on CPU | Same trade-off as RF-DETR. |

**Licence warning (important).** The installed `ultralytics` package declares
`AGPL-3.0` (checked in package metadata). If this system is offered to others over a
network, AGPL obligations apply to the whole service, or an Enterprise licence is
needed. For an internal temple deployment run by the temple itself, discuss this with
whoever will own the system. RF-DETR (Apache-2.0) is the fallback if AGPL is
unacceptable; it would be a contained change (`app/ai/detector.py` adapter).

Measured (EVALUATION.md §3): on crowded frames YOLOv8n, YOLO11n and YOLO26n were within
noise of each other; image size 640 → 1280 halved counting error; small (s) models
cost ~3x the time for a modest gain. → Use a nano model, choose image size per camera.

## 2. Multi-object tracking

| Option | Uses appearance (Re-ID)? | Notes |
|---|---|---|
| **ByteTrack** | No | Two-stage association using low-confidence boxes; good in crowds; cheap. Current. |
| BoT-SORT | Optional | Adds camera-motion compensation and optional Re-ID; heavier; motion compensation is irrelevant for fixed CCTV. |
| OC-SORT / Deep OC-SORT | Deep variant yes | Better under non-linear motion and occlusion; Ultralytics 8.4 now ships `ocsort.yaml`, `deepocsort.yaml` (verified in the installed package). |
| BoxMOT library | Varies | Many trackers in one package; AGPL-3.0. |

Decision: keep ByteTrack. The simulation shows crossings are lost mainly when the
processing rate is too low, not because ByteTrack itself is weak at 5 FPS. OC-SORT is
the first alternative to evaluate if temple clips show identity switches in the queue —
it plugs into the same `ByteTrackTracker` wrapper pattern.

## 3. Person re-identification across cameras

Not implemented, by choice. Cross-camera Re-ID builds appearance embeddings of
individuals, which is close to biometric processing; it needs a stated purpose, a lawful
basis under India's DPDP Act 2023, consent/notice, retention rules and labelled
evaluation data. The current requirements (counts, queues, entries/exits, safety alerts)
do not need it. Revisit only with a concrete need (e.g. "average time from gate to
serving counter") and a privacy review.

## 4. Crowd counting and density

* **Box counting (current)** is accurate for sparse/medium scenes and explainable,
  but undercounts when people hide each other (measured: most of the remaining error).
* **Density-map / point-based counting models** (e.g. CSRNet, DM-Count, P2PNet) estimate
  counts in packed crowds without needing every person visible. Public training data
  (ShanghaiTech, UCF-QNRF, JHU-Crowd++, NWPU-Crowd) is mostly non-commercial research
  licensed, so check before use. They need their own evaluation on temple frames.
* **Density in people per m²** is what crowd-safety guidance uses. Zones accept
  `area_m2` so thresholds can be set in people/m² once the floor area under each zone
  is measured on site.

## 5. Queue length and wait time

Implemented without a new model: queue length = confirmed people whose feet are inside
the queue polygon; observed wait = average dwell of people who left in the last
10 minutes; estimated wait = queue length ÷ recent throughput (Little's law). Limits:
identity switches inside a dense queue shorten measured dwell; the estimate needs a few
minutes of service to stabilise. Both are shown separately on the dashboard.

## 6. Entry/exit counting

Implemented: a virtual line in frame fractions; a crossing needs a confirmed track,
a clear side change beyond a hysteresis band, and movement through the drawn segment;
per-direction cooldown suppresses flicker. Simulation: 0–3 % error at ≥ 5 FPS,
large undercount at ≤ 2 FPS. Best physical setup: camera looking down at the gate,
people at least ~100 px tall, line in the middle of the frame.

## 7. Occlusion and crowded scenes

Mitigations in software: larger inference size, lower confidence threshold, feet-point
anchoring, ByteTrack's low-score association. Not solvable in software: people fully
hidden. Physical mitigations (camera height/angle) are the most effective and cheapest.

## 8. Camera health and reconnection

Existing design kept (separate capture thread, latest-frame buffer, exponential back-off
with jitter, transition-only events, stale-frame skipping). Added: stale-feed flag in
the API, per-frame continuity epoch so tracking restarts cleanly after any gap.
Untested here: real RTSP cameras and real network faults (no camera available).

## 9. Real-time events and alert delivery

Alerts are debounced (raise after N seconds, clear after M seconds, single row per
alert, escalation once), stored in `crowd_alerts`, shown on the dashboard within ~2 s
(polling) and can be acknowledged by an admin. Not yet: push notifications (SMS /
WhatsApp / email). Those need a provider account, a message policy and protection
against alert storms; the alert table is the integration point.

## References

* Ultralytics YOLO26 documentation — https://docs.ultralytics.com/models/yolo26/
* Ultralytics YOLO26 paper — https://arxiv.org/pdf/2606.03748
* Ultralytics tracking (ByteTrack, BoT-SORT) — https://docs.ultralytics.com/modes/track/
* Ultralytics licensing (AGPL-3.0 / Enterprise) — https://www.ultralytics.com/license
* Ultralytics model weights releases — https://github.com/ultralytics/assets/releases
* RF-DETR documentation — https://rfdetr.roboflow.com and https://pypi.org/project/rfdetr/
* P2PNet: Rethinking Counting and Localization in Crowds — https://arxiv.org/pdf/2107.12746
* NWPU-Crowd benchmark — https://ar5iv.arxiv.org/html/2001.03360
* JHU-CROWD++ dataset — https://arxiv.org/abs/2004.03597
* MOTChallenge MOT17 (benchmark data used here, CC BY-NC-SA 3.0) — https://motchallenge.net/data/MOT17/
* BoxMOT (source of the MOT17-mini test frames) — https://github.com/mikel-brostrom/boxmot
