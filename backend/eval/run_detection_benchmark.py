"""Reproducible person-detection and counting benchmark.

Runs the SAME detector code the live pipeline uses (app.ai.YoloDetector through
ModelRegistry) on public datasets you supply, and writes a JSON report.

    cd backend
    python -m eval.run_detection_benchmark \
        --models ../weights/yolo11n.pt ../weights/yolo26n.pt \
        --imgsz 640 960 \
        --coco128 /data/coco128 \
        --mot /data/MOT17-mini/train \
        --out eval/results/baseline.json

What is measured
* COCO-style AP50 and AP50-95 for the person class (predictions at conf >= 0.001).
* Precision / recall / F1 at the deployed threshold (AI_CONFIDENCE, default 0.4) —
  what the live system would actually count.
* Per-frame count MAE / RMSE / bias on MOT frames (crowded street scenes).
* Inference latency (median / p95, includes Ultralytics pre/post-processing) on
  full-HD MOT frames at the deployed threshold, and peak process RSS.

What it does NOT prove: performance on temple cameras. Benchmark images differ
from temple scenes in viewpoint, lighting, clothing and density. See
docs/EVALUATION.md for the temple-camera protocol.
"""

from __future__ import annotations

import argparse
import json
import platform
import resource
import statistics
import sys
import time
from pathlib import Path

import numpy as np

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.ai.detector import DetectorSettings, YoloDetector  # noqa: E402
from app.ai.model_loader import ModelRegistry  # noqa: E402
from app.utils.time import utc_now  # noqa: E402
from eval.datasets import Sample, iter_mot_sequences, load_mot_sequence, load_yolo_split  # noqa: E402
from eval.metrics import ImageEval, count_errors, detection_report  # noqa: E402

AP_FLOOR = 0.001


def _predict(detector: YoloDetector, samples: list[Sample]) -> list[ImageEval]:
    import cv2

    evals = []
    for s in samples:
        img = cv2.imread(str(s.image_path))
        dets = detector.detect(img, utc_now())
        pred = np.array([d.bbox for d in dets]).reshape(-1, 4)
        scores = np.array([d.confidence for d in dets])
        evals.append(ImageEval(gt=s.gt, pred=pred, scores=scores, ignore=s.ignore))
    return evals


def _latency(detector: YoloDetector, samples: list[Sample], repeats: int) -> dict:
    import cv2

    images = [cv2.imread(str(s.image_path)) for s in samples]
    detector.detect(images[0], utc_now())  # warm
    times = []
    for _ in range(repeats):
        for img in images:
            detector.detect(img, utc_now())
            times.append(detector.last_inference_seconds)
    times.sort()
    return {
        "frames": len(times),
        "frame_size": f"{images[0].shape[1]}x{images[0].shape[0]}",
        "median_ms": round(statistics.median(times) * 1000, 1),
        "p95_ms": round(times[int(0.95 * (len(times) - 1))] * 1000, 1),
        "max_fps_single_camera": round(1.0 / statistics.median(times), 2),
    }


def run(args: argparse.Namespace) -> dict:
    coco = load_yolo_split(Path(args.coco128), args.coco_split) if args.coco128 else []
    mot: list[Sample] = []
    if args.mot:
        for seq in iter_mot_sequences(Path(args.mot)):
            mot.extend(load_mot_sequence(seq, min_visibility=args.min_visibility))

    registry = ModelRegistry(warmup=True)
    report: dict = {
        "created_at": utc_now().isoformat(),
        "environment": {
            "python": platform.python_version(),
            "machine": platform.machine(),
            "processor": platform.processor() or "unknown",
            "cpu_count": __import__("os").cpu_count(),
            "device": args.device,
        },
        "datasets": {
            "coco128_images": len(coco),
            "coco128_person_boxes": int(sum(len(s.gt) for s in coco)),
            "mot_frames": len(mot),
            "mot_pedestrian_boxes": int(sum(len(s.gt) for s in mot)),
            "mot_min_visibility": args.min_visibility,
        },
        "operating_threshold": args.conf,
        "threshold_sweep": args.sweep,
        "runs": [],
    }

    for model_path in args.models:
        for imgsz in args.imgsz:
            handle = registry.get(str(Path(model_path).resolve()), args.device, imgsz)
            floor = YoloDetector(handle, DetectorSettings(AP_FLOOR, args.iou, imgsz, ("person",)))
            live = YoloDetector(handle, DetectorSettings(args.conf, args.iou, imgsz, ("person",)))
            run_report: dict = {"model": handle.model_name, "imgsz": imgsz}
            started = time.monotonic()
            if coco:
                evals = _predict(floor, coco)
                run_report["coco128_person"] = {
                    str(t): detection_report(evals, t).as_dict() for t in args.sweep}
            if mot:
                evals = _predict(floor, mot)
                run_report["mot_person"] = {
                    str(t): detection_report(evals, t).as_dict() for t in args.sweep}
                true_counts = [len(e.gt) for e in evals]
                run_report["mot_count"] = {
                    str(t): count_errors(
                        true_counts, [int((e.scores >= t).sum()) for e in evals]).as_dict()
                    for t in args.sweep}
                run_report["latency_fullhd"] = _latency(live, mot, args.repeats)
            run_report["eval_seconds"] = round(time.monotonic() - started, 1)
            run_report["peak_rss_mb"] = round(
                resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 0)
            report["runs"].append(run_report)
            print(json.dumps(run_report), flush=True)
    return report


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--models", nargs="+", required=True, help="weight files (.pt)")
    p.add_argument("--imgsz", nargs="+", type=int, default=[640])
    p.add_argument("--coco128", help="YOLO-format dataset root (COCO128)")
    p.add_argument("--coco-split", default="train2017")
    p.add_argument("--mot", help="directory of MOTChallenge sequences")
    p.add_argument("--min-visibility", type=float, default=0.0)
    p.add_argument("--conf", type=float, default=0.4, help="deployed AI_CONFIDENCE")
    p.add_argument("--sweep", nargs="+", type=float, default=None,
                   help="extra confidence thresholds to report P/R/count at (default: --conf)")
    p.add_argument("--iou", type=float, default=0.5, help="NMS IoU (AI_IOU)")
    p.add_argument("--device", default="cpu")
    p.add_argument("--repeats", type=int, default=2, help="latency passes over MOT frames")
    p.add_argument("--out", required=True)
    args = p.parse_args(argv)
    args.sweep = sorted(set((args.sweep or []) + [args.conf]))
    if not args.coco128 and not args.mot:
        p.error("give --coco128 and/or --mot")
    report = run(args)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
