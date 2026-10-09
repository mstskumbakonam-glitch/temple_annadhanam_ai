"""AI pipeline: frame -> detection -> tracking -> events (fake model, real ByteTrack)."""

import numpy as np
import pytest

pytest.importorskip("ultralytics")

from app.ai.detector import DetectorSettings
from app.ai.pipeline import DetectionPipeline, build_pipeline
from app.ai.tracker import TrackerSettings
from app.models.enums import VisitorEventType
from app.services.runtime_events import TrackEventRecord
from tests.helpers import FakeYoloModel, make_handle, packet, person_box, walking_people

DET = DetectorSettings(confidence_floor=0.1, iou=0.5, image_size=640, target_classes=("person",))
TRK = TrackerSettings(high_confidence=0.4, low_confidence=0.1, max_lost_frames=3)


def make_pipeline(script, *, min_hits=3, persist=True, model=None):
    model = model or FakeYoloModel(script)
    pipeline = build_pipeline(
        camera_id="CAM-1", camera_db_id=7, handle=make_handle(model),
        detector_settings=DET, tracker_settings=TRK,
        min_hits=min_hits, persist_track_events=persist,
    )
    return pipeline, model


def run(pipeline, frames, start=0):
    return [pipeline.process_frame(packet("CAM-1", seq=i + 1, when=i * 0.2))
            for i in range(start, start + frames)]


# ------------------------------------------------- frame -> detection -> tracking
def test_frame_flows_through_detection_and_tracking():
    pipeline, model = make_pipeline(walking_people(lambda i: [100, 400]))
    result = pipeline.process_frame(packet("CAM-1"))

    assert result.usable_frame
    assert len(result.detections) == 2
    assert result.confident_detections == 2
    assert result.tracking is not None
    assert result.person_count == 2
    assert result.active_track_count == 2
    assert len(model.calls) == 1
    assert {t.camera_id for t in result.tracking.tracks} == {"CAM-1"}


def test_tracks_keep_their_ids_across_frames():
    pipeline, _ = make_pipeline(walking_people(lambda i: [100 + 3 * i, 400 - 3 * i]))
    results = run(pipeline, 8)
    ids = {frozenset(t.track_id for t in r.tracking.tracks) for r in results}
    assert len(ids) == 1


def test_multiple_people_are_all_tracked():
    pipeline, _ = make_pipeline(walking_people(lambda i: [100, 250, 400, 550, 700]))
    result = run(pipeline, 3)[-1]
    assert result.person_count == 5
    assert len({t.track_id for t in result.tracking.tracks}) == 5


def test_confident_detections_counts_only_those_above_the_high_threshold():
    script = lambda i, f: [(*person_box(100), 0.9, 0), (*person_box(300), 0.2, 0)]
    pipeline, _ = make_pipeline(script)
    result = pipeline.process_frame(packet())
    assert len(result.detections) == 2            # weak one is still passed to ByteTrack
    assert result.confident_detections == 1


# ------------------------------------------------------------ empty / no people
@pytest.mark.parametrize("bad", [None, np.zeros((0, 0, 3), np.uint8), "garbage"])
def test_empty_frame_skips_the_models_and_the_tracker(bad):
    pipeline, model = make_pipeline(walking_people(lambda i: [100]))
    result = pipeline.process_frame(packet(img=bad))
    assert result.usable_frame is False
    assert result.tracking is None
    assert result.detections == ()
    assert result.person_count == 0
    assert model.calls == []
    assert pipeline.tracker.live_track_count == 0


def test_an_empty_frame_does_not_age_or_expire_existing_tracks():
    pipeline, _ = make_pipeline(walking_people(lambda i: [100]))
    run(pipeline, 3)
    live = pipeline.tracker.live_track_count
    for _ in range(10):
        pipeline.process_frame(packet(img=None))
    assert pipeline.tracker.live_track_count == live == 1


def test_no_detections_is_a_normal_result_and_tracks_age():
    pipeline, _ = make_pipeline(lambda i, f: ([(*person_box(100), 0.9, 0)] if i < 3 else []))
    results = run(pipeline, 10)
    assert results[0].person_count == 1
    assert results[5].person_count == 0
    assert results[5].usable_frame
    assert pipeline.tracker.live_track_count == 0     # expired after the buffer ran out


# -------------------------------------------------------------- event emission
def test_detected_event_is_written_once_after_min_hits_and_is_anonymous():
    pipeline, _ = make_pipeline(walking_people(lambda i: [100]), min_hits=3)
    results = run(pipeline, 6)
    events = [e for r in results for e in r.events]

    assert [type(e) for e in events] == [TrackEventRecord]
    event = events[0]
    assert event.event_type is VisitorEventType.DETECTED
    assert event.camera_db_id == 7
    assert event.metadata["anonymous"] is True
    assert event.metadata["source"] == "bytetrack"
    assert results[0].events == () and results[1].events == ()    # not yet confirmed
    assert results[2].events == (event,)                          # exactly at hit 3


def test_track_events_carry_no_visitor_or_staff_identity():
    pipeline, _ = make_pipeline(walking_people(lambda i: [100]), min_hits=1)
    event = run(pipeline, 2)[0].events[0]
    fields = set(vars(event)) if hasattr(event, "__dict__") else set(event.__dataclass_fields__)
    assert not {"visitor_id", "visitor_code", "staff_id", "staff_code", "face_embedding"} & fields
    assert not any(k in event.metadata for k in ("visitor_code", "staff_code", "embedding"))


def test_lost_event_is_written_once_when_a_confirmed_track_expires():
    pipeline, _ = make_pipeline(lambda i, f: ([(*person_box(100), 0.9, 0)] if i < 4 else []), min_hits=3)
    events = [e for r in run(pipeline, 14) for e in r.events]
    kinds = [e.event_type for e in events]
    assert kinds == [VisitorEventType.DETECTED, VisitorEventType.LOST]
    detected, lost = events
    assert lost.tracking_id == detected.tracking_id
    assert lost.metadata["tracker_session"] == detected.metadata["tracker_session"]
    assert lost.metadata["reason"] == "track_expired"


def test_a_ghost_track_that_never_reaches_min_hits_leaves_no_trace():
    """A one-frame false positive must not be persisted at all."""
    pipeline, _ = make_pipeline(lambda i, f: ([(*person_box(100), 0.9, 0)] if i == 0 else []), min_hits=3)
    assert [e for r in run(pipeline, 12) for e in r.events] == []


def test_persistence_can_be_switched_off():
    pipeline, _ = make_pipeline(walking_people(lambda i: [100]), min_hits=1, persist=False)
    assert [e for r in run(pipeline, 6) for e in r.events] == []


def test_reset_tracking_closes_confirmed_tracks_and_starts_a_new_session():
    pipeline, _ = make_pipeline(walking_people(lambda i: [100, 400]), min_hits=2)
    run(pipeline, 4)
    old_session = pipeline.tracker_session

    closing = pipeline.reset_tracking(packet(when=9).timestamp)
    lost = [e for e in closing.events if e.event_type is VisitorEventType.LOST]
    assert len(lost) == 2
    assert {e.metadata["reason"] for e in lost} == {"tracker_session_ended"}
    assert {e.metadata["tracker_session"] for e in lost} == {old_session}
    assert pipeline.tracker_session != old_session
    assert pipeline.tracker.live_track_count == 0


def test_after_reset_new_tracks_are_confirmed_afresh():
    pipeline, _ = make_pipeline(walking_people(lambda i: [100]), min_hits=2)
    run(pipeline, 3)
    pipeline.reset_tracking(packet(when=9).timestamp)
    after = [e for r in run(pipeline, 3, start=50) for e in r.events]
    assert [e.event_type for e in after] == [VisitorEventType.DETECTED]


# ---------------------------------------------------------------- resilience
def test_a_faulty_processor_does_not_break_detection_or_other_processors():
    class Faulty:
        def process(self, result):
            raise RuntimeError("bug in a future processor")

    class Marker:
        def process(self, result):
            return ["marker"]

    pipeline, _ = make_pipeline(walking_people(lambda i: [100]), min_hits=99)
    pipeline.add_processor(Faulty())
    pipeline.add_processor(Marker())
    result = pipeline.process_frame(packet())
    assert result.person_count == 1
    assert "marker" in result.events


def test_added_processor_receives_the_pipeline_result():
    seen = []

    class Spy:
        def process(self, result):
            seen.append(result)
            return []

    pipeline, _ = make_pipeline(walking_people(lambda i: [100]))
    pipeline.add_processor(Spy())
    pipeline.process_frame(packet())
    assert len(seen) == 1 and seen[0].person_count == 1


def test_inference_faults_propagate_to_the_worker():
    pipeline, _ = make_pipeline(None, model=FakeYoloModel(error=RuntimeError("cuda oom")))
    with pytest.raises(RuntimeError, match="cuda oom"):
        pipeline.process_frame(packet())


def test_two_pipelines_share_a_model_but_not_tracker_state():
    model = FakeYoloModel(walking_people(lambda i: [100, 400]))
    handle = make_handle(model)

    def build(cam):
        return build_pipeline(camera_id=cam, camera_db_id=1, handle=handle,
                              detector_settings=DET, tracker_settings=TRK,
                              min_hits=1, persist_track_events=True)

    a, b = build("CAM-A"), build("CAM-B")
    assert a.tracker is not b.tracker and a.detector is not b.detector
    assert a.tracker_session != b.tracker_session
    for i in range(3):
        a.process_frame(packet("CAM-A", seq=i + 1, when=i * .2))
    assert b.tracker.live_track_count == 0          # B untouched by A's frames
    assert a.tracker.live_track_count == 2
