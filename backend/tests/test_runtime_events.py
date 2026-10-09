"""DatabaseEventWriter against real PostgreSQL.

These tests COMMIT (the writer owns its sessions), so they clean up after
themselves rather than relying on the rollback fixture.
"""

import threading
import time

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from app.models import Camera, CameraEvent, VisitorEvent
from app.models.enums import CameraEventType, CameraStatus, VisitorEventType
from app.services.runtime_events import (
    CameraEventRecord,
    CameraStatusRecord,
    DatabaseEventWriter,
    TrackEventRecord,
)
from tests.helpers import ts, wait_until

pytestmark = pytest.mark.db


@pytest.fixture
def factory(migrated_database):
    return sessionmaker(bind=migrated_database, expire_on_commit=False)


@pytest.fixture
def db_camera(factory):
    with factory() as s:
        camera = Camera(camera_id="WRITER-CAM", camera_name="Writer test", enabled=True, status="OFFLINE")
        s.add(camera)
        s.commit()
        camera_id = camera.id
    yield camera_id
    with factory() as s:
        s.execute(text("DELETE FROM visitor_events WHERE camera_id = :c"), {"c": camera_id})
        s.execute(text("DELETE FROM cameras WHERE id = :c"), {"c": camera_id})   # camera_events cascade
        s.commit()


@pytest.fixture
def writer(factory):
    made = []

    def build(**kw):
        w = DatabaseEventWriter(factory, flush_interval=0.02, **kw)
        made.append(w)
        return w

    yield build
    for w in made:
        w.stop(timeout=5)


def read_camera_events(factory, camera_id):
    with factory() as s:
        return list(s.scalars(select(CameraEvent).where(CameraEvent.camera_id == camera_id)))


def read_track_events(factory, camera_id):
    with factory() as s:
        return list(s.scalars(select(VisitorEvent).where(VisitorEvent.camera_id == camera_id)))


def test_camera_event_is_persisted_including_the_ai_types(writer, factory, db_camera):
    w = writer()
    w.start()
    w.submit(CameraEventRecord(db_camera, CameraEventType.AI_STARTED, ts(), "AI pipeline started"))
    w.submit(CameraEventRecord(db_camera, CameraEventType.ONLINE, ts(1), "stream connected", fps=24.5,
                               metadata={"attempt": 1}))
    w.stop()

    rows = {r.event_type: r for r in read_camera_events(factory, db_camera)}
    assert set(rows) == {"AI_STARTED", "ONLINE"}
    assert rows["ONLINE"].fps == 24.5 and rows["ONLINE"].event_metadata == {"attempt": 1}
    assert rows["ONLINE"].event_time.tzinfo is not None


def test_camera_status_is_updated_with_fps_and_last_frame_time(writer, factory, db_camera):
    w = writer()
    w.start()
    w.submit(CameraStatusRecord(db_camera, CameraStatus.ONLINE, fps=24.0, last_frame_time=ts(5)))
    w.stop()
    with factory() as s:
        camera = s.get(Camera, db_camera)
        assert (camera.status, camera.fps, camera.last_frame_time) == ("ONLINE", 24.0, ts(5))


def test_a_disabled_camera_keeps_its_disabled_status(writer, factory, db_camera):
    with factory() as s:
        camera = s.get(Camera, db_camera)
        camera.enabled, camera.status = False, "DISABLED"
        s.commit()
    w = writer()
    w.start()
    w.submit(CameraStatusRecord(db_camera, CameraStatus.ONLINE))
    w.stop()
    with factory() as s:
        assert s.get(Camera, db_camera).status == "DISABLED"


def test_status_update_for_a_deleted_camera_is_ignored_quietly(writer, factory):
    w = writer()
    w.start()
    w.submit(CameraStatusRecord(999_999_999, CameraStatus.ONLINE))
    w.stop()
    assert w.stats["failed"] == 0 and w.stats["written"] == 1


def test_track_events_are_stored_anonymously(writer, factory, db_camera):
    w = writer()
    w.start()
    w.submit(TrackEventRecord(db_camera, VisitorEventType.DETECTED, 17, ts(),
                              {"source": "bytetrack", "anonymous": True}))
    w.submit(TrackEventRecord(db_camera, VisitorEventType.LOST, 17, ts(9), {"reason": "track_expired"}))
    w.stop()

    rows = read_track_events(factory, db_camera)
    assert {r.event_type for r in rows} == {"DETECTED", "LOST"}
    assert all(r.visitor_id is None for r in rows)              # a track is not a visitor
    assert all(r.tracking_id == 17 for r in rows)


def test_writing_track_events_creates_no_visitor_rows(writer, factory, db_camera):
    with factory() as s:
        before = s.scalar(text("SELECT count(*) FROM visitors"))
    w = writer()
    w.start()
    for tid in range(1, 6):
        w.submit(TrackEventRecord(db_camera, VisitorEventType.DETECTED, tid, ts()))
    w.stop()
    with factory() as s:
        assert s.scalar(text("SELECT count(*) FROM visitors")) == before


def test_a_password_in_a_message_is_redacted_before_it_reaches_the_database(writer, factory, db_camera):
    w = writer()
    w.start()
    w.submit(CameraEventRecord(db_camera, CameraEventType.ERROR, ts(),
                               "could not open rtsp://admin:hunter2@10.0.0.5/s"))
    w.stop()
    message = read_camera_events(factory, db_camera)[0].message
    assert "hunter2" not in message and "admin:***" in message


def test_one_bad_record_does_not_block_its_neighbours(writer, factory, db_camera):
    w = writer()
    good_1 = CameraEventRecord(db_camera, CameraEventType.ONLINE, ts(1), "good 1")
    bad = CameraEventRecord(999_999_999, CameraEventType.ONLINE, ts(2), "orphan: camera missing")
    good_2 = CameraEventRecord(db_camera, CameraEventType.OFFLINE, ts(3), "good 2")
    for record in (good_1, bad, good_2):
        w.submit(record)
    w.start()
    w.stop()

    assert {r.message for r in read_camera_events(factory, db_camera)} == {"good 1", "good 2"}
    assert w.stats["failed"] == 1 and w.stats["written"] == 2


def test_a_full_queue_drops_and_counts_instead_of_growing(writer, db_camera):
    w = writer(max_queue=3)                                     # not started: nothing drains
    results = [w.submit(CameraEventRecord(db_camera, CameraEventType.ONLINE, ts(i), f"e{i}"))
               for i in range(8)]
    assert results == [True] * 3 + [False] * 5
    assert w.stats["dropped"] == 5 and w.stats["queued"] == 3


def test_stop_flushes_everything_that_was_queued(writer, factory, db_camera):
    w = writer(batch_size=7)
    w.start()
    for i in range(60):
        w.submit(TrackEventRecord(db_camera, VisitorEventType.DETECTED, i + 1, ts(i)))
    w.stop(timeout=10)
    assert len(read_track_events(factory, db_camera)) == 60
    assert w.stats["queued"] == 0


def test_sessions_are_per_batch_and_never_used_by_the_submitting_thread(factory, db_camera):
    """One connection per batch, opened on the writer thread - not per event, not shared."""
    opened: list[int] = []

    def counting_factory():
        opened.append(threading.get_ident())
        return factory()

    w = DatabaseEventWriter(counting_factory, batch_size=25, flush_interval=0.05)
    for i in range(100):                                        # queued before the writer starts
        w.submit(TrackEventRecord(db_camera, VisitorEventType.DETECTED, i + 1, ts(i)))
    w.start()
    w.stop(timeout=10)

    assert len(read_track_events(factory, db_camera)) == 100
    assert 1 <= len(opened) <= 6                                # ~4 batches, nowhere near 100
    assert threading.get_ident() not in opened                  # never the caller's thread
    assert len(set(opened)) == 1                                # a single writer thread


def test_the_writer_survives_a_database_outage_and_recovers(db_camera, factory):
    state = {"down": True}

    def flaky_factory():
        if state["down"]:
            raise OperationalError("SELECT 1", {}, Exception("connection refused"))
        return factory()

    w = DatabaseEventWriter(flaky_factory, flush_interval=0.02)
    w.start()
    w.submit(CameraEventRecord(db_camera, CameraEventType.OFFLINE, ts(), "lost during outage"))
    assert wait_until(lambda: w.stats["failed"] >= 1)           # counted, not raised
    assert w._thread.is_alive()

    state["down"] = False
    w.submit(CameraEventRecord(db_camera, CameraEventType.RECOVERED, ts(1), "after outage"))
    assert wait_until(lambda: w.stats["written"] >= 1)
    w.stop()
    assert [r.message for r in read_camera_events(factory, db_camera)] == ["after outage"]


def test_submit_never_blocks_even_when_the_database_is_slow(factory, db_camera):
    def slow_factory():
        time.sleep(0.3)
        return factory()

    w = DatabaseEventWriter(slow_factory, flush_interval=0.02)
    w.start()
    began = time.monotonic()
    for i in range(50):
        w.submit(TrackEventRecord(db_camera, VisitorEventType.DETECTED, i + 1, ts(i)))
    assert time.monotonic() - began < 0.1                       # inference is never held up
    w.stop(timeout=10)
