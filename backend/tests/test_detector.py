"""Detection structure and YOLO detector parsing."""

import math
from dataclasses import FrozenInstanceError

import numpy as np
import pytest

from app.ai.detector import DetectorSettings, YoloDetector, is_usable_frame
from app.ai.model_loader import ModelConfigurationError
from app.ai.types import Detection, MalformedDetectionError
from tests.helpers import FakeTensor, FakeYoloModel, frame, make_handle, person_box, ts

SETTINGS = DetectorSettings(confidence_floor=0.1, iou=0.5, image_size=640,
                            target_classes=("person",))


def make_detector(rows_per_call, settings=SETTINGS, **model_kwargs):
    model = FakeYoloModel(lambda i, f: rows_per_call(i) if callable(rows_per_call) else rows_per_call,
                          **model_kwargs)
    return YoloDetector(make_handle(model), settings), model


# ---------------------------------------------------------------- Detection
def test_valid_person_detection_and_geometry():
    d = Detection(0, "person", 0.9, 100, 50, 200, 250, ts())
    assert (d.class_id, d.class_name, d.confidence) == (0, "person", 0.9)
    assert (d.center_x, d.center_y) == (150.0, 150.0)
    assert (d.width, d.height, d.area) == (100.0, 200.0, 20000.0)
    assert d.bbox == (100, 50, 200, 250)


def test_bottom_center_is_the_middle_of_the_bottom_edge():
    """Seat logic (Phase 8) judges where a person's feet are, so this must be exact."""
    d = Detection(0, "person", 0.9, 100, 50, 200, 250, ts())
    assert (d.bottom_center_x, d.bottom_center_y) == (150.0, 250.0)


def test_detection_carries_frame_timestamp_and_is_immutable():
    d = Detection(0, "person", 0.9, 0, 0, 10, 10, ts(5))
    assert d.frame_timestamp == ts(5)
    with pytest.raises(FrozenInstanceError):
        d.confidence = 0.1


def test_from_xyxy_converts_and_clamps_to_frame():
    d = Detection.from_xyxy([-20, -5, 400, 300], 0.8, 0, "person", ts(), frame_shape=(240, 320, 3))
    assert d.bbox == (0.0, 0.0, 320.0, 240.0)       # clipped to width 320, height 240
    assert (d.center_x, d.center_y) == (160.0, 120.0)


def test_from_xyxy_accepts_numpy_input():
    d = Detection.from_xyxy(np.array([10, 20, 110, 220], dtype=np.float32), np.float32(0.5),
                            np.int64(0), "person", ts())
    assert d.bbox == (10.0, 20.0, 110.0, 220.0)
    assert isinstance(d.class_id, int)


@pytest.mark.parametrize(
    "xyxy, conf, cid",
    [
        ((10, 10, 10, 50), 0.9, 0),              # zero width
        ((10, 50, 50, 10), 0.9, 0),              # inverted box
        ((math.nan, 0, 10, 10), 0.9, 0),
        ((0, 0, math.inf, 10), 0.9, 0),
        ((0, 0, 10, 10), math.nan, 0),
        ((0, 0, 10, 10), 1.5, 0),                # confidence out of range
        ((0, 0, 10, 10), -0.1, 0),
        ((0, 0, 10, 10), 0.9, -1),               # negative class
        (("a", 0, 10, 10), 0.9, 0),              # not numeric
        ((0, 0, 10), 0.9, 0),                    # wrong length
        ((0, 0, 10, 10), 0.9, float("inf")),
    ],
)
def test_malformed_detections_are_rejected(xyxy, conf, cid):
    with pytest.raises(MalformedDetectionError):
        Detection.from_xyxy(xyxy, conf, cid, "person", ts())


def test_naive_timestamp_is_rejected():
    from datetime import datetime

    with pytest.raises(MalformedDetectionError, match="timezone"):
        Detection(0, "person", 0.9, 0, 0, 10, 10, datetime(2026, 1, 1))


def test_box_entirely_outside_frame_is_rejected_after_clamping():
    with pytest.raises(MalformedDetectionError):
        Detection.from_xyxy([500, 500, 600, 600], 0.9, 0, "person", ts(), frame_shape=(240, 320, 3))


def test_to_dict_contains_no_identity():
    payload = Detection(0, "person", 0.9, 100, 50, 200, 250, ts()).to_dict()
    assert set(payload) == {"class_id", "class_name", "confidence", "bbox", "center", "bottom_center"}


# ---------------------------------------------------------------- detector
def test_detector_returns_person_detections():
    detector, model = make_detector([(*person_box(100), 0.9, 0), (*person_box(300), 0.7, 0)])
    result = detector.detect(frame(), ts())
    assert len(result) == 2
    assert all(d.class_name == "person" for d in result)
    assert all(d.frame_timestamp == ts() for d in result)


def test_detector_passes_configuration_to_the_model():
    detector, model = make_detector([])
    detector.detect(frame(), ts())
    call = model.calls[0]
    assert call["conf"] == 0.1
    assert call["iou"] == 0.5
    assert call["imgsz"] == 640
    assert call["classes"] == [0]                # only 'person' is asked for


def test_only_target_classes_are_returned_even_if_the_model_leaks_others():
    rows = [(*person_box(100), 0.9, 0), (*person_box(200), 0.9, 2), (*person_box(300), 0.9, 1)]
    class LeakyModel(FakeYoloModel):
        def predict(self, frame, **kwargs):       # ignores the classes filter
            kwargs["classes"] = None
            return super().predict(frame, **kwargs)

    detector = YoloDetector(make_handle(LeakyModel(lambda i, f: rows)), SETTINGS)
    result = detector.detect(frame(), ts())
    assert [d.class_name for d in result] == ["person"]


def test_results_are_sorted_by_confidence():
    detector, _ = make_detector([(*person_box(100), 0.5, 0), (*person_box(200), 0.95, 0),
                                 (*person_box(300), 0.7, 0)])
    assert [round(d.confidence, 2) for d in detector.detect(frame(), ts())] == [0.95, 0.7, 0.5]


def test_malformed_rows_are_dropped_and_counted_but_valid_rows_survive():
    rows = [
        (*person_box(100), 0.9, 0),
        (50, 50, 50, 90, 0.9, 0),                 # zero width
        (math.nan, 0, 10, 10, 0.9, 0),
        (*person_box(300), 0.8, 0),
    ]
    detector, _ = make_detector(rows)
    result = detector.detect(frame(), ts())
    assert len(result) == 2
    assert detector.rejected_total == 2


@pytest.mark.parametrize(
    "bad_frame",
    [None, np.zeros((0, 0, 3), np.uint8), np.zeros((240,), np.uint8), "not a frame", 42,
     np.zeros((1, 1, 3), np.uint8)],
)
def test_unusable_frames_yield_no_detections_and_skip_the_model(bad_frame):
    detector, model = make_detector([(*person_box(100), 0.9, 0)])
    assert detector.detect(bad_frame, ts()) == []
    assert model.calls == []
    assert detector.empty_frames == 1
    assert not is_usable_frame(bad_frame)


def test_no_detections_returns_empty_list():
    detector, _ = make_detector([])
    assert detector.detect(frame(), ts()) == []


def test_missing_boxes_object_is_handled():
    class NoBoxes:
        boxes = None

    class Model(FakeYoloModel):
        def predict(self, frame, **kw):
            return [NoBoxes()]

    assert YoloDetector(make_handle(Model()), SETTINGS).detect(frame(), ts()) == []


def test_empty_results_list_is_handled():
    class Model(FakeYoloModel):
        def predict(self, frame, **kw):
            return []

    assert YoloDetector(make_handle(Model()), SETTINGS).detect(frame(), ts()) == []


def test_inconsistent_output_shapes_are_rejected_wholesale():
    class Boxes:
        xyxy = np.zeros((3, 4), np.float32) + [0, 0, 10, 10]
        conf = np.array([0.9, 0.8], np.float32)          # 2 scores for 3 boxes
        cls = np.zeros(3, np.float32)

    class Model(FakeYoloModel):
        def predict(self, frame, **kw):
            return [type("R", (), {"boxes": Boxes(), "names": {}})()]

    detector = YoloDetector(make_handle(Model()), SETTINGS)
    assert detector.detect(frame(), ts()) == []
    assert detector.rejected_total >= 1


def test_torch_style_tensors_are_accepted():
    class Boxes:
        xyxy = FakeTensor([[100, 100, 160, 200]])
        conf = FakeTensor([0.88])
        cls = FakeTensor([0])

    class Model(FakeYoloModel):
        def predict(self, frame, **kw):
            return [type("R", (), {"boxes": Boxes(), "names": {}})()]

    result = YoloDetector(make_handle(Model()), SETTINGS).detect(frame(), ts())
    assert len(result) == 1 and result[0].bbox == (100.0, 100.0, 160.0, 200.0)


def test_unknown_target_class_is_a_configuration_error():
    bad = DetectorSettings(0.1, 0.5, 640, ("unicorn",))
    with pytest.raises(ModelConfigurationError, match="unicorn"):
        YoloDetector(make_handle(), bad)


def test_empty_target_list_means_all_classes():
    everything = DetectorSettings(0.1, 0.5, 640, ())
    detector = YoloDetector(make_handle(FakeYoloModel(lambda i, f: [(*person_box(100), .9, 0), (*person_box(300), .9, 2)])), everything)
    result = detector.detect(frame(), ts())
    assert detector.target_class_ids is None
    assert {d.class_name for d in result} == {"person", "car"}


def test_inference_faults_propagate_so_the_worker_can_back_off():
    detector, _ = make_detector([], error=RuntimeError("CUDA out of memory"))
    with pytest.raises(RuntimeError, match="out of memory"):
        detector.detect(frame(), ts())


def test_detector_records_inference_time():
    detector, _ = make_detector([], delay=0.02)
    detector.detect(frame(), ts())
    assert detector.last_inference_seconds >= 0.02
