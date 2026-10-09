"""Alert and count-history rows written by DatabaseEventWriter (real PostgreSQL)."""

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import sessionmaker

from app.models import Camera, CrowdAlert, CrowdCountSnapshot
from app.services.runtime_events import AlertRecord, CountSnapshotRecord, DatabaseEventWriter
from tests.helpers import ts

pytestmark = pytest.mark.db


@pytest.fixture
def factory(migrated_database):
    return sessionmaker(bind=migrated_database, expire_on_commit=False)


@pytest.fixture
def cam(factory):
    with factory() as s:
        camera = Camera(camera_id="ANALYTICS-CAM", camera_name="Analytics", enabled=True)
        s.add(camera)
        s.commit()
        cid = camera.id
    yield cid
    with factory() as s:
        s.execute(text("DELETE FROM cameras WHERE id = :c"), {"c": cid})   # children cascade
        s.commit()


def write(factory, *records):
    w = DatabaseEventWriter(factory, flush_interval=0.02)
    w.start()
    for r in records:
        w.submit(r)
    w.stop()
    return w.stats


def alert(cid, action, **kw):
    base = dict(alert_id="a" * 32, action=action, camera_db_id=cid, alert_type="CROWD_DENSITY",
                zone_id="HALL", severity="warning", started_at=ts(0), threshold=20.0,
                message="Hall: crowd density HIGH", peak_value=21.0)
    base.update(kw)
    return AlertRecord(**base)


def test_one_alert_is_one_row_through_raise_escalate_clear(factory, cam):
    stats = write(factory,
                  alert(cam, "RAISE"),
                  alert(cam, "ESCALATE", severity="critical", peak_value=31.0),
                  alert(cam, "CLEAR", severity="critical", peak_value=31.0, ended_at=ts(120),
                        metadata={"reason": "no_video"}))
    assert stats["failed"] == 0
    with factory() as s:
        rows = list(s.scalars(select(CrowdAlert).where(CrowdAlert.camera_id == cam)))
    assert len(rows) == 1
    row = rows[0]
    assert row.severity == "critical" and row.peak_value == 31.0
    assert row.started_at == ts(0) and row.ended_at == ts(120)
    assert row.event_metadata == {"reason": "no_video"}


def test_alert_message_is_redacted(factory, cam):
    write(factory, alert(cam, "RAISE", message="from rtsp://admin:hunter2@10.0.0.9/live"))
    with factory() as s:
        row = s.scalar(select(CrowdAlert).where(CrowdAlert.camera_id == cam))
    assert "hunter2" not in row.message


def test_count_snapshots_merge_when_a_minute_is_resubmitted(factory, cam):
    write(factory,
          CountSnapshotRecord(cam, "*", ts(0), samples=10, avg_count=4.0, max_count=6, entries=2),
          CountSnapshotRecord(cam, "*", ts(0), samples=30, avg_count=8.0, max_count=9, exits=1),
          CountSnapshotRecord(cam, "QUEUE", ts(0), samples=5, avg_count=3.0, max_count=3))
    with factory() as s:
        rows = {r.zone_id: r for r in s.scalars(
            select(CrowdCountSnapshot).where(CrowdCountSnapshot.camera_id == cam))}
    whole = rows["*"]
    assert whole.samples == 40
    assert whole.avg_count == pytest.approx((4 * 10 + 8 * 30) / 40)
    assert whole.max_count == 9 and whole.entries == 2 and whole.exits == 1
    assert rows["QUEUE"].avg_count == 3.0


def test_alert_for_a_deleted_camera_is_dropped_quietly(factory):
    stats = write(factory, alert(987654, "RAISE"))
    assert stats["failed"] == 0
