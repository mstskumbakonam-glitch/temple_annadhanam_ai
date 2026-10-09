"""Recorded-video demo source and an end-to-end run through the real worker."""

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from app.ai.analytics_config import AnalyticsConfig
from app.ai.detector import DetectorSettings
from app.ai.pipeline import build_pipeline
from app.ai.tracker import TrackerSettings
from app.camera.rtsp import CameraConfig, build_source_factory
from app.camera.state import CameraRuntime
from app.camera.video_file import (
    DemoSourceError,
    VideoFileFrameSource,
    _RefusingSource,
    demo_file_name,
    resolve_demo_path,
)
from app.camera.worker import CameraWorker, WorkerSettings
from app.models.enums import VisitorEventType
from app.services.runtime_events import InMemoryEventSink, TrackEventRecord
from tests.helpers import FakeYoloModel, make_handle, wait_until


def write_video(path: Path, frames: int = 20, size=(320, 240), fps: float = 25.0) -> Path:
    import cv2

    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, size)
    for i in range(frames):
        img = np.full((size[1], size[0], 3), (i * 10) % 255, np.uint8)
        writer.write(img)
    writer.release()
    assert path.stat().st_size > 0
    return path


# --------------------------------------------------------------- URL rules
@pytest.mark.parametrize("url", ["demo://a.mp4", "demo://Queue_Clip-2.mov", "DEMO://x.avi"])
def test_valid_demo_names(url):
    assert demo_file_name(url) == url.split("://", 1)[1]


@pytest.mark.parametrize("url", ["demo://", "demo://../x.mp4", "demo://dir/x.mp4", "demo://x.txt",
                                 "demo://.hidden.mp4", "demo://x y.mp4", "rtsp://cam"])
def test_invalid_demo_names(url):
    with pytest.raises(DemoSourceError):
        demo_file_name(url)


def test_resolved_path_stays_inside_the_demo_dir(tmp_path):
    assert resolve_demo_path("demo://clip.mp4", tmp_path) == (tmp_path / "clip.mp4").resolve()


def test_factory_refuses_demo_urls_unless_demo_mode(tmp_path):
    settings = SimpleNamespace(camera_rtsp_transport="tcp", demo_mode=False, demo_video_path=tmp_path)
    source = build_source_factory(settings)("demo://clip.mp4")
    assert isinstance(source, _RefusingSource) and source.open(1) is False
    settings.demo_mode = True
    assert isinstance(build_source_factory(settings)("demo://clip.mp4"), VideoFileFrameSource)
    assert isinstance(build_source_factory(settings)("demo://../x.mp4"), _RefusingSource)


# ------------------------------------------------------------- the source
def test_video_source_loops_and_paces_to_the_file_rate(tmp_path):
    path = write_video(tmp_path / "clip.mp4", frames=5, fps=25)
    clock = [0.0]
    slept = []

    def sleep(s):
        slept.append(s)
        clock[0] += s

    src = VideoFileFrameSource(path, clock=lambda: clock[0], sleep=sleep)
    assert src.open(1)
    frames = [src.read() for _ in range(12)]
    assert all(ok for ok, _ in frames)
    assert src.loops == 2                                   # 12 frames from a 5-frame file
    assert src.consume_discontinuity() is True and src.consume_discontinuity() is False
    assert sum(slept) == pytest.approx(11 / 25, abs=1e-6)   # real-time, not decode speed
    src.release()
    assert src.read() == (False, None)


def test_missing_file_does_not_open(tmp_path):
    assert VideoFileFrameSource(tmp_path / "nope.mp4").open(1) is False


# --------------------------------------------- end to end through a worker
def test_recorded_video_runs_through_worker_and_counts_an_entry(tmp_path):
    """Fake detector, REAL ByteTrack, analytics, worker threads and file source."""
    write_video(tmp_path / "gate.mp4", frames=40, size=(640, 360), fps=25)

    def script(i, _frame):            # one person walking down past y=0.5 (180 px)
        y = 60 + i * 12
        return [(300, y - 100, 340, y, 0.9, 0)]

    handle = make_handle(FakeYoloModel(script))
    config = AnalyticsConfig.model_validate(
        {"lines": [{"id": "GATE", "start": [0, 0.5], "end": [1, 0.5]}], "min_track_hits": 2})

    def pipeline_factory(cfg):
        return build_pipeline(
            camera_id=cfg.camera_id, camera_db_id=cfg.db_id, handle=handle,
            detector_settings=DetectorSettings(0.1, 0.5, 640, ("person",)),
            tracker_settings=TrackerSettings(0.4, 0.1, 30),
            min_hits=2, persist_track_events=True, analytics_config=config,
        )

    settings = SimpleNamespace(camera_rtsp_transport="tcp", demo_mode=True, demo_video_path=tmp_path)
    sink = InMemoryEventSink()
    runtime = CameraRuntime("DEMO-GATE")
    worker = CameraWorker(
        CameraConfig(1, "DEMO-GATE", "Gate (recorded)", "demo://gate.mp4"),
        WorkerSettings(process_fps=20, heartbeat_seconds=60, keep_preview=True),
        pipeline_factory=pipeline_factory, source_factory=build_source_factory(settings),
        sink=sink, runtime=runtime,
    )
    worker.start()
    try:
        assert wait_until(lambda: any(
            isinstance(r, TrackEventRecord) and r.event_type is VisitorEventType.ENTERED
            for r in sink.records), timeout=10)
        snap = runtime.snapshot()
        assert snap.source_kind == "recorded"
        assert snap.analytics is not None and snap.analytics.entries >= 1
        assert runtime.preview() is not None
    finally:
        worker.stop()
    assert runtime.snapshot().analytics is None and runtime.preview() is None
