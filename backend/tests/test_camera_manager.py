"""CameraManager: lifecycle, sync with the cameras table, isolation, ownership."""

import time

import pytest
from sqlalchemy.orm import sessionmaker

pytest.importorskip("ultralytics")

from app.ai.model_loader import ModelRegistry
from app.camera.manager import CameraManager
from app.camera.rtsp import CameraConfig
from app.camera.state import ManagerState, ManagerStatusHolder, RuntimeRegistry
from app.camera.worker import WorkerSettings
from app.config import Settings
from app.models import Camera
from app.services.runtime_events import InMemoryEventSink
from tests.helpers import FakeYoloModel, SourceScript, wait_until, walking_people

DB = "postgresql+psycopg://u:p@localhost:5432/d"

FAST = WorkerSettings(
    process_fps=50.0, connect_timeout=1.0, read_timeout=0.25, reconnect_delay=0.02,
    max_reconnect_delay=0.05, reconnect_jitter=0.0, heartbeat_seconds=5.0,
    stats_log_seconds=60.0, join_timeout=3.0, poll_interval=0.005,
)


def cam(n: int, url: str | None = None) -> CameraConfig:
    return CameraConfig(n, f"CAM-{n}", f"Camera {n}", url or f"rtsp://user:pw{n}@10.0.0.{n}/s")


class Harness:
    """A manager wired to fakes, with its own registry / status so tests never touch globals."""

    def __init__(self, tmp_path, cameras, *, model_present=True, session_factory=None,
                 lock=False, lock_engine=None, sync_interval=0.05):
        self.weights = tmp_path / "m.pt"
        if model_present:
            self.weights.write_bytes(b"w")
        self.cameras = list(cameras)
        self.script = SourceScript()
        self.sink = InMemoryEventSink()
        self.registry = RuntimeRegistry()
        self.status = ManagerStatusHolder()
        self.model_registry = ModelRegistry(
            lambda p, d: FakeYoloModel(walking_people(lambda i: [100, 400])), warmup=False)
        self.settings = Settings(
            _env_file=None, database_url=DB, ai_model_path=str(self.weights),
            ai_sync_interval=sync_interval, ai_process_fps=50.0, ai_track_min_hits=2,
        )
        self.load_error: Exception | None = None
        self.manager = CameraManager(
            self.settings,
            session_factory=session_factory or (lambda: None),
            lock_engine=lock_engine,
            sink=self.sink,
            registry=self.registry,
            status=self.status,
            model_registry=self.model_registry,
            source_factory=self.script.factory,
            camera_loader=self._load,
            worker_settings=FAST,
            use_advisory_lock=lock,
        )

    def _load(self):
        if self.load_error:
            raise self.load_error
        return list(self.cameras)


@pytest.fixture
def harness(tmp_path):
    made = []

    def build(cameras=(), **kw):
        h = Harness(tmp_path, cameras, **kw)
        made.append(h)
        return h

    yield build
    for h in made:
        h.manager.stop()


# ------------------------------------------------------------------ lifecycle
def test_start_returns_immediately_and_workers_come_up_in_the_background(harness):
    h = harness([cam(1), cam(2), cam(3)])
    began = time.monotonic()
    h.manager.start()
    assert time.monotonic() - began < 0.5                     # never blocks the API
    assert wait_until(lambda: h.manager.worker_ids == ["CAM-1", "CAM-2", "CAM-3"])
    assert h.status.get().state is ManagerState.RUNNING
    assert h.status.get().model_loaded and h.status.get().model_name == "m.pt"
    assert wait_until(lambda: all(h.registry.snapshot(c).connected
                                  for c in ("CAM-1", "CAM-2", "CAM-3")))


def test_model_is_loaded_once_for_all_cameras(harness):
    h = harness([cam(1), cam(2), cam(3), cam(4)])
    h.manager.start()
    assert wait_until(lambda: len(h.manager.worker_ids) == 4)
    assert h.model_registry.load_count == 1


def test_the_rtsp_url_comes_from_the_camera_config_only(harness):
    h = harness([cam(1, "rtsp://admin:secret@10.9.9.9/x")])
    h.manager.start()
    assert wait_until(lambda: h.script.open_calls >= 1)
    assert h.script.urls_seen[0] == "rtsp://admin:secret@10.9.9.9/x"


def test_stop_stops_every_worker_and_marks_the_manager_stopped(harness):
    h = harness([cam(1), cam(2)])
    h.manager.start()
    assert wait_until(lambda: len(h.manager.worker_ids) == 2)
    began = time.monotonic()
    h.manager.stop()
    assert time.monotonic() - began < 5
    assert h.manager.worker_ids == []
    assert h.status.get().state is ManagerState.STOPPED
    for code in ("CAM-1", "CAM-2"):
        assert h.registry.snapshot(code).ai_running is False


def test_stop_before_start_is_harmless(harness):
    harness([]).manager.stop()


# ---------------------------------------------------------------- model problems
def test_missing_model_reports_a_clear_status_and_starts_no_workers(harness):
    h = harness([cam(1)], model_present=False)
    h.manager.start()                                          # must not raise or crash
    assert wait_until(lambda: h.status.get().state is ManagerState.MODEL_UNAVAILABLE)
    assert "not found" in h.status.get().detail
    assert h.status.get().model_loaded is False
    assert h.manager.worker_ids == []
    assert h.registry.snapshot("CAM-1").ai_running is False


def test_manager_recovers_when_the_model_file_appears(harness):
    h = harness([cam(1)], model_present=False)
    h.manager.start()
    assert wait_until(lambda: h.status.get().state is ManagerState.MODEL_UNAVAILABLE)
    h.weights.write_bytes(b"w")                                # operator drops the weights in
    assert wait_until(lambda: h.manager.worker_ids == ["CAM-1"], timeout=5)
    assert h.status.get().state is ManagerState.RUNNING


def test_unconfigured_model_path_is_reported(harness, tmp_path):
    h = harness([cam(1)])
    h.manager._settings = Settings(_env_file=None, database_url=DB, ai_model_path=None,
                                   ai_sync_interval=0.05)
    h.manager.start()
    assert wait_until(lambda: h.status.get().state is ManagerState.MODEL_UNAVAILABLE)
    assert "AI_MODEL_PATH is not set" in h.status.get().detail


# ---------------------------------------------------------------- reconciliation
def test_new_cameras_are_picked_up_without_a_restart(harness):
    h = harness([cam(1)])
    h.manager.start()
    assert wait_until(lambda: h.manager.worker_ids == ["CAM-1"])
    h.cameras.append(cam(2))
    assert wait_until(lambda: h.manager.worker_ids == ["CAM-1", "CAM-2"])


def test_removed_or_disabled_cameras_are_stopped_and_forgotten(harness):
    h = harness([cam(1), cam(2)])
    h.manager.start()
    assert wait_until(lambda: len(h.manager.worker_ids) == 2)
    h.cameras.pop()                                            # CAM-2 removed / disabled
    assert wait_until(lambda: h.manager.worker_ids == ["CAM-1"])
    # the runtime entry is dropped just after the worker finishes stopping
    assert wait_until(lambda: h.registry.get("CAM-2") is None)
    assert h.registry.snapshot("CAM-2").ai_running is False    # placeholder, not stale data


def test_changing_a_cameras_url_restarts_only_that_worker(harness):
    h = harness([cam(1), cam(2)])
    h.manager.start()
    assert wait_until(lambda: len(h.manager.worker_ids) == 2)
    before_1, before_2 = h.manager.get_worker("CAM-1"), h.manager.get_worker("CAM-2")
    h.cameras[0] = cam(1, "rtsp://admin:newpass@10.0.0.99/s")
    assert wait_until(lambda: h.manager.get_worker("CAM-1") is not before_1)
    assert h.manager.get_worker("CAM-2") is before_2           # untouched
    assert wait_until(lambda: "rtsp://admin:newpass@10.0.0.99/s" in h.script.urls_seen)


def test_a_dead_worker_is_restarted_by_the_supervisor(harness):
    h = harness([cam(1)])
    h.manager.start()
    assert wait_until(lambda: h.manager.worker_ids == ["CAM-1"])
    first = h.manager.get_worker("CAM-1")
    first.request_stop()                                       # simulate its threads dying
    first._capture_thread.join(2)
    first._processing_thread.join(2)
    assert not first.is_alive
    assert wait_until(lambda: h.manager.get_worker("CAM-1") not in (None, first), timeout=5)
    assert h.manager.get_worker("CAM-1").is_alive


def test_a_database_error_while_listing_cameras_keeps_current_workers(harness):
    h = harness([cam(1)])
    h.manager.start()
    assert wait_until(lambda: h.manager.worker_ids == ["CAM-1"])
    h.load_error = RuntimeError("database down")
    time.sleep(0.3)                                            # several sync cycles
    assert h.manager.worker_ids == ["CAM-1"]
    assert h.manager.get_worker("CAM-1").is_alive


# --------------------------------------------------------------------- isolation
def test_one_camera_that_cannot_start_does_not_stop_the_others(harness):
    h = harness([cam(1), cam(2), cam(3)])
    original = h.manager._make_pipeline

    def picky(config):
        if config.camera_id == "CAM-2":
            raise RuntimeError("cannot build pipeline for CAM-2")
        return original(config)

    h.manager._make_pipeline = picky
    h.manager.start()
    assert wait_until(lambda: h.manager.worker_ids == ["CAM-1", "CAM-3"])
    assert h.registry.snapshot("CAM-2").last_error == "start failed: RuntimeError"
    assert h.registry.snapshot("CAM-2").ai_running is False
    assert wait_until(lambda: h.registry.snapshot("CAM-1").connected
                      and h.registry.snapshot("CAM-3").connected)


def test_an_unreachable_camera_does_not_affect_a_healthy_one(harness):
    h = harness([cam(1), cam(2)])
    h.script.factory = lambda url: (SourceScript(open_results=(False,)).factory(url)
                                    if "10.0.0.2" in url else SourceScript().factory(url))
    h.manager._source_factory = h.script.factory
    h.manager.start()
    assert wait_until(lambda: h.registry.snapshot("CAM-1").connected)
    assert wait_until(lambda: h.registry.snapshot("CAM-2").last_error == "open_failed")
    assert wait_until(lambda: h.registry.snapshot("CAM-1").frames_processed > 0)
    assert h.registry.snapshot("CAM-2").connected is False


# ---------------------------------------------------------------- database loader
@pytest.mark.db
def test_only_enabled_cameras_with_an_rtsp_url_are_loaded(session, tmp_path):
    session.add_all([
        Camera(camera_id="LOAD-OK", camera_name="ok", rtsp_url="rtsp://u:p@h/1", enabled=True),
        Camera(camera_id="LOAD-OFF", camera_name="off", rtsp_url="rtsp://u:p@h/2", enabled=False),
        Camera(camera_id="LOAD-NOURL", camera_name="none", rtsp_url=None, enabled=True),
        Camera(camera_id="LOAD-EMPTY", camera_name="empty", rtsp_url="", enabled=True),
    ])
    session.flush()

    factory = sessionmaker(bind=session.get_bind(), join_transaction_mode="create_savepoint")
    h = Harness(tmp_path, [], session_factory=factory)
    loaded = {c.camera_id: c for c in h.manager._load_cameras_from_db()}

    assert set(loaded) == {"LOAD-OK"}
    assert loaded["LOAD-OK"].rtsp_url == "rtsp://u:p@h/1"      # URL comes from PostgreSQL
    assert loaded["LOAD-OK"].db_id > 0


# ------------------------------------------------------------- single-owner lock
@pytest.mark.db
def test_only_one_process_runs_the_pipeline(harness, test_engine):
    first = harness([cam(1)], lock=True, lock_engine=test_engine)
    second = harness([cam(1)], lock=True, lock_engine=test_engine)

    first.manager.start()
    assert wait_until(lambda: first.status.get().state is ManagerState.RUNNING)
    second.manager.start()
    assert wait_until(lambda: second.status.get().state is ManagerState.NOT_OWNER)
    assert second.manager.worker_ids == []                     # would have duplicated events
    assert first.manager.worker_ids == ["CAM-1"]


@pytest.mark.db
def test_ownership_transfers_when_the_owner_stops(harness, test_engine):
    first = harness([cam(1)], lock=True, lock_engine=test_engine)
    second = harness([cam(1)], lock=True, lock_engine=test_engine)
    first.manager.start()
    assert wait_until(lambda: first.status.get().state is ManagerState.RUNNING)
    second.manager.start()
    assert wait_until(lambda: second.status.get().state is ManagerState.NOT_OWNER)

    first.manager.stop()                                       # releases the advisory lock
    assert wait_until(lambda: second.manager.worker_ids == ["CAM-1"], timeout=5)
    assert second.status.get().state is ManagerState.RUNNING
