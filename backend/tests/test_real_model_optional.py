"""OPTIONAL verification with a REAL YOLO model.

Skipped unless AI_MODEL_PATH points at real weights, because the weights are large,
licensed separately and deliberately not part of this repository. Everything these
tests check is also covered with a deterministic fake model in the other files.

What is real here: the YOLO model, Ultralytics ByteTrack, OpenCV decoding and the
camera worker. What is NOT: there is no camera and no RTSP transport. The worker
test reads a local video file through the same cv2.VideoCapture path; it is not an
RTSP smoke test and must not be reported as one.
"""

import time
from pathlib import Path

import numpy as np
import pytest

from app.ai.detector import DetectorSettings
from app.ai.model_loader import ModelConfigurationError, ModelRegistry, resolve_model_path
from app.ai.pipeline import build_pipeline
from app.ai.tracker import TrackerSettings
from app.camera.rtsp import CameraConfig, OpenCvFrameSource
from app.camera.state import CameraRuntime
from app.camera.worker import CameraWorker, WorkerSettings
from app.config import get_settings
from app.models.enums import CameraEventType, VisitorEventType
from app.services.runtime_events import InMemoryEventSink, TrackEventRecord
from tests.helpers import packet, wait_until

pytestmark = pytest.mark.real_model


def _configured_weights() -> str | None:
    try:
        resolve_model_path(get_settings().ai_model_path)
        return get_settings().ai_model_path
    except ModelConfigurationError:
        return None


WEIGHTS = _configured_weights()
requires_model = pytest.mark.skipif(
    WEIGHTS is None,
    reason="No real YOLO weights configured (AI_MODEL_PATH); mocked-model tests cover the logic.",
)

DET = DetectorSettings(confidence_floor=0.1, iou=0.5, image_size=640, target_classes=("person",))
TRK = TrackerSettings(high_confidence=0.4, low_confidence=0.1, max_lost_frames=15)


@pytest.fixture(scope="module")
def registry():
    return ModelRegistry()


@pytest.fixture(scope="module")
def handle(registry):
    return registry.get(WEIGHTS, "cpu", 640)


@pytest.fixture(scope="module")
def sample_image():
    cv2 = pytest.importorskip("cv2")
    ultralytics = pytest.importorskip("ultralytics")
    path = Path(ultralytics.__file__).parent / "assets" / "bus.jpg"     # bundled sample photo
    image = cv2.imread(str(path))
    if image is None:
        pytest.skip("Ultralytics sample image is not available")
    return image


def make_pipeline(handle, camera_id="REAL-1", db_id=1, min_hits=2):
    return build_pipeline(camera_id=camera_id, camera_db_id=db_id, handle=handle,
                          detector_settings=DET, tracker_settings=TRK,
                          min_hits=min_hits, persist_track_events=True)


@requires_model
def test_real_model_loads_on_cpu_without_a_gpu(handle):
    assert handle.device == "cpu"
    assert handle.names[0] == "person"
    assert len(handle.names) >= 1


@requires_model
def test_real_model_finds_and_tracks_people_in_a_real_photo(handle, sample_image):
    pipeline = make_pipeline(handle)
    results = [pipeline.process_frame(packet("REAL-1", seq=i + 1, when=i * 0.2, img=sample_image))
               for i in range(4)]

    last = results[-1]
    assert last.usable_frame and last.person_count >= 3          # the photo shows a group of people
    ids_per_frame = [frozenset(t.track_id for t in r.tracking.tracks) for r in results]
    assert len(set(ids_per_frame)) == 1                          # a static scene: stable ids
    height, width = sample_image.shape[:2]
    for track in last.tracking.tracks:
        assert track.class_name == "person"
        assert 0 <= track.x1 < track.x2 <= width and 0 <= track.y1 < track.y2 <= height
        assert 0.4 <= track.confidence <= 1.0
        bx, by = track.bottom_center
        assert by == pytest.approx(track.y2) and bx == pytest.approx((track.x1 + track.x2) / 2)
    assert sum(len(r.events) for r in results) == last.person_count   # one DETECTED per person


@requires_model
def test_real_model_is_shared_and_loaded_once_across_cameras(registry, handle, sample_image):
    a = make_pipeline(handle, "REAL-A", 1)
    b = make_pipeline(handle, "REAL-B", 2)
    assert a.detector._handle is b.detector._handle is handle
    assert registry.load_count == 1
    a.process_frame(packet("REAL-A", img=sample_image))
    assert b.tracker.live_track_count == 0                        # tracker state stays private


@requires_model
def test_real_model_on_a_blank_frame_reports_nobody(handle):
    pipeline = make_pipeline(handle)
    result = pipeline.process_frame(packet(img=np.zeros((480, 640, 3), np.uint8)))
    assert result.usable_frame and result.person_count == 0 and result.detections == ()


class PacedFileSource(OpenCvFrameSource):
    """The real OpenCV capture on a local file, paced like a live camera."""

    def __init__(self, path, fps=10.0):
        super().__init__(path)
        self._interval = 1.0 / fps

    def read(self):
        time.sleep(self._interval)
        return super().read()


@requires_model
def test_real_worker_end_to_end_on_a_local_video_file(handle, sample_image, tmp_path):
    """Real model + real ByteTrack + real OpenCV decoding + the real worker.

    NOTE: a local file, not an RTSP camera.
    """
    cv2 = pytest.importorskip("cv2")
    video = tmp_path / "hall.mp4"
    height, width = sample_image.shape[:2]
    writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"mp4v"), 10.0, (width, height))
    if not writer.isOpened():
        pytest.skip("This OpenCV build cannot encode mp4v test video")
    for _ in range(200):
        writer.write(sample_image)
    writer.release()

    sink = InMemoryEventSink()
    runtime = CameraRuntime("FILE-1")
    settings = WorkerSettings(process_fps=5.0, connect_timeout=5.0, read_timeout=5.0,
                              heartbeat_seconds=60.0, stats_log_seconds=60.0, join_timeout=10.0)
    worker = CameraWorker(
        CameraConfig(1, "FILE-1", "Local file", str(video)), settings,
        pipeline_factory=lambda cfg: make_pipeline(handle, "FILE-1", 1),
        source_factory=lambda url: PacedFileSource(url, fps=10.0),
        sink=sink, runtime=runtime,
    )
    worker.start()
    try:
        assert wait_until(lambda: runtime.snapshot().frames_processed >= 4, timeout=60)
        snap = runtime.snapshot()
        assert snap.connected and snap.person_count >= 3
        assert snap.processing_fps is not None and 0 < snap.processing_fps <= 5.05   # never above AI_PROCESS_FPS=5
        assert snap.capture_fps and snap.capture_fps > snap.processing_fps          # capture outpaces AI
        assert snap.last_detection_timestamp is not None
        assert wait_until(lambda: len([r for r in sink.of_type(TrackEventRecord)
                                       if r.event_type is VisitorEventType.DETECTED]) >= 3, timeout=30)
    finally:
        worker.stop()

    kinds = [r.event_type for r in sink.camera_events()]
    assert kinds[0] is CameraEventType.AI_STARTED and CameraEventType.ONLINE in kinds
    assert kinds[-1] is CameraEventType.AI_STOPPED
    detected = [r for r in sink.of_type(TrackEventRecord) if r.event_type is VisitorEventType.DETECTED]
    lost = [r for r in sink.of_type(TrackEventRecord) if r.event_type is VisitorEventType.LOST]
    # every confirmed track is closed exactly once, and only ever anonymously
    assert sorted((r.metadata["tracker_session"], r.tracking_id) for r in detected) == \
        sorted((r.metadata["tracker_session"], r.tracking_id) for r in lost)
    assert {r.metadata["reason"] for r in lost} <= {"track_expired", "tracker_session_ended"}
    assert all(r.metadata["anonymous"] is True for r in detected + lost)
