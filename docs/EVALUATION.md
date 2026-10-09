# Accuracy and performance evaluation

**Read this first.** Every number below was measured on *public benchmark data* or a
*simulation*, on one small CPU machine. None of it is a measurement on a temple camera.
Benchmark scenes (city streets, COCO photos) differ from an annadhanam hall in viewpoint,
lighting, clothing, density and occlusion. Use these numbers to choose settings and to
see what limits accuracy, not to promise an accuracy figure to anyone. The protocol at
the end of this page is how to measure the real thing.

Raw results: `backend/eval/results/*.json`. Every run is reproducible with the commands
shown.

## 1. Test machine

| Item | Value |
|---|---|
| CPU | 2 vCPU (x86_64, cloud sandbox), **no GPU** |
| RAM | 7 GB |
| Python / PyTorch / Ultralytics | 3.13 / 2.14 (CPU) / 8.4.174 |
| Date | 9 Oct 2026 |

A typical temple PC (4–8 cores) will be faster; an NVIDIA GPU would be 10–30x faster.
Re-run the benchmark on the real machine before deciding settings.

## 2. Data used

| Dataset | What it tests | Size | Caveat |
|---|---|---|---|
| COCO128 (Ultralytics sample of COCO train2017) | General person detection | 128 images, 254 person boxes | These images are from COCO **train**, which the YOLO models were trained on: results are **optimistic**. Only useful for comparing settings. |
| MOT17-02 and MOT17-04 frames (from BoxMOT's `MOT17-mini` test assets) | Crowded pedestrian scenes, elevated camera — closest public match to a hall/queue camera | 12 full-HD frames, 424 pedestrians (avg 35 per frame) | Tiny sample; two scenes only. Licence CC BY-NC-SA 3.0 (evaluation use only). |
| Simulated pedestrian traffic | Entry/exit line counting after detection | 18 runs x 5 min | Synthetic: tests the tracking and counting logic, not the camera or detector. |

Ground truth for MOT17 follows the MOTChallenge rules: pedestrians marked "consider"
are scored; static people, distractors, reflections and people on vehicles are
"don't care" (a detection there is neither right nor wrong).

## 3. Person detection and counting (crowded frames)

Command:

```bash
cd backend
python -m eval.run_detection_benchmark \
  --models <weights>/yolov8n.pt <weights>/yolo11n.pt <weights>/yolo26n.pt <weights>/yolo11s.pt <weights>/yolo26s.pt \
  --imgsz 640 960 1280 --sweep 0.25 0.3 0.4 0.5 \
  --coco128 <data>/coco128 --mot <data>/MOT17-mini/train \
  --out eval/results/benchmark_cpu_2026-10.json
```

### Baseline (the repository's documented setup: YOLOv8n, 640 px, confidence 0.4)

| Metric (MOT17 crowded frames) | Value |
|---|---|
| Precision at 0.4 | 0.90 |
| Recall at 0.4 | **0.34** (two of every three pedestrians missed) |
| AP50 / AP50-95 | 0.595 / 0.315 |
| Count error (MAE), people per frame | **21.8** of 35 on average (bias −21.8: always under) |
| Latency per full-HD frame (median / p95) | 76 ms / 85 ms |

### Full comparison (MOT17 crowded frames, all pedestrians)

| Model | Image size | AP50 | P / R at 0.25 | P / R at 0.4 | Count MAE at 0.25 | Count MAE at 0.4 | Median ms |
|---|---|---|---|---|---|---|---|
| YOLOv8n | 640 | 0.595 | 0.87 / 0.40 | 0.90 / 0.34 | 18.7 | 21.8 | 76 |
| YOLOv8n | 960 | 0.643 | 0.88 / 0.50 | 0.90 / 0.44 | 14.0 | 17.2 | 150 |
| YOLOv8n | 1280 | 0.680 | 0.85 / 0.59 | 0.90 / 0.51 | 9.2 | 14.6 | 253 |
| YOLO11n | 640 | 0.534 | 0.91 / 0.32 | 0.91 / 0.22 | 22.4 | 26.4 | 82 |
| YOLO11n | 960 | 0.623 | 0.86 / 0.49 | 0.90 / 0.35 | 14.8 | 21.2 | 143 |
| YOLO11n | 1280 | 0.680 | 0.84 / 0.63 | 0.88 / 0.53 | **7.3** | 13.7 | 261 |
| YOLO26n | 640 | 0.529 | 0.87 / 0.25 | 0.86 / 0.21 | 24.7 | 26.4 | 69 |
| YOLO26n | 960 | 0.608 | 0.87 / 0.39 | 0.90 / 0.32 | 18.4 | 22.5 | 138 |
| YOLO26n | 1280 | 0.668 | 0.87 / 0.62 | 0.89 / 0.52 | 8.6 | 14.2 | 273 |
| YOLO11s | 1280 | 0.698 | 0.85 / 0.61 | 0.92 / 0.52 | 7.3 | 14.2 | 714 |
| YOLO26s | 1280 | 0.679 | 0.87 / 0.64 | 0.91 / 0.57 | 7.1 | 12.2 | 738 |

(640/960 rows of the "s" models are in the JSON file; they follow the same pattern.)

On COCO128 (optimistic, see caveat) all nano models reach AP50 ≈ 0.75–0.80 and the
small models ≈ 0.80–0.84; precision at 0.4 is ≥ 0.87 everywhere.

### Only clearly visible people (visibility ≥ 50 %)

The MOT ground truth includes people who are almost completely hidden. Scoring only
people at least half visible (244 pedestrians, avg 20 per frame) separates "the
detector is weak" from "the person cannot be seen":

| Model, 1280 px | Recall at 0.4 | Count MAE at 0.4 |
|---|---|---|
| YOLOv8n | 0.82 | 0.9 |
| YOLO11n | 0.82 | 1.8 |
| YOLO26n | 0.81 | 1.4 |

At 640 px recall of visible people is only 0.34–0.56. (`eval/results/benchmark_cpu_2026-10_vis050.json`)

### What this means, in plain words

1. **Input image size matters more than the model generation.** Going from 640 to
   1280 px roughly halves the counting error on crowded wide shots, because people far
   from the camera are only a few dozen pixels tall.
2. **A lower confidence threshold (0.25–0.3) helps crowded scenes**: recall rises a lot
   while precision stays around 0.85.
3. **YOLO11 / YOLO26 were not consistently better than YOLOv8 on these frames.** The
   sample is too small to call a winner; there is no evidence here that switching models
   alone fixes undercounting.
4. **Heavily occluded people are the main remaining gap.** No box detector counts people
   it cannot see. For a packed hall, a density-map crowd-counting model (Section 6 of
   the technology review) or a better camera angle is needed.
5. **Cost:** 1280 px takes ~260 ms per frame on this 2-core CPU, so one camera can be
   processed at about 3–4 FPS. That conflicts with entry/exit counting (next section).

### End-to-end resource use (demo run)

One recorded 640x360 camera, YOLO11n at 640 px, 5 AI FPS, preview on, on the 2-vCPU
machine: measured AI rate 4.99 FPS, inference 77–111 ms per frame, backend process
~976 MB RSS and ~0.5 of one CPU core averaged over 6 minutes. Recovery after a real
RTSP disconnect was **not** measured (no camera available); the reconnect logic is
covered by scripted-stream tests only.

## 4. Entry/exit counting (simulation)

Command: `python -m eval.run_crossing_simulation --out eval/results/crossing_simulation.json`

The real ByteTrack tracker and the real line-crossing code are fed synthetic
detections of people walking through a gate (0.8–1.5 m/s), with missed detections, box
jitter, false positives and people hiding each other. Three random seeds per scenario.

| Scenario | Detector quality | AI FPS | Entry count error | Exit count error | Event precision / recall |
|---|---|---|---|---|---|
| Low density, one way | 95 % found | 5 | 0 % | — | 0.98 / 0.98 |
| Medium, two-way (40/min) | 90 % | 5 | 0 to −3 % | 0 % | 0.98 / 0.98 |
| Medium, two-way | 90 % | **3** | −7 to −8 % | −1 to −4 % | 0.98 / 0.92 |
| Medium, two-way | 90 % | **2** | **−51 to −62 %** | −15 to −33 % | 0.99 / 0.57 |
| High, two-way (120/min) | 85 % | 5 | −2 to −3 % | −1 % | 0.97 / 0.95 |
| High, two-way, poor detector | 65 % | 5 | −19 to −21 % | −9 to −11 % | 0.98 / 0.83 |
| High, two-way, poor detector | 65 % | 10 | −1 to −3 % | −1 to −2 % | 0.97 / 0.95 |

What it shows:

* **Processing rate is the deciding factor for line counting.** Below ~4 FPS, people
  move too far between frames for the tracker to keep their identity and crossings are
  lost. 5 FPS is enough with a good detector; a weaker detector needs more frames.
* Counting errors are almost always **missed** crossings, not invented ones (precision
  stays ≥ 0.96): the hysteresis band and the "confirmed track" rule work as intended.
* A person must be tracked for about one second before they reach the line. **Place the
  line well inside the picture**, not near the edge where people first appear.

Because of the conflict between Sections 3 and 4, cameras can now be tuned
individually (`analytics_config.tuning`): gate cameras small and fast
(640 px, ≥ 5 FPS), hall/queue cameras large and slow (960–1280 px, 1–2 FPS).

## 5. Before / after

| Item | Before | After | Evidence |
|---|---|---|---|
| Count MAE, crowded MOT frames (all pedestrians) | 21.8 (YOLOv8n 640, conf 0.4) | 7.3 (YOLO11n 1280, conf 0.25) — via per-camera tuning | benchmark JSON |
| Recall of clearly visible people | 0.56 (v8n 640 @0.4) | 0.81–0.87 (1280 px, conf 0.25–0.4) | vis050 JSON |
| Entry/exit counting | not implemented | 0–3 % error at ≥ 5 FPS in simulation | crossing_simulation.json |
| Density / queue / alerts | not implemented | implemented, unit-tested (deterministic) | tests/test_analytics.py |
| Recorded-video loop artifact | — | found and fixed: tracks were continued across the loop cut; tracking now restarts with a continuity epoch carried by every frame | tests/test_demo_video.py |

The "after" detection numbers come from configuration, not new model code: the
improvement is that the system can now run each camera at the settings its job needs.

## 6. What limits accuracy — software or site?

| Limitation | Software fixable? | What helps |
|---|---|---|
| Far-away people too small | Partly (larger image size, tiling) | Camera mounted higher and closer; 1080p or better; zoom on the queue |
| People hidden behind others | No, for box detectors | Overhead or steep angle (≥ 45°); density-map counting model for packed areas |
| Missed crossings at low FPS | Yes, with more compute | GPU, or fewer/lower-resolution streams per CPU; line placed mid-frame |
| Backlight from doorways, night lighting | Little | Camera WDR/BLC settings, IR or added lighting, avoid facing sunlit doors |
| Unusual appearance (veshti/saree, dense similar clothing, children carried) | Yes, with data | Fine-tune on a few hundred labelled temple frames (needs consent and labelling effort) |
| Camera moved / re-aimed | — | Lines and zones are in frame fractions; redraw after any physical change |

## 7. Protocol for measuring on temple cameras

Do this before telling anyone how accurate the system is.

1. **Permission first.** Written approval from temple management; signs at entrances;
   recordings stored encrypted, accessed by named people, deleted after evaluation.
2. **Record clips** (2–5 min each, original resolution) per camera covering:
   low / medium / high crowd; morning, noon, evening and night lighting; backlit
   doorway; two-way traffic at the gate; the queue at its longest; one clip after
   unplugging the camera network cable for 30 s (recovery).
3. **Label ground truth** with a free tool (CVAT or Label Studio):
   * counting: number of people in each zone every 5 s;
   * gate: every crossing with time and direction;
   * queue: queue length every 30 s;
   * alerts: the periods a supervisor would call "too crowded".
4. **Run** each clip as a `demo://` camera with the candidate settings and export
   `visitor_events`, `crowd_count_snapshots` and `crowd_alerts` for the clip window.
5. **Report separately** from the benchmark numbers: count MAE/RMSE per zone and
   density, entry/exit error %, alert precision/recall and false alarms per hour,
   FPS and CPU/RAM on the actual PC, and time to recover after the cable test.
6. **Set targets only after this baseline exists**, e.g. "gate count within ±5 % at
   peak", "no more than one false crowd alert per day".
