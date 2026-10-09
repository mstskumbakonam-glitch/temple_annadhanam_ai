"""The benchmark's own metric code must be right, or every reported number is wrong."""

import math

import numpy as np
import pytest

from eval.metrics import (
    ImageEval,
    average_precision,
    count_errors,
    detection_report,
    event_scores,
    iou_matrix,
    match_detections,
)


def test_iou():
    m = iou_matrix(np.array([[0, 0, 10, 10]]), np.array([[0, 0, 10, 10], [5, 0, 15, 10], [20, 20, 30, 30]]))
    assert m[0].tolist() == pytest.approx([1.0, 50 / 150, 0.0])


def test_matching_is_greedy_by_score_and_one_to_one():
    gt = np.array([[0, 0, 10, 10]])
    pred = np.array([[0, 0, 10, 10], [1, 1, 10, 10]])
    tp, ign = match_detections(gt, pred, np.array([0.5, 0.9]), 0.5)
    # sorted by score: the 0.9 box takes the GT, the 0.5 duplicate is a false positive
    assert tp.tolist() == [True, False] and not ign.any()


def test_ignore_regions_are_neither_tp_nor_fp():
    tp, ign = match_detections(np.zeros((0, 4)), np.array([[0, 0, 10, 10]]), np.array([0.9]), 0.5,
                               ignore=np.array([[0, 0, 10, 10]]))
    assert not tp[0] and ign[0]


def test_ap_perfect_and_empty():
    assert average_precision(np.array([1, 1]), np.array([0.9, 0.8]), 2) == pytest.approx(1.0)
    assert average_precision(np.array([]), np.array([]), 3) == 0.0
    assert math.isnan(average_precision(np.array([]), np.array([]), 0))


def test_ap_half_recall():
    # one of two GT found with perfect precision: area = precision 1 over recall 0..0.5
    ap = average_precision(np.array([1]), np.array([0.9]), 2)
    assert ap == pytest.approx(51 / 101, abs=1e-6)


def test_detection_report_counts():
    im = ImageEval(gt=np.array([[0, 0, 10, 10], [20, 0, 30, 10]]),
                   pred=np.array([[0, 0, 10, 10], [50, 50, 60, 60], [20, 0, 30, 10]]),
                   scores=np.array([0.9, 0.8, 0.2]))
    rep = detection_report([im], operating_threshold=0.5)
    assert (rep.tp, rep.fp, rep.fn) == (1, 1, 1)
    assert rep.precision == 0.5 and rep.recall == 0.5


def test_count_errors():
    rep = count_errors([10, 20], [8, 23])
    assert rep.mae == 2.5 and rep.bias == 0.5
    assert rep.rmse == pytest.approx(math.sqrt((4 + 9) / 2))
    with pytest.raises(ValueError):
        count_errors([1], [1, 2])


def test_event_scores_tolerance_and_labels():
    truth = [("IN", 10.0), ("OUT", 20.0), ("IN", 30.0)]
    pred = [("IN", 11.0), ("IN", 20.5), ("IN", 33.5)]
    rep = event_scores(truth, pred, tolerance=2.0)
    assert (rep.tp, rep.fp, rep.fn) == (1, 2, 2)
