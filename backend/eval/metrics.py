"""Evaluation metrics, implemented in plain NumPy so they can be unit-tested.

Detection
    match_detections   greedy, confidence-ordered matching at an IoU threshold
    average_precision  COCO-style 101-point interpolated AP
    detection_report   precision / recall / F1 / AP50 / AP50-95 over many images

Counting
    count_errors       MAE, RMSE, mean signed error (bias), relative MAE

Events (line crossings, alerts)
    event_scores       precision / recall / F1 of predicted vs ground-truth events
                       matched within a time tolerance

Nothing here knows about YOLO or the database.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

import numpy as np

COCO_IOU_THRESHOLDS = tuple(np.round(np.arange(0.5, 0.96, 0.05), 2).tolist())


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------
def iou_matrix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """IoU between every box in `a` (N,4) and every box in `b` (M,4), xyxy."""
    a = np.asarray(a, dtype=np.float64).reshape(-1, 4)
    b = np.asarray(b, dtype=np.float64).reshape(-1, 4)
    if len(a) == 0 or len(b) == 0:
        return np.zeros((len(a), len(b)))
    x1 = np.maximum(a[:, None, 0], b[None, :, 0])
    y1 = np.maximum(a[:, None, 1], b[None, :, 1])
    x2 = np.minimum(a[:, None, 2], b[None, :, 2])
    y2 = np.minimum(a[:, None, 3], b[None, :, 3])
    inter = np.clip(x2 - x1, 0, None) * np.clip(y2 - y1, 0, None)
    area_a = (a[:, 2] - a[:, 0]) * (a[:, 3] - a[:, 1])
    area_b = (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1])
    union = area_a[:, None] + area_b[None, :] - inter
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(union > 0, inter / union, 0.0)


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------
@dataclass
class ImageEval:
    """Ground truth and predictions for one image."""

    gt: np.ndarray                          # (G,4) xyxy boxes that must be found
    pred: np.ndarray                        # (P,4) xyxy predicted boxes
    scores: np.ndarray                      # (P,)
    ignore: np.ndarray = field(default_factory=lambda: np.zeros((0, 4)))  # don't-care regions


def match_detections(
    gt: np.ndarray,
    pred: np.ndarray,
    scores: np.ndarray,
    iou_threshold: float,
    ignore: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Greedy matching in descending score order (the COCO/VOC convention).

    Returns (is_tp, is_ignored) aligned with `pred` *sorted by score descending*.
    A prediction that matches no GT box but overlaps an ignore box at the same
    threshold is neither a TP nor an FP (e.g. a MOT17 'static person' or 'distractor').
    """
    gt = np.asarray(gt, dtype=np.float64).reshape(-1, 4)
    pred = np.asarray(pred, dtype=np.float64).reshape(-1, 4)
    scores = np.asarray(scores, dtype=np.float64).reshape(-1)
    order = np.argsort(-scores, kind="stable")
    pred = pred[order]

    ious = iou_matrix(pred, gt)
    ignore_ious = iou_matrix(pred, ignore) if ignore is not None and len(ignore) else None
    taken = np.zeros(len(gt), dtype=bool)
    is_tp = np.zeros(len(pred), dtype=bool)
    is_ignored = np.zeros(len(pred), dtype=bool)
    for i in range(len(pred)):
        if len(gt):
            candidates = np.where(~taken, ious[i], -1.0)
            j = int(np.argmax(candidates))
            if candidates[j] >= iou_threshold:
                taken[j] = True
                is_tp[i] = True
                continue
        if ignore_ious is not None and ignore_ious[i].max(initial=0.0) >= iou_threshold:
            is_ignored[i] = True
    return is_tp, is_ignored


def average_precision(tp: np.ndarray, scores: np.ndarray, n_gt: int) -> float:
    """COCO-style AP: 101-point interpolated area under the precision/recall curve."""
    if n_gt == 0:
        return float("nan")
    if len(tp) == 0:
        return 0.0
    order = np.argsort(-np.asarray(scores), kind="stable")
    tp = np.asarray(tp, dtype=np.float64)[order]
    fp = 1.0 - tp
    tp_cum, fp_cum = np.cumsum(tp), np.cumsum(fp)
    recall = tp_cum / n_gt
    precision = tp_cum / np.maximum(tp_cum + fp_cum, np.finfo(np.float64).eps)
    # Make precision monotonically non-increasing (the precision envelope).
    precision = np.maximum.accumulate(precision[::-1])[::-1]
    points = np.linspace(0.0, 1.0, 101)
    idx = np.searchsorted(recall, points, side="left")
    sampled = np.array([precision[i] if i < len(precision) else 0.0 for i in idx])
    return float(sampled.mean())


@dataclass(frozen=True)
class DetectionReport:
    images: int
    gt_boxes: int
    operating_threshold: float
    tp: int
    fp: int
    fn: int
    precision: float
    recall: float
    f1: float
    ap50: float
    ap50_95: float

    def as_dict(self) -> dict:
        return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in self.__dict__.items()}


def detection_report(images: Sequence[ImageEval], operating_threshold: float) -> DetectionReport:
    """Precision/recall at the deployed confidence threshold, plus threshold-free AP.

    For AP to be meaningful the predictions should be produced with a very low
    confidence floor (e.g. 0.001); precision/recall are then computed only from
    predictions >= `operating_threshold`, i.e. what the live system would count.
    """
    n_gt = int(sum(len(im.gt) for im in images))
    aps = []
    for thr in COCO_IOU_THRESHOLDS:
        all_tp, all_scores = [], []
        for im in images:
            tp, ign = match_detections(im.gt, im.pred, im.scores, thr, im.ignore)
            sc = np.sort(np.asarray(im.scores, dtype=np.float64))[::-1]
            all_tp.append(tp[~ign])
            all_scores.append(sc[~ign])
        tp_cat = np.concatenate(all_tp) if all_tp else np.zeros(0)
        sc_cat = np.concatenate(all_scores) if all_scores else np.zeros(0)
        aps.append(average_precision(tp_cat, sc_cat, n_gt))

    tp_n = fp_n = 0
    for im in images:
        keep = np.asarray(im.scores) >= operating_threshold
        tp, ign = match_detections(im.gt, np.asarray(im.pred).reshape(-1, 4)[keep],
                                   np.asarray(im.scores)[keep], 0.5, im.ignore)
        tp_n += int(tp.sum())
        fp_n += int((~tp & ~ign).sum())
    fn_n = n_gt - tp_n
    precision = tp_n / (tp_n + fp_n) if tp_n + fp_n else float("nan")
    recall = tp_n / n_gt if n_gt else float("nan")
    f1 = (2 * precision * recall / (precision + recall)
          if precision == precision and recall == recall and precision + recall > 0 else float("nan"))
    return DetectionReport(
        images=len(images), gt_boxes=n_gt, operating_threshold=operating_threshold,
        tp=tp_n, fp=fp_n, fn=fn_n, precision=precision, recall=recall, f1=f1,
        ap50=aps[0], ap50_95=float(np.nanmean(aps)),
    )


# ---------------------------------------------------------------------------
# Counting
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class CountReport:
    samples: int
    mean_true: float
    mae: float
    rmse: float
    bias: float          # mean(pred - true): negative = undercount
    relative_mae: float  # MAE / mean_true

    def as_dict(self) -> dict:
        return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in self.__dict__.items()}


def count_errors(true_counts: Iterable[float], predicted_counts: Iterable[float]) -> CountReport:
    t = np.asarray(list(true_counts), dtype=np.float64)
    p = np.asarray(list(predicted_counts), dtype=np.float64)
    if t.shape != p.shape:
        raise ValueError("true and predicted counts must have the same length")
    if len(t) == 0:
        raise ValueError("no samples")
    err = p - t
    mean_true = float(t.mean())
    mae = float(np.abs(err).mean())
    return CountReport(
        samples=len(t), mean_true=mean_true, mae=mae,
        rmse=float(math.sqrt((err ** 2).mean())), bias=float(err.mean()),
        relative_mae=mae / mean_true if mean_true else float("nan"),
    )


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class EventReport:
    true_events: int
    predicted_events: int
    tp: int
    fp: int
    fn: int
    precision: float
    recall: float
    f1: float

    def as_dict(self) -> dict:
        return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in self.__dict__.items()}


def event_scores(
    true_events: Sequence[tuple[str, float]],
    predicted_events: Sequence[tuple[str, float]],
    tolerance: float,
) -> EventReport:
    """Match (label, time) events one-to-one when labels agree and |dt| <= tolerance."""
    used = [False] * len(true_events)
    tp = 0
    for label, when in sorted(predicted_events, key=lambda e: e[1]):
        best, best_dt = None, None
        for i, (t_label, t_when) in enumerate(true_events):
            if used[i] or t_label != label:
                continue
            dt = abs(t_when - when)
            if dt <= tolerance and (best_dt is None or dt < best_dt):
                best, best_dt = i, dt
        if best is not None:
            used[best] = True
            tp += 1
    fp = len(predicted_events) - tp
    fn = len(true_events) - tp
    precision = tp / len(predicted_events) if predicted_events else float("nan")
    recall = tp / len(true_events) if true_events else float("nan")
    f1 = (2 * precision * recall / (precision + recall)
          if predicted_events and true_events and precision + recall > 0 else float("nan"))
    return EventReport(len(true_events), len(predicted_events), tp, fp, fn, precision, recall, f1)
