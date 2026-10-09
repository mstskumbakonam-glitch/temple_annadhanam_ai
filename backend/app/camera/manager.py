"""Camera manager: starts, supervises and stops one worker per camera.

* Cameras come only from the `cameras` table (enabled, with an RTSP URL). There
  is no second camera configuration system.
* Startup is non-blocking: `start()` returns at once and a supervisor thread does
  the slow work (model load, worker start). The API stays responsive.
* Failures are isolated: a camera that cannot start, or whose worker dies, does
  not affect the others; the supervisor retries on its next cycle.
* Cameras added, removed, disabled or re-pointed through the REST API are picked
  up on the next sync without a restart.
* A PostgreSQL advisory lock guarantees only ONE process runs the pipeline, so
  starting several uvicorn workers cannot write every event several times.
"""

from __future__ import annotations

import json
import logging
import math
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING

from sqlalchemy import select, text
from sqlalchemy.engine import Connection as SAConnection, Engine
from sqlalchemy.orm import Session

from app.ai.detector import DetectorSettings
from app.ai.model_loader import (
    ModelConfigurationError,
    ModelHandle,
    ModelRegistry,
    default_registry,
)
from app.ai.pipeline import DetectionPipeline, build_pipeline
from app.ai.tracker import TrackerSettings
from app.ai.analytics_config import AnalyticsConfig, parse_analytics_config
from app.camera.rtsp import CameraConfig, FrameSourceFactory, build_source_factory
from app.camera.state import (
    ManagerState,
    ManagerStatusHolder,
    RuntimeRegistry,
    manager_status,
    runtime_registry,
)
from app.camera.worker import CameraWorker, WorkerSettings
from app.models import Camera
from app.services.runtime_events import DatabaseEventWriter, EventSink
from app.utils.logs import log_event

if TYPE_CHECKING:
    from app.config import Settings

logger = logging.getLogger("temple_annadhanam.camera")

# Arbitrary constant identifying "the Temple Annadhanam AI pipeline" to PostgreSQL.
ADVISORY_LOCK_KEY = 0x54414E4E41444841

CameraLoader = Callable[[], list[CameraConfig]]


class CameraManager:
    def __init__(
        self,
        settings: "Settings",
        *,
        session_factory: Callable[[], Session],
        lock_engine: Engine | None = None,
        sink: EventSink | None = None,
        registry: RuntimeRegistry = runtime_registry,
        status: ManagerStatusHolder = manager_status,
        model_registry: ModelRegistry = default_registry,
        source_factory: FrameSourceFactory | None = None,
        camera_loader: CameraLoader | None = None,
        worker_settings: WorkerSettings | None = None,
        use_advisory_lock: bool = True,
    ) -> None:
        self._settings = settings
        self._session_factory = session_factory
        self._lock_engine = lock_engine
        self._registry = registry
        self._status = status
        self._model_registry = model_registry
        self._source_factory = source_factory or build_source_factory(settings)
        self._camera_loader = camera_loader or self._load_cameras_from_db
        self._worker_settings = worker_settings or WorkerSettings.from_settings(settings)
        self._use_lock = use_advisory_lock

        self._owns_sink = sink is None
        self._sink: EventSink = sink or DatabaseEventWriter(session_factory)

        self._workers: dict[str, CameraWorker] = {}
        self._workers_lock = threading.Lock()
        self._handle: ModelHandle | None = None
        self._lock_conn: SAConnection | None = None
        self._stop = threading.Event()
        self._supervisor: threading.Thread | None = None
        self._last_model_error: str | None = None

    # --------------------------------------------------------------- lifecycle
    def start(self) -> None:
        """Begin supervising in the background. Returns immediately."""
        if self._supervisor is not None and self._supervisor.is_alive():
            return
        self._stop.clear()
        self._status.set(ManagerState.STARTING)
        self._supervisor = threading.Thread(
            target=self._supervise, name="camera-supervisor", daemon=True
        )
        self._supervisor.start()

    def stop(self, timeout: float = 20.0) -> None:
        """Stop everything cleanly: workers first, then the writer, then the lock."""
        self._stop.set()
        if self._supervisor is not None:
            self._supervisor.join(timeout)

        with self._workers_lock:
            workers = list(self._workers.values())
            self._workers.clear()
        for worker in workers:      # signal all first so they wind down in parallel
            worker.request_stop()
        for worker in workers:
            worker.stop()

        if self._owns_sink and isinstance(self._sink, DatabaseEventWriter):
            self._sink.stop(timeout=10.0)
        self._release_lock()
        self._status.set(ManagerState.STOPPED)
        log_event(logger, logging.INFO, "CAMERA", None, "manager_stopped")

    # ---------------------------------------------------------------- queries
    @property
    def worker_ids(self) -> list[str]:
        with self._workers_lock:
            return sorted(self._workers)

    def get_worker(self, camera_id: str) -> CameraWorker | None:
        with self._workers_lock:
            return self._workers.get(camera_id)

    # -------------------------------------------------------------- supervisor
    def _supervise(self) -> None:
        owner = False
        try:
            while not self._stop.is_set():
                if not owner:
                    owner = self._try_become_owner()
                if owner and self._ensure_model() is not None:
                    self.reconcile()
                self._stop.wait(self._settings.ai_sync_interval)
        except Exception:
            logger.exception("[CAMERA] supervisor crashed")
            self._status.set(ManagerState.STOPPED, detail="supervisor crashed; see server log")

    def _try_become_owner(self) -> bool:
        try:
            acquired = self._acquire_lock()
        except Exception as exc:
            self._status.set(ManagerState.STARTING, detail="waiting for the database")
            log_event(logger, logging.WARNING, "CAMERA", None, "lock_unavailable",
                      error=type(exc).__name__)
            return False
        if not acquired:
            self._status.set(
                ManagerState.NOT_OWNER,
                detail="another process already runs the AI pipeline",
            )
            log_event(logger, logging.WARNING, "CAMERA", None, "not_owner")
            return False
        if self._owns_sink and isinstance(self._sink, DatabaseEventWriter):
            self._sink.start()
        return True

    # ------------------------------------------------------ advisory lock (PG)
    def _acquire_lock(self) -> bool:
        if not self._use_lock:
            return True
        if self._lock_conn is not None:
            return True
        engine = self._lock_engine
        if engine is None:
            from app.database import engine as default_engine

            engine = default_engine
        connection = engine.connect().execution_options(isolation_level="AUTOCOMMIT")
        try:
            acquired = connection.execute(
                text("SELECT pg_try_advisory_lock(:key)"), {"key": ADVISORY_LOCK_KEY}
            ).scalar()
        except Exception:
            connection.close()
            raise
        if acquired:
            self._lock_conn = connection
            return True
        connection.close()
        return False

    def _release_lock(self) -> None:
        connection, self._lock_conn = self._lock_conn, None
        if connection is None:
            return
        try:
            # Session-level locks survive returning the connection to the pool,
            # so unlock explicitly before closing.
            connection.execute(
                text("SELECT pg_advisory_unlock(:key)"), {"key": ADVISORY_LOCK_KEY}
            )
        except Exception:
            connection.invalidate()   # closing the DB session releases the lock too
        finally:
            connection.close()

    # ------------------------------------------------------------------- model
    def _ensure_model(self) -> ModelHandle | None:
        if self._handle is not None:
            return self._handle
        try:
            handle = self._model_registry.get(
                self._settings.ai_model_path,
                self._settings.ai_device,
                self._settings.ai_image_size,
            )
        except ModelConfigurationError as exc:
            self._set_model_unavailable(str(exc))
            return None
        self._handle = handle
        self._last_model_error = None
        self._status.set(
            ManagerState.RUNNING, model_loaded=True,
            model_name=handle.model_name, device=handle.device,
        )
        return handle

    def _set_model_unavailable(self, message: str) -> None:
        self._status.set(ManagerState.MODEL_UNAVAILABLE, detail=message, model_loaded=False)
        if message != self._last_model_error:      # log a given problem once, not every cycle
            self._last_model_error = message
            log_event(logger, logging.ERROR, "AI", None, "model_unavailable", reason=message)

    # --------------------------------------------------------------- reconcile
    def reconcile(self) -> None:
        """Make the running workers match the cameras table."""
        try:
            wanted = {c.camera_id: c for c in self._camera_loader()}
        except Exception as exc:
            # Database trouble: keep what is running, try again next cycle.
            log_event(logger, logging.WARNING, "CAMERA", None, "camera_list_unavailable",
                      error=type(exc).__name__)
            return

        with self._workers_lock:
            current = dict(self._workers)

        for code, worker in current.items():
            new_config = wanted.get(code)
            if new_config is None:
                self._retire(code, worker, "camera removed or disabled")
            elif new_config != worker.config:
                self._retire(code, worker, "camera configuration changed")
            elif not worker.is_alive:
                self._retire(code, worker, "worker stopped unexpectedly")

        with self._workers_lock:
            running = set(self._workers)
        for code, config in wanted.items():
            if code in running:
                continue
            if not self._start_worker(config):
                break   # model problem: no point trying the remaining cameras

        if not wanted:
            self._status.set(ManagerState.RUNNING, detail="no enabled cameras with an RTSP URL")
        elif self._status.get().state is not ManagerState.MODEL_UNAVAILABLE:
            self._status.set(ManagerState.RUNNING)

    def _retire(self, code: str, worker: CameraWorker, reason: str) -> None:
        log_event(logger, logging.INFO, "CAMERA", code, "worker_retired", reason=reason)
        with self._workers_lock:
            self._workers.pop(code, None)
        worker.stop()
        if reason == "camera removed or disabled":
            self._registry.remove(code)

    def _start_worker(self, config: CameraConfig) -> bool:
        """Start one camera. Returns False only for a model configuration problem."""
        runtime = self._registry.register(config.camera_id)
        worker = CameraWorker(
            config,
            self._worker_settings,
            pipeline_factory=self._make_pipeline,
            source_factory=self._source_factory,
            sink=self._sink,
            runtime=runtime,
        )
        try:
            worker.start()
        except ModelConfigurationError as exc:
            self._handle = None
            self._set_model_unavailable(str(exc))
            runtime.update(last_error="model unavailable")
            return False
        except Exception as exc:
            runtime.update(last_error=f"start failed: {type(exc).__name__}")
            log_event(logger, logging.ERROR, "CAMERA", config.camera_id, "start_failed",
                      error=type(exc).__name__)
            return True   # isolate: other cameras carry on

        with self._workers_lock:
            self._workers[config.camera_id] = worker
        return True

    def _make_pipeline(self, config: CameraConfig) -> DetectionPipeline:
        s = self._settings
        assert self._handle is not None
        detector_settings = DetectorSettings(
            confidence_floor=min(s.ai_confidence, s.ai_track_low_confidence),
            iou=s.ai_iou,
            image_size=s.ai_image_size,
            target_classes=tuple(s.ai_target_class_list),
        )
        tracker_settings = TrackerSettings(
            high_confidence=s.ai_confidence,
            low_confidence=s.ai_track_low_confidence,
            max_lost_frames=max(1, math.ceil(s.ai_track_lost_seconds * s.ai_process_fps)),
        )
        return build_pipeline(
            camera_id=config.camera_id,
            camera_db_id=config.db_id,
            handle=self._handle,
            detector_settings=detector_settings,
            tracker_settings=tracker_settings,
            min_hits=s.ai_track_min_hits,
            persist_track_events=s.ai_persist_track_events,
            analytics_config=self._analytics_config(config),
            persist_count_history=s.ai_persist_count_history,
        )

    @staticmethod
    def _analytics_config(config: CameraConfig) -> AnalyticsConfig:
        """Stored config is validated by the API; a bad row (edited by hand in SQL)
        disables lines/zones for that camera instead of stopping its counting."""
        try:
            return parse_analytics_config(json.loads(config.analytics_json) if config.analytics_json else None)
        except Exception as exc:
            log_event(logger, logging.ERROR, "AI", config.camera_id, "invalid_analytics_config",
                      error=type(exc).__name__)
            return AnalyticsConfig()

    # ---------------------------------------------------------- database read
    def _load_cameras_from_db(self) -> list[CameraConfig]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(Camera)
                .where(
                    Camera.enabled.is_(True),
                    Camera.rtsp_url.is_not(None),
                    Camera.rtsp_url != "",
                )
                .order_by(Camera.camera_id)
            ).all()
            return [
                CameraConfig(
                    db_id=row.id,
                    camera_id=row.camera_id,
                    camera_name=row.camera_name,
                    rtsp_url=row.rtsp_url,
                    analytics_json=(
                        json.dumps(row.analytics_config, sort_keys=True)
                        if row.analytics_config else ""
                    ),
                )
                for row in rows
            ]
