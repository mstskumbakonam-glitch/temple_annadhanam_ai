"""Persistence of runtime events produced by the camera/AI pipeline.

Workers never touch the database directly. They hand small immutable records to
an EventSink. The production sink, DatabaseEventWriter, owns ONE background
thread that drains a bounded queue and writes in batches, opening a short-lived
SQLAlchemy session for each batch:

* inference is never blocked by database latency or an outage;
* one connection per batch, not one per frame or per event;
* no Session object is ever shared between threads;
* if the queue overflows, events are dropped and counted instead of growing
  memory without bound.
"""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, Union

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models import Camera, CameraEvent, CrowdAlert, CrowdCountSnapshot, VisitorEvent
from app.models.enums import CameraEventType, CameraStatus, VisitorEventType
from app.utils.logs import redact_text

logger = logging.getLogger("temple_annadhanam.db")


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class CameraEventRecord:
    """A row for camera_events. `camera_db_id` is cameras.id, not the display code."""

    camera_db_id: int
    event_type: CameraEventType
    event_time: datetime
    message: str | None = None
    fps: float | None = None
    metadata: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.message is not None:  # a URL password must never reach the database
            object.__setattr__(self, "message", redact_text(self.message)[:500])


@dataclass(frozen=True)
class CameraStatusRecord:
    """An update of cameras.status (and optionally fps / last_frame_time)."""

    camera_db_id: int
    status: CameraStatus
    fps: float | None = None
    last_frame_time: datetime | None = None


@dataclass(frozen=True)
class TrackEventRecord:
    """An ANONYMOUS tracker event stored in visitor_events.

    visitor_id is always NULL: a ByteTrack track is not a visitor. Visitor
    identities are created in a later phase.
    """

    camera_db_id: int
    event_type: VisitorEventType
    tracking_id: int
    event_time: datetime
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AlertRecord:
    """A crowd alert transition. RAISE inserts the row; ESCALATE and CLEAR update it
    (matched by alert_id), so one alert is always exactly one row."""

    alert_id: str
    action: str                 # RAISE | ESCALATE | CLEAR
    camera_db_id: int
    alert_type: str
    zone_id: str | None
    severity: str
    started_at: datetime
    ended_at: datetime | None = None
    peak_value: float | None = None
    threshold: float | None = None
    message: str | None = None
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class CountSnapshotRecord:
    """One minute of counts. Re-submitting the same (camera, zone, minute) MERGES
    into the existing row (sample-weighted), e.g. after a reconnect mid-minute."""

    camera_db_id: int
    zone_id: str
    bucket_start: datetime
    samples: int
    avg_count: float
    max_count: int
    entries: int = 0
    exits: int = 0


Record = Union[CameraEventRecord, CameraStatusRecord, TrackEventRecord, AlertRecord, CountSnapshotRecord]


class EventSink(Protocol):
    def submit(self, record: Record) -> bool:
        """Accept a record without blocking. Returns False if it was dropped."""


# ---------------------------------------------------------------------------
# In-memory sink (tests, and a safe default when persistence is disabled)
# ---------------------------------------------------------------------------
class InMemoryEventSink:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: list[Record] = []

    def submit(self, record: Record) -> bool:
        with self._lock:
            self._records.append(record)
        return True

    @property
    def records(self) -> list[Record]:
        with self._lock:
            return list(self._records)

    def of_type(self, record_type: type) -> list[Any]:
        return [r for r in self.records if isinstance(r, record_type)]

    def camera_events(self, camera_db_id: int | None = None) -> list[CameraEventRecord]:
        return [
            r for r in self.of_type(CameraEventRecord)
            if camera_db_id is None or r.camera_db_id == camera_db_id
        ]


# ---------------------------------------------------------------------------
# Database writer
# ---------------------------------------------------------------------------
class DatabaseEventWriter:
    """Background writer. Call start() once and stop() on shutdown."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        max_queue: int = 1000,
        batch_size: int = 50,
        flush_interval: float = 0.5,
    ) -> None:
        self._session_factory = session_factory
        self._queue: queue.Queue[Record] = queue.Queue(maxsize=max_queue)
        self._batch_size = batch_size
        self._flush_interval = flush_interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

        self._stats_lock = threading.Lock()
        self._written = 0
        self._failed = 0
        self._dropped = 0

    # ------------------------------------------------------------- lifecycle
    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="event-writer", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        """Flush what is queued, then stop. Anything left after `timeout` is lost."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)

    # ----------------------------------------------------------------- input
    def submit(self, record: Record) -> bool:
        try:
            self._queue.put_nowait(record)
            return True
        except queue.Full:
            with self._stats_lock:
                self._dropped += 1
            return False

    # ----------------------------------------------------------------- stats
    @property
    def stats(self) -> dict[str, int]:
        with self._stats_lock:
            return {
                "written": self._written,
                "failed": self._failed,
                "dropped": self._dropped,
                "queued": self._queue.qsize(),
            }

    # ---------------------------------------------------------------- worker
    def _run(self) -> None:
        while True:
            batch = self._collect_batch()
            if batch:
                self._write_batch(batch)
            elif self._stop.is_set():
                return

    def _collect_batch(self) -> list[Record]:
        batch: list[Record] = []
        try:
            batch.append(self._queue.get(timeout=self._flush_interval))
        except queue.Empty:
            return batch
        while len(batch) < self._batch_size:
            try:
                batch.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return batch

    def _write_batch(self, batch: list[Record]) -> None:
        try:
            self._commit(batch)
            self._count(written=len(batch))
        except Exception as exc:
            logger.warning("[DB] batch of %d failed (%s); retrying individually",
                           len(batch), type(exc).__name__)
            for record in batch:
                self._write_one(record)

    def _write_one(self, record: Record) -> None:
        """Isolate a poison record so it cannot block the ones around it."""
        try:
            self._commit([record])
            self._count(written=1)
        except Exception as exc:
            self._count(failed=1)
            logger.warning("[DB] dropped %s (%s: %s)", type(record).__name__,
                           type(exc).__name__, redact_text(str(exc))[:120])

    def _commit(self, batch: list[Record]) -> None:
        # A fresh session per batch: sessions are never shared across threads.
        with self._session_factory() as session:
            try:
                for record in batch:
                    self._apply(session, record)
                session.commit()
            except Exception:
                session.rollback()
                raise

    def _count(self, written: int = 0, failed: int = 0) -> None:
        with self._stats_lock:
            self._written += written
            self._failed += failed

    @staticmethod
    def _apply(session: Session, record: Record) -> None:
        if isinstance(record, CameraEventRecord):
            session.add(
                CameraEvent(
                    camera_id=record.camera_db_id,
                    event_type=record.event_type,
                    event_time=record.event_time,
                    fps=record.fps,
                    message=record.message,
                    event_metadata=record.metadata,
                )
            )
        elif isinstance(record, CameraStatusRecord):
            camera = session.get(Camera, record.camera_db_id)
            if camera is None or not camera.enabled:
                return  # deleted, or switched off by an operator: leave DISABLED alone
            camera.status = record.status
            if record.fps is not None:
                camera.fps = record.fps
            if record.last_frame_time is not None:
                camera.last_frame_time = record.last_frame_time
        elif isinstance(record, TrackEventRecord):
            session.add(
                VisitorEvent(
                    visitor_id=None,  # anonymous: a track is not a visitor
                    camera_id=record.camera_db_id,
                    event_type=record.event_type,
                    event_time=record.event_time,
                    tracking_id=record.tracking_id,
                    event_metadata=record.metadata,
                )
            )
        elif isinstance(record, AlertRecord):
            DatabaseEventWriter._apply_alert(session, record)
        elif isinstance(record, CountSnapshotRecord):
            table = CrowdCountSnapshot.__table__
            stmt = pg_insert(table).values(
                camera_id=record.camera_db_id, zone_id=record.zone_id,
                bucket_start=record.bucket_start, samples=record.samples,
                avg_count=record.avg_count, max_count=record.max_count,
                entries=record.entries, exits=record.exits,
            )
            ex = stmt.excluded
            stmt = stmt.on_conflict_do_update(
                index_elements=["camera_id", "zone_id", "bucket_start"],
                set_={
                    "avg_count": (table.c.avg_count * table.c.samples + ex.avg_count * ex.samples)
                    / (table.c.samples + ex.samples),
                    "samples": table.c.samples + ex.samples,
                    "max_count": func.greatest(table.c.max_count, ex.max_count),
                    "entries": table.c.entries + ex.entries,
                    "exits": table.c.exits + ex.exits,
                },
            )
            session.execute(stmt)
        else:  # pragma: no cover - guards future record types
            raise TypeError(f"unsupported record type {type(record).__name__}")

    @staticmethod
    def _apply_alert(session: Session, record: AlertRecord) -> None:
        row = session.scalar(select(CrowdAlert).where(CrowdAlert.alert_uid == record.alert_id))
        if row is None:
            if session.get(Camera, record.camera_db_id) is None:
                return  # camera deleted meanwhile
            row = CrowdAlert(
                alert_uid=record.alert_id, camera_id=record.camera_db_id,
                zone_id=record.zone_id, alert_type=record.alert_type,
                started_at=record.started_at, threshold=record.threshold,
            )
            session.add(row)
        row.severity = record.severity
        row.message = redact_text(record.message)[:500] if record.message else row.message
        if record.peak_value is not None:
            row.peak_value = max(row.peak_value or 0.0, record.peak_value)
        if record.action == "CLEAR":
            row.ended_at = record.ended_at
        if record.metadata:
            row.event_metadata = {**(row.event_metadata or {}), **record.metadata}

