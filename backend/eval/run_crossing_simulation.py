"""Entry/exit counting robustness on SIMULATED pedestrian traffic.

This does not measure the detector. It measures what happens AFTER detection:
the real ByteTrack tracker and the real CrowdAnalyticsProcessor are fed synthetic
detections with known ground truth, degraded the way real detections are
(missed people, box jitter, false positives, people hiding each other). It
answers "how many entries/exits does the counting logic get right when the
detector is X% reliable at Y frames per second?".

    cd backend
    python -m eval.run_crossing_simulation --out eval/results/crossing_simulation.json

Results are SIMULATION results. They must not be quoted as temple-camera accuracy.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.ai.analytics import CrowdAnalyticsProcessor  # noqa: E402
from app.ai.analytics_config import AnalyticsConfig  # noqa: E402
from app.ai.pipeline_types import PipelineResult  # noqa: E402
from app.ai.tracker import ByteTrackTracker, TrackerSettings  # noqa: E402
from app.ai.types import Detection  # noqa: E402
from app.models.enums import VisitorEventType  # noqa: E402
from app.services.runtime_events import TrackEventRecord  # noqa: E402
from eval.metrics import event_scores, iou_matrix  # noqa: E402

W, H = 1280, 720
LINE_Y = 0.55
T0 = datetime(2026, 10, 1, 6, 0, tzinfo=timezone.utc)


@dataclass
class Walker:
    pid: int
    start_t: float
    x0: float
    y0: float
    vx: float
    vy: float
    w: float
    h: float

    def foot(self, t: float) -> tuple[float, float]:
        dt = t - self.start_t
        return self.x0 + self.vx * dt, self.y0 + self.vy * dt

    def box(self, t: float) -> tuple[float, float, float, float]:
        fx, fy = self.foot(t)
        scale = 0.7 + 0.5 * fy / H          # perspective: nearer people look bigger
        w, h = self.w * scale, self.h * scale
        return fx - w / 2, fy - h, fx + w / 2, fy

    def crossing_time(self) -> tuple[str, float] | None:
        if self.vy == 0:
            return None
        t = self.start_t + (LINE_Y * H - self.y0) / self.vy
        return ("IN" if self.vy > 0 else "OUT"), t


@dataclass(frozen=True)
class Scenario:
    name: str
    people_per_minute: float
    two_way: bool
    detect_prob: float
    jitter_px: float
    false_positives_per_frame: float
    occlusion_drop: float     # chance a person hidden behind another is missed
    fps: float
    duration_s: float = 300.0


def make_walkers(sc: Scenario, rng: random.Random) -> list[Walker]:
    walkers, t, pid = [], 0.0, 0
    while t < sc.duration_s - 20:
        t += rng.expovariate(sc.people_per_minute / 60.0)
        down = (not sc.two_way) or rng.random() < 0.6
        # 0.45-0.9 body heights per second (~0.8-1.5 m/s for a 1.7 m adult)
        speed = rng.uniform(0.45, 0.9) * 125
        y0 = -10.0 if down else H + 10.0
        walkers.append(Walker(pid, t, rng.uniform(150, W - 150), y0,
                              rng.uniform(-15, 15), speed if down else -speed,
                              rng.uniform(40, 60), rng.uniform(110, 140)))
        pid += 1
    return walkers


def detections_at(t: float, walkers: list[Walker], sc: Scenario, rng: random.Random,
                  stamp: datetime) -> tuple[list[Detection], list[tuple[int, tuple]]]:
    visible = []
    for wk in walkers:
        if t < wk.start_t:
            continue
        x1, y1, x2, y2 = wk.box(t)
        if y1 > H or y2 < 0 or x2 < 0 or x1 > W:
            continue
        visible.append((wk.pid, (x1, y1, x2, y2)))
    # Occlusion: of two strongly overlapping people, the one further away (smaller y2) may vanish.
    hidden = set()
    if len(visible) > 1:
        import numpy as np

        boxes = np.array([b for _, b in visible])
        ious = iou_matrix(boxes, boxes)
        for i in range(len(visible)):
            for j in range(len(visible)):
                if i != j and ious[i, j] > 0.3 and visible[i][1][3] < visible[j][1][3]:
                    if rng.random() < sc.occlusion_drop:
                        hidden.add(i)
    dets = []
    for i, (_, (x1, y1, x2, y2)) in enumerate(visible):
        if i in hidden or rng.random() > sc.detect_prob:
            continue
        j = sc.jitter_px
        box = (x1 + rng.gauss(0, j), y1 + rng.gauss(0, j), x2 + rng.gauss(0, j), y2 + rng.gauss(0, j))
        try:
            dets.append(Detection.from_xyxy(box, rng.uniform(0.45, 0.95), 0, "person", stamp, (H, W)))
        except Exception:
            pass
    n_fp = sum(1 for _ in range(5) if rng.random() < sc.false_positives_per_frame / 5)
    for _ in range(n_fp):
        cx, cy = rng.uniform(50, W - 50), rng.uniform(100, H)
        dets.append(Detection.from_xyxy((cx - 25, cy - 60, cx + 25, cy), rng.uniform(0.3, 0.55),
                                        0, "person", stamp, (H, W)))
    return dets, visible


def run_scenario(sc: Scenario, seed: int) -> dict:
    rng = random.Random(seed)
    walkers = make_walkers(sc, rng)
    tracker = ByteTrackTracker("SIM", TrackerSettings(
        high_confidence=0.4, low_confidence=0.1,
        max_lost_frames=max(1, math.ceil(3.0 * sc.fps))))
    proc = CrowdAnalyticsProcessor(1, "SIM", AnalyticsConfig.model_validate(
        {"lines": [{"id": "GATE", "start": [0.0, LINE_Y], "end": [1.0, LINE_Y]}],
         "min_track_hits": 2}), persist_snapshots=False)

    predicted: list[tuple[str, float]] = []
    id_by_person: dict[int, set[int]] = {}
    t, step = 0.0, 1.0 / sc.fps
    while t < sc.duration_s:
        stamp = T0 + timedelta(seconds=t)
        dets, visible = detections_at(t, walkers, sc, rng, stamp)
        tracking = tracker.update(dets, stamp)
        # ID-switch bookkeeping: which track ids covered each true person
        if tracking.tracks and visible:
            import numpy as np

            ious = iou_matrix(np.array([b for _, b in visible]),
                              np.array([tr.bbox for tr in tracking.tracks]))
            for i, (pid, _) in enumerate(visible):
                j = int(ious[i].argmax())
                if ious[i, j] >= 0.5:
                    id_by_person.setdefault(pid, set()).add(tracking.tracks[j].track_id)
        result = PipelineResult("SIM", 0, stamp, True, tuple(dets), len(dets), tracking,
                                frame_shape=(H, W))
        for rec in proc.process(result):
            if isinstance(rec, TrackEventRecord) and rec.event_type in (
                    VisitorEventType.ENTERED, VisitorEventType.EXITED):
                predicted.append(("IN" if rec.event_type is VisitorEventType.ENTERED else "OUT", t))
        t += step

    truth = [c for wk in walkers if (c := wk.crossing_time()) and c[1] < sc.duration_s - 1]
    scores = event_scores(truth, predicted, tolerance=3.0)
    true_in = sum(1 for d, _ in truth if d == "IN")
    true_out = len(truth) - true_in
    pred_in = sum(1 for d, _ in predicted if d == "IN")
    pred_out = len(predicted) - pred_in
    switches = sum(len(ids) - 1 for ids in id_by_person.values() if ids)
    return {
        "scenario": sc.name, "seed": seed, "fps": sc.fps, "detect_prob": sc.detect_prob,
        "people_per_minute": sc.people_per_minute, "two_way": sc.two_way,
        "true_in": true_in, "pred_in": pred_in, "true_out": true_out, "pred_out": pred_out,
        "count_error_in_pct": round(100 * (pred_in - true_in) / true_in, 1) if true_in else None,
        "count_error_out_pct": round(100 * (pred_out - true_out) / true_out, 1) if true_out else None,
        "event_precision": round(scores.precision, 3), "event_recall": round(scores.recall, 3),
        "event_f1": round(scores.f1, 3),
        "people_tracked": len(id_by_person), "id_switches": switches,
        "id_switches_per_person": round(switches / max(1, len(id_by_person)), 3),
    }


SCENARIOS = [
    # name, ppm, two_way, p_detect, jitter, fp/frame, occlusion_drop, fps
    Scenario("low_density_good_light", 10, False, 0.95, 3, 0.05, 0.3, 5),
    Scenario("medium_two_way", 40, True, 0.90, 4, 0.10, 0.4, 5),
    Scenario("medium_two_way_3fps", 40, True, 0.90, 4, 0.10, 0.4, 3),
    Scenario("medium_two_way_2fps", 40, True, 0.90, 4, 0.10, 0.4, 2),
    Scenario("high_two_way", 120, True, 0.85, 5, 0.20, 0.5, 5),
    Scenario("high_two_way_poor_detector", 120, True, 0.65, 8, 0.40, 0.6, 5),
    Scenario("high_two_way_poor_detector_10fps", 120, True, 0.65, 8, 0.40, 0.6, 10),
]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--seeds", type=int, nargs="+", default=[1, 2, 3])
    p.add_argument("--out", required=True)
    args = p.parse_args(argv)
    rows = []
    for sc in SCENARIOS:
        for seed in args.seeds:
            row = run_scenario(sc, seed)
            rows.append(row)
            print(json.dumps(row), flush=True)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"kind": "SIMULATION - not camera accuracy", "runs": rows}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
