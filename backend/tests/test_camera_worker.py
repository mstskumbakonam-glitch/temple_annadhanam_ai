"""Camera worker: capture, reconnect, transition events, pacing, shutdown.

The RTSP stream and the YOLO model are scripted fakes, so no camera or GPU is used.
"""

import logging
import time

import pytest

pytest.importorskip("ultralytics")

from app.ai.detector import DetectorSettings
from app.ai.pipeline import build_pipeline
from app.ai.tracker import TrackerSettings
from app.camera.rtsp import CameraConfig
from app.camera.state import CameraRuntime, Connection
from app.camera.worker import CameraWorker, WorkerSettings
from app.models.enums import CameraEventType, CameraStatus, VisitorEventType
from app.services.runtime_events import (
    CameraEventRecord,
    CameraStatusRecord,
    InMemoryEventSink,
    TrackEventRecord,
)
from tests.helpers import FakeYoloModel, SourceScript, make_handle, wait_until, walking_people

SECRET_URL = "rtsp://admin:hunter2@10.0.0.5:554/stream"

FAST = WorkerSettings(
    process_fps=100.0, connect_timeout=1.0, read_timeout=0.25,
    reconnect_delay=0.02, max_reconnect_delay=0.05, reconnect_jitter=0.0,
    heartbeat_seconds=0.15, stats_log_seconds=60.0, join_timeout=3.0, poll_interval=0.005,
)


def make_worker(script: SourceScript, *, camera="CAM-1", db_id=1, model=None, handle=None,
                settings=FAST, sink=None, url=SECRET_URL, min_hits=2):
    """`handle` lets several workers share ONE ModelHandle (and so one lock), as the
    manager does in production; otherwise each worker gets its own."""
    model = model or FakeYoloModel(walking_people(lambda i: [100, 400]))
    handle = handle or make_handle(model)

    def pipeline_factory(config):
        return build_pipeline(
            camera_id=config.camera_id, camera_db_id=config.db_id, handle=handle,
            detector_settings=DetectorSettings(0.1, 0.5, 640, ("person",)),
            tracker_settings=TrackerSettings(0.4, 0.1, 5),
            min_hits=min_hits, persist_track_events=True,
        )

    sink = sink or InMemoryEventSink()
    runtime = CameraRuntime(camera)
    worker = CameraWorker(
        CameraConfig(db_id, camera, f"Camera {camera}", url), settings,
        pipeline_factory=pipeline_factory, source_factory=script.factory,
        sink=sink, runtime=runtime,
    )
    return worker, sink, runtime, model


@pytest.fixture
def started():
    workers = []

    def start(*args, **kwargs):
        worker, sink, runtime, model = make_worker(*args, **kwargs)
        workers.append(worker)
        worker.start()
        return worker, sink, runtime, model

    yield start
    for w in workers:
        w.stop()


def event_types(sink, db_id=None):
    return [r.event_type for r in sink.camera_events(db_id)]


# ------------------------------------------------------------------ connection
def test_worker_connects_captures_and_processes(started):
    script = SourceScript()
    worker, sink, runtime, model = started(script)

    assert wait_until(lambda: runtime.snapshot().frames_processed >= 5)
    snap = runtime.snapshot()
    assert snap.connected and snap.ai_running and snap.connection_state is Connection.ONLINE
    assert snap.person_count == 2 and snap.active_tracks == 2
    assert snap.frames_captured >= snap.frames_processed
    assert snap.last_frame_timestamp is not None
    assert snap.last_detection_timestamp is not None
    assert snap.processing_fps and snap.processing_fps > 0
    assert snap.tracker_session


def test_the_rtsp_url_used_is_the_one_from_the_camera_config(started):
    script = SourceScript()
    started(script)
    assert wait_until(lambda: script.open_calls >= 1)
    assert script.urls_seen[0] == SECRET_URL


def test_connection_failure_records_one_error_event_not_one_per_retry(started):
    script = SourceScript(open_results=(False,))            # never connects
    worker, sink, runtime, _ = started(script)

    assert wait_until(lambda: script.open_calls >= 8)       # many retries happened
    assert event_types(sink) == [CameraEventType.AI_STARTED, CameraEventType.ERROR]
    snap = runtime.snapshot()
    assert snap.connection_state is Connection.ERROR and not snap.connected
    assert snap.last_error == "open_failed"
    assert snap.person_count is None                        # unknown, not a made-up zero
    statuses = [r.status for r in sink.of_type(CameraStatusRecord)]
    assert statuses.count(CameraStatus.ERROR) == 1


def test_open_that_raises_is_treated_as_a_failed_connection(started):
    script = SourceScript(open_raises=OSError)
    worker, sink, runtime, _ = started(script)
    assert wait_until(lambda: len(script.urls_seen) >= 3)      # it keeps retrying
    assert runtime.snapshot().last_error == "exception:OSError"
    assert worker.is_alive                                  # the thread survived


def test_reconnect_after_initial_failures_records_a_single_online_event(started):
    script = SourceScript(open_results=(False, False, False, True))
    worker, sink, runtime, _ = started(script)

    assert wait_until(lambda: runtime.snapshot().connected)
    types = event_types(sink)
    assert types == [CameraEventType.AI_STARTED, CameraEventType.ERROR, CameraEventType.RECOVERED] \
        or types == [CameraEventType.AI_STARTED, CameraEventType.ERROR, CameraEventType.ONLINE]
    assert script.open_calls == 4


def test_repeated_connected_state_produces_exactly_one_online_event(started):
    script = SourceScript()
    worker, sink, runtime, _ = started(script)
    assert wait_until(lambda: runtime.snapshot().frames_processed >= 30)
    assert event_types(sink).count(CameraEventType.ONLINE) == 1
    assert CameraEventType.RECOVERED not in event_types(sink)


def test_stream_drop_records_offline_once_then_recovered_after_reconnect(started):
    script = SourceScript(frames_per_conn=15)               # stream dies after 15 frames
    worker, sink, runtime, _ = started(script)

    assert wait_until(lambda: runtime.snapshot().reconnect_count >= 2, timeout=8)
    types = event_types(sink)
    assert types[0] is CameraEventType.AI_STARTED and types[1] is CameraEventType.ONLINE
    # every outage is one OFFLINE followed by one RECOVERED - never repeated in between
    body = [t for t in types[2:] if t in (CameraEventType.OFFLINE, CameraEventType.RECOVERED)]
    for i in range(0, len(body) - 1, 2):
        assert body[i] is CameraEventType.OFFLINE and body[i + 1] is CameraEventType.RECOVERED
    assert runtime.snapshot().reconnect_count >= 2


def test_disconnect_clears_counts_and_flags_the_camera_offline(started):
    script = SourceScript(open_results=(True, False), frames_per_conn=10)   # one connection, then gone
    worker, sink, runtime, _ = started(script)

    assert wait_until(lambda: runtime.snapshot().connection_state is Connection.OFFLINE, timeout=6)
    snap = runtime.snapshot()
    assert snap.connected is False
    assert snap.person_count is None and snap.active_tracks is None
    assert snap.last_error == "read_timeout"
    assert CameraEventType.OFFLINE in event_types(sink)
    assert event_types(sink).count(CameraEventType.OFFLINE) == 1
    assert runtime.latest_tracks() == ()


def test_track_events_are_closed_when_the_stream_is_lost(started):
    """People being tracked when the camera drops must not be left 'inside' forever."""
    script = SourceScript(open_results=(True, False), frames_per_conn=30)
    worker, sink, runtime, _ = started(script)

    assert wait_until(lambda: runtime.snapshot().connection_state is Connection.OFFLINE, timeout=6)
    assert wait_until(lambda: any(
        r.event_type is VisitorEventType.LOST for r in sink.of_type(TrackEventRecord)))
    detected = [r for r in sink.of_type(TrackEventRecord) if r.event_type is VisitorEventType.DETECTED]
    lost = [r for r in sink.of_type(TrackEventRecord) if r.event_type is VisitorEventType.LOST]
    assert len(detected) == len(lost) == 2


def test_the_buffer_is_cleared_on_disconnect(started):
    script = SourceScript(open_results=(True, False), frames_per_conn=5)
    worker, _, runtime, _ = started(script)
    assert wait_until(lambda: runtime.snapshot().connection_state is Connection.OFFLINE, timeout=6)
    assert worker.buffer.get_latest() is None


# ------------------------------------------------------------------ pacing / cpu
def test_inference_runs_at_the_configured_rate_not_the_camera_rate(started):
    """Camera delivers ~500 FPS; AI is capped at 20 FPS."""
    settings = WorkerSettings(**{**FAST.__dict__, "process_fps": 20.0})
    script = SourceScript(frame_delay=0.002)
    worker, _, runtime, model = started(script, settings=settings)

    time.sleep(1.5)
    snap = runtime.snapshot()
    assert snap.frames_captured > 100                       # capture kept draining
    inference_calls = len(model.calls)
    assert 15 <= inference_calls <= 40, f"expected ~30 calls in 1.5s at 20 FPS, got {inference_calls}"
    assert inference_calls < snap.frames_captured / 3
    assert snap.processing_fps is not None and snap.processing_fps <= 25


def test_only_the_newest_frame_is_processed_never_a_backlog(started):
    settings = WorkerSettings(**{**FAST.__dict__, "process_fps": 5.0})
    script = SourceScript(frame_delay=0.001)
    worker, _, runtime, _ = started(script, settings=settings)
    time.sleep(1.0)
    assert len(worker.buffer) == 1                          # bounded
    assert worker.buffer.dropped > 50                       # stale frames were discarded
    assert runtime.snapshot().frames_dropped == pytest.approx(worker.buffer.dropped, abs=50)


def test_max_capture_fps_limits_what_enters_the_buffer(started):
    settings = WorkerSettings(**{**FAST.__dict__, "max_capture_fps": 20.0})
    script = SourceScript(frame_delay=0.001)
    worker, _, runtime, _ = started(script, settings=settings)
    time.sleep(1.0)
    assert runtime.snapshot().frames_captured <= 30


# ---------------------------------------------------------------- resilience
def test_inference_errors_are_survived_and_reported(started):
    model = FakeYoloModel(error=RuntimeError("CUDA out of memory"))
    worker, _, runtime, _ = started(SourceScript(), model=model)
    assert wait_until(lambda: (runtime.snapshot().last_error or "").startswith("processing"))
    assert runtime.snapshot().last_error == "processing: RuntimeError"
    assert worker.is_alive and runtime.snapshot().connected      # capture unaffected


def test_a_failing_read_is_not_a_crash(started):
    class Exploding:
        def open(self, timeout): return True
        def read(self): raise RuntimeError("decoder fault")
        def release(self): pass

    script = SourceScript()
    script.factory = lambda url: Exploding()
    worker, sink, runtime, _ = started(script)
    assert wait_until(lambda: runtime.snapshot().connection_state is Connection.OFFLINE, timeout=6)
    assert worker.is_alive


def test_heartbeat_updates_last_frame_time_periodically_not_per_frame(started):
    script = SourceScript(frame_delay=0.002)
    worker, sink, runtime, _ = started(script)
    time.sleep(1.0)
    beats = [r for r in sink.of_type(CameraStatusRecord)
             if r.last_frame_time is not None and r.status is CameraStatus.ONLINE]
    assert 2 <= len(beats) <= 10                            # ~every 0.15 s, nowhere near per frame
    assert runtime.snapshot().frames_captured > 100


# --------------------------------------------------------------- shutdown
def test_shutdown_stops_threads_releases_the_stream_and_records_ai_stopped(started):
    script = SourceScript()
    worker, sink, runtime, _ = started(script)
    assert wait_until(lambda: runtime.snapshot().frames_processed >= 3)

    began = time.monotonic()
    worker.stop()
    assert time.monotonic() - began < 2.0
    assert not worker.is_alive
    assert script.releases >= 1
    snap = runtime.snapshot()
    assert snap.ai_running is False and snap.connected is False
    assert snap.connection_state is Connection.STOPPED
    assert snap.person_count is None and snap.processing_fps is None
    assert event_types(sink)[-1] is CameraEventType.AI_STOPPED


def test_stop_is_idempotent_and_safe_before_start():
    worker, sink, _, _ = make_worker(SourceScript())
    worker.stop()                                            # never started: no-op
    assert sink.records == []

    worker.start()
    worker.stop()
    worker.stop()
    assert event_types(sink).count(CameraEventType.AI_STOPPED) == 1


def test_start_twice_is_an_error():
    worker, _, _, _ = make_worker(SourceScript())
    worker.start()
    try:
        with pytest.raises(RuntimeError, match="already started"):
            worker.start()
    finally:
        worker.stop()


def test_shutdown_while_the_camera_is_unreachable_is_prompt(started):
    worker, _, _, _ = started(SourceScript(open_results=(False,)))
    time.sleep(0.1)
    began = time.monotonic()
    worker.stop()
    assert time.monotonic() - began < 1.5


def test_status_is_reset_to_offline_on_start_and_stop(started):
    worker, sink, runtime, _ = started(SourceScript())
    assert wait_until(lambda: runtime.snapshot().connected)
    worker.stop()
    statuses = [r.status for r in sink.of_type(CameraStatusRecord)]
    assert statuses[0] is CameraStatus.OFFLINE and statuses[-1] is CameraStatus.OFFLINE
    assert CameraStatus.ONLINE in statuses


def test_a_pipeline_that_cannot_be_built_fails_start_cleanly():
    from app.ai.model_loader import ModelConfigurationError

    def broken(config):
        raise ModelConfigurationError("model has no class named ['person']")

    sink = InMemoryEventSink()
    worker = CameraWorker(
        CameraConfig(1, "CAM-X", "X", SECRET_URL), FAST,
        pipeline_factory=broken, source_factory=SourceScript().factory,
        sink=sink, runtime=CameraRuntime("CAM-X"),
    )
    with pytest.raises(ModelConfigurationError):
        worker.start()
    assert not worker.is_alive and sink.records == []


# ------------------------------------------------------------- multi-camera
def test_cameras_run_independently_and_do_not_share_state(started):
    model = FakeYoloModel(walking_people(lambda i: [100, 400]))
    a_script, b_script = SourceScript(), SourceScript(open_results=(False,))   # B is dead
    a, a_sink, a_rt, _ = started(a_script, camera="CAM-A", db_id=1, model=model)
    b, b_sink, b_rt, _ = started(b_script, camera="CAM-B", db_id=2, model=model)

    assert wait_until(lambda: a_rt.snapshot().frames_processed >= 10)
    a_snap, b_snap = a_rt.snapshot(), b_rt.snapshot()
    assert a_snap.connected and a_snap.person_count == 2
    assert not b_snap.connected and b_snap.person_count is None
    assert a_snap.tracker_session != b_snap.tracker_session
    assert a.buffer is not b.buffer and a.buffer.camera_id == "CAM-A"
    assert b.buffer.get_latest() is None                     # B never received a frame
    assert a.is_alive and b.is_alive                         # B's failure did not stop A
    assert event_types(a_sink, 1).count(CameraEventType.ERROR) == 0
    assert event_types(b_sink, 2).count(CameraEventType.ERROR) == 1


def test_many_cameras_share_one_model_with_serialised_inference(started):
    model = FakeYoloModel(walking_people(lambda i: [100, 400]), delay=0.003)
    shared_handle = make_handle(model)                   # one handle => one lock, as in production
    runtimes = []
    for n in range(4):
        _, _, rt, _ = started(SourceScript(), camera=f"CAM-{n}", db_id=n + 1,
                              model=model, handle=shared_handle)
        runtimes.append(rt)
    assert wait_until(lambda: all(r.snapshot().frames_processed >= 5 for r in runtimes), timeout=10)
    assert model.max_concurrent == 1
    assert {r.snapshot().tracker_session for r in runtimes}.__len__() == 4


# -------------------------------------------------------------------- secrets
def test_credentials_never_appear_in_logs_events_or_runtime_state(started, caplog):
    from app.utils.logs import install_log_redaction

    install_log_redaction()
    caplog.set_level(logging.DEBUG)
    script = SourceScript(open_results=(False, False, True), frames_per_conn=10)
    worker, sink, runtime, _ = started(script)
    assert wait_until(lambda: runtime.snapshot().reconnect_count >= 1
                      or runtime.snapshot().frames_processed >= 3, timeout=6)
    worker.stop()

    assert "hunter2" not in caplog.text
    for record in sink.of_type(CameraEventRecord):
        assert "hunter2" not in (record.message or "")
        assert "hunter2" not in str(record.metadata)
    assert "hunter2" not in repr(runtime.snapshot())
    assert "10.0.0.5" not in repr(runtime.snapshot())


def test_shutdown_closes_open_tracks_so_no_detected_row_is_left_dangling(started):
    script = SourceScript()
    worker, sink, runtime, _ = started(script)
    assert wait_until(lambda: len([r for r in sink.of_type(TrackEventRecord)
                                   if r.event_type is VisitorEventType.DETECTED]) == 2)
    assert not [r for r in sink.of_type(TrackEventRecord) if r.event_type is VisitorEventType.LOST]

    worker.stop()

    detected = [r for r in sink.of_type(TrackEventRecord) if r.event_type is VisitorEventType.DETECTED]
    lost = [r for r in sink.of_type(TrackEventRecord) if r.event_type is VisitorEventType.LOST]
    assert sorted(r.tracking_id for r in detected) == sorted(r.tracking_id for r in lost)
    assert {r.metadata["reason"] for r in lost} == {"tracker_session_ended"}
    # the LOST rows come before the AI_STOPPED marker
    records = sink.records
    last_lost = max(i for i, r in enumerate(records) if r in lost)
    stopped = next(i for i, r in enumerate(records)
                   if isinstance(r, CameraEventRecord) and r.event_type is CameraEventType.AI_STOPPED)
    assert last_lost < stopped
