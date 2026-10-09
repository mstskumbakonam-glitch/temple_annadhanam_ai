"""Phase 3 model tests: creation, uniqueness, relationships, constraints.

All tests run against PostgreSQL. There is no SQLite fallback.
"""

from datetime import timedelta

import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import DataError, IntegrityError

from app.models import (
    Camera,
    CameraEvent,
    Seat,
    SeatOccupancy,
    Staff,
    StaffAttendance,
    Visitor,
    VisitorEvent,
)
from app.utils.time import utc_now

pytestmark = pytest.mark.db


# --------------------------------------------------------------- cameras
def test_camera_creation(session):
    camera = Camera(camera_id="ANN-ENT-01", camera_name="Entrance", location="Gate")
    session.add(camera)
    session.flush()
    session.refresh(camera)
    assert camera.id is not None            # BIGINT identity assigned by PostgreSQL
    assert camera.status == "OFFLINE"       # server default
    assert camera.enabled is True
    assert camera.created_at.tzinfo is not None


def test_camera_id_is_unique(session):
    session.add(Camera(camera_id="DUP-01", camera_name="A"))
    session.flush()
    session.add(Camera(camera_id="DUP-01", camera_name="B"))
    with pytest.raises(IntegrityError):
        session.flush()


def test_camera_status_check_constraint(session):
    session.add(Camera(camera_id="BAD-01", camera_name="A", status="SLEEPING"))
    with pytest.raises(IntegrityError):
        session.flush()


def test_camera_rtsp_password_is_masked(session):
    camera = Camera(
        camera_id="SEC-01",
        camera_name="Secure",
        rtsp_url="rtsp://admin:hunter2@10.0.0.5:554/stream",
    )
    assert "hunter2" not in camera.rtsp_url_masked
    assert "admin:***" in camera.rtsp_url_masked


# -------------------------------------------------------------- visitors
def test_visitor_creation_and_code_format(session, camera):
    visitor = Visitor(camera_id=camera.id, tracking_id=7)
    session.add(visitor)
    session.flush()
    session.refresh(visitor)
    assert visitor.visitor_code.startswith("VIS-")
    assert len(visitor.visitor_code) == 10          # VIS- + 6 digits
    assert visitor.visitor_code[4:].isdigit()
    assert visitor.status == "ACTIVE"


def test_visitor_codes_are_sequential_and_unique(session, camera):
    codes = []
    for _ in range(5):
        v = Visitor(camera_id=camera.id)
        session.add(v)
        session.flush()
        session.refresh(v)
        codes.append(v.visitor_code)
    assert len(set(codes)) == 5
    numbers = [int(c[4:]) for c in codes]
    assert numbers == sorted(numbers)


def test_visitor_code_generation_is_not_max_id_plus_one(session, camera):
    """The sequence keeps advancing even after rows are deleted, so codes are never reused."""
    first = Visitor(camera_id=camera.id)
    session.add(first)
    session.flush()
    session.refresh(first)
    first_number = int(first.visitor_code[4:])

    session.delete(first)
    session.flush()

    second = Visitor(camera_id=camera.id)
    session.add(second)
    session.flush()
    session.refresh(second)
    assert int(second.visitor_code[4:]) > first_number


def test_visitor_code_is_unique(session, camera):
    v1 = Visitor(camera_id=camera.id)
    session.add(v1)
    session.flush()
    session.refresh(v1)
    session.add(Visitor(camera_id=camera.id, visitor_code=v1.visitor_code))
    with pytest.raises(IntegrityError):
        session.flush()


def test_visitor_has_no_face_embedding_column():
    """Visitors are anonymous: face data must not be storable against them."""
    columns = {c.name for c in Visitor.__table__.columns}
    assert "face_embedding" not in columns
    assert not any("face" in name or "embedding" in name for name in columns)


def test_visitor_status_check_constraint(session, camera):
    session.add(Visitor(camera_id=camera.id, status="WANDERING"))
    with pytest.raises(IntegrityError):
        session.flush()


def test_visitor_exit_must_not_precede_entry(session, camera):
    now = utc_now()
    session.add(
        Visitor(camera_id=camera.id, entry_time=now, exit_time=now - timedelta(minutes=5))
    )
    with pytest.raises(IntegrityError):
        session.flush()


def test_visitor_seat_relationship(session, visitor, seat):
    visitor.current_seat_id = seat.id
    session.flush()
    session.refresh(visitor)
    assert visitor.current_seat.seat_id == "S01"


# ----------------------------------------------------------------- staff
def test_staff_creation_and_code_format(session):
    staff = Staff(staff_name="Ravi Kumar", department="Serving")
    session.add(staff)
    session.flush()
    session.refresh(staff)
    assert staff.staff_code.startswith("STAFF-")
    assert staff.staff_code[6:].isdigit()
    assert staff.active is True


def test_staff_code_is_unique(session):
    s1 = Staff(staff_name="A")
    session.add(s1)
    session.flush()
    session.refresh(s1)
    session.add(Staff(staff_name="B", staff_code=s1.staff_code))
    with pytest.raises(IntegrityError):
        session.flush()


def test_employee_code_unique_but_nullable(session):
    """Many staff may have no employee_code; duplicates are still rejected."""
    session.add_all([Staff(staff_name="A"), Staff(staff_name="B")])
    session.flush()  # two NULLs are fine

    session.add(Staff(staff_name="C", employee_code="EMP-1"))
    session.flush()
    session.add(Staff(staff_name="D", employee_code="EMP-1"))
    with pytest.raises(IntegrityError):
        session.flush()


def test_staff_face_embedding_is_binary(session):
    """Embeddings are stored as BYTEA and round-trip byte-exact."""
    import struct

    vector = [0.1, -0.25, 0.75]
    blob = struct.pack(f"{len(vector)}f", *vector)
    staff = Staff(staff_name="Face Test", face_embedding=blob)
    session.add(staff)
    session.flush()
    session.refresh(staff)
    assert isinstance(staff.face_embedding, bytes)
    assert list(struct.unpack(f"{len(vector)}f", staff.face_embedding)) == pytest.approx(vector)


# ------------------------------------------------------ staff attendance
def test_staff_attendance_relationship(session, staff_member, camera):
    record = StaffAttendance(staff_id=staff_member.id, camera_id=camera.id)
    session.add(record)
    session.flush()
    session.refresh(record)
    assert record.status == "PRESENT"
    assert record.staff.staff_code == staff_member.staff_code
    assert record.camera.camera_id == camera.camera_id
    assert staff_member.attendance_records[0].id == record.id


def test_only_one_active_attendance_per_staff(session, staff_member, camera):
    """The partial unique index stops a new record being created on every frame."""
    session.add(StaffAttendance(staff_id=staff_member.id, camera_id=camera.id))
    session.flush()
    session.add(StaffAttendance(staff_id=staff_member.id, camera_id=camera.id))
    with pytest.raises(IntegrityError):
        session.flush()


def test_staff_may_have_multiple_closed_attendances(session, staff_member, camera):
    now = utc_now()
    for offset in (3, 2):
        session.add(
            StaffAttendance(
                staff_id=staff_member.id,
                camera_id=camera.id,
                entry_time=now - timedelta(hours=offset),
                exit_time=now - timedelta(hours=offset - 1),
                status="EXITED",
            )
        )
    session.flush()
    assert len(staff_member.attendance_records) == 2


def test_exited_attendance_requires_exit_time(session, staff_member, camera):
    session.add(
        StaffAttendance(staff_id=staff_member.id, camera_id=camera.id, status="EXITED")
    )
    with pytest.raises(IntegrityError):
        session.flush()


# ----------------------------------------------------------------- seats
def test_seat_creation_with_polygon(session, camera):
    seat = Seat(
        seat_id="S02",
        hall_id="TESTHALL",
        camera_id=camera.id,
        polygon_points=[{"x": 100, "y": 200}, {"x": 180, "y": 200}, {"x": 180, "y": 300}],
    )
    session.add(seat)
    session.flush()
    session.refresh(seat)
    assert seat.polygon_points[0]["x"] == 100     # JSONB round-trip
    assert seat.enabled is True
    assert seat.hall_id == "TESTHALL"


def test_seat_id_unique_within_hall(session, camera):
    session.add(Seat(seat_id="S09", hall_id="H1", camera_id=camera.id))
    session.flush()
    session.add(Seat(seat_id="S09", hall_id="H1", camera_id=camera.id))
    with pytest.raises(IntegrityError):
        session.flush()


def test_same_seat_id_allowed_in_different_hall(session, camera):
    session.add(Seat(seat_id="S01", hall_id="HALL-A", camera_id=camera.id))
    session.add(Seat(seat_id="S01", hall_id="HALL-B", camera_id=camera.id))
    session.flush()  # must not raise


def test_seat_polygon_must_be_json_array(session, camera):
    session.add(
        Seat(seat_id="S77", hall_id="TESTHALL", camera_id=camera.id, polygon_points={"x": 1})
    )
    with pytest.raises(IntegrityError):
        session.flush()


def test_future_seats_can_be_added(session, camera):
    """S07, S08, S09... are ordinary rows; nothing caps the seat count."""
    for index in range(7, 13):
        session.add(Seat(seat_id=f"S{index:02d}", hall_id="GROW", camera_id=camera.id))
    session.flush()
    count = session.scalar(
        select(text("count(*)")).select_from(Seat).where(Seat.hall_id == "GROW")
    )
    assert count == 6


# -------------------------------------------------------- seat occupancy
def test_seat_occupancy_by_visitor(session, seat, visitor, camera):
    occupancy = SeatOccupancy(
        seat_id=seat.id, camera_id=camera.id, person_type="VISITOR", visitor_id=visitor.id
    )
    session.add(occupancy)
    session.flush()
    session.refresh(occupancy)
    assert occupancy.status == "OCCUPIED"
    assert occupancy.visitor.visitor_code == visitor.visitor_code
    assert occupancy.staff is None
    assert occupancy.seat.seat_id == seat.seat_id


def test_seat_occupancy_by_staff(session, seat, staff_member, camera):
    occupancy = SeatOccupancy(
        seat_id=seat.id, camera_id=camera.id, person_type="STAFF", staff_id=staff_member.id
    )
    session.add(occupancy)
    session.flush()
    session.refresh(occupancy)
    assert occupancy.staff.staff_code == staff_member.staff_code
    assert occupancy.visitor is None


def test_occupancy_cannot_have_both_visitor_and_staff(session, seat, visitor, staff_member):
    session.add(
        SeatOccupancy(
            seat_id=seat.id,
            person_type="VISITOR",
            visitor_id=visitor.id,
            staff_id=staff_member.id,
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()


def test_visitor_occupancy_requires_visitor_id(session, seat):
    session.add(SeatOccupancy(seat_id=seat.id, person_type="VISITOR"))
    with pytest.raises(IntegrityError):
        session.flush()


def test_staff_occupancy_requires_staff_id(session, seat):
    session.add(SeatOccupancy(seat_id=seat.id, person_type="STAFF"))
    with pytest.raises(IntegrityError):
        session.flush()


def test_staff_person_type_rejects_visitor_id(session, seat, visitor):
    session.add(
        SeatOccupancy(seat_id=seat.id, person_type="STAFF", visitor_id=visitor.id)
    )
    with pytest.raises(IntegrityError):
        session.flush()


def test_seat_has_only_one_active_occupancy(session, seat, visitor, staff_member):
    session.add(
        SeatOccupancy(seat_id=seat.id, person_type="VISITOR", visitor_id=visitor.id)
    )
    session.flush()
    session.add(
        SeatOccupancy(seat_id=seat.id, person_type="STAFF", staff_id=staff_member.id)
    )
    with pytest.raises(IntegrityError):
        session.flush()


def test_released_occupancy_frees_the_seat(session, seat, visitor, staff_member):
    first = SeatOccupancy(
        seat_id=seat.id, person_type="VISITOR", visitor_id=visitor.id
    )
    session.add(first)
    session.flush()

    first.status = "RELEASED"
    first.released_at = utc_now()
    first.duration_seconds = 120
    session.flush()

    session.add(
        SeatOccupancy(seat_id=seat.id, person_type="STAFF", staff_id=staff_member.id)
    )
    session.flush()  # must not raise


def test_released_status_requires_released_at(session, seat, visitor):
    session.add(
        SeatOccupancy(
            seat_id=seat.id, person_type="VISITOR", visitor_id=visitor.id, status="RELEASED"
        )
    )
    with pytest.raises(IntegrityError):
        session.flush()


# ---------------------------------------------------------------- events
def test_visitor_event_relationship(session, visitor, camera):
    event = VisitorEvent(
        visitor_id=visitor.id,
        camera_id=camera.id,
        event_type="ENTERED",
        tracking_id=99,
        event_metadata={"line": "entry", "confidence": 0.91},
    )
    session.add(event)
    session.flush()
    session.refresh(event)
    assert event.event_metadata["line"] == "entry"      # JSONB round-trip
    assert event.event_time.tzinfo is not None
    assert event.visitor.visitor_code == visitor.visitor_code
    assert visitor.events[0].event_type == "ENTERED"


def test_visitor_event_type_check_constraint(session, visitor):
    session.add(VisitorEvent(visitor_id=visitor.id, event_type="TELEPORTED"))
    with pytest.raises(IntegrityError):
        session.flush()


def test_camera_event_relationship(session, camera):
    event = CameraEvent(
        camera_id=camera.id,
        event_type="OFFLINE",
        fps=0.0,
        message="Stream timed out",
        event_metadata={"retries": 3},
    )
    session.add(event)
    session.flush()
    session.refresh(event)
    assert event.camera.camera_id == camera.camera_id
    assert event.event_metadata["retries"] == 3
    assert camera.camera_events[0].event_type == "OFFLINE"


def test_camera_event_type_check_constraint(session, camera):
    session.add(CameraEvent(camera_id=camera.id, event_type="EXPLODED"))
    with pytest.raises(IntegrityError):
        session.flush()


# ------------------------------------------------- foreign keys / cascade
def test_foreign_key_rejects_missing_camera(session):
    session.add(Visitor(camera_id=9_999_999))
    with pytest.raises(IntegrityError):
        session.flush()


def test_deleting_staff_with_attendance_is_blocked(session, staff_member, camera):
    """RESTRICT protects historical attendance from disappearing with the staff row."""
    session.add(StaffAttendance(staff_id=staff_member.id, camera_id=camera.id))
    session.flush()
    session.delete(staff_member)
    with pytest.raises(IntegrityError):
        session.flush()


def test_deleting_seat_with_occupancy_is_blocked(session, seat, visitor):
    session.add(
        SeatOccupancy(seat_id=seat.id, person_type="VISITOR", visitor_id=visitor.id)
    )
    session.flush()
    session.delete(seat)
    with pytest.raises(IntegrityError):
        session.flush()


def test_deleting_camera_nulls_visitor_reference(session, camera):
    """Visitor history survives camera removal; the reference is simply cleared."""
    visitor = Visitor(camera_id=camera.id)
    session.add(visitor)
    session.flush()
    visitor_id = visitor.id

    session.execute(text("DELETE FROM camera_events WHERE camera_id = :cid"), {"cid": camera.id})
    session.delete(camera)
    session.flush()
    session.expire_all()

    survivor = session.get(Visitor, visitor_id)
    assert survivor is not None
    assert survivor.camera_id is None


# --------------------------------------------------------------- columns
def test_all_timestamp_columns_are_timezone_aware():
    """No naive datetime columns anywhere in the schema."""
    from sqlalchemy import DateTime

    from app.models import Base

    naive = [
        f"{table.name}.{column.name}"
        for table in Base.metadata.tables.values()
        for column in table.columns
        if isinstance(column.type, DateTime) and not column.type.timezone
    ]
    assert naive == [], f"Naive datetime columns found: {naive}"


def test_primary_keys_are_bigint_identity():
    from sqlalchemy import BigInteger

    from app.models import Base

    for table in Base.metadata.tables.values():
        pks = list(table.primary_key.columns)
        assert len(pks) == 1, f"{table.name} should have a single-column primary key"
        assert isinstance(pks[0].type, BigInteger), f"{table.name}.{pks[0].name} is not BIGINT"
        assert pks[0].identity is not None, f"{table.name}.{pks[0].name} is not IDENTITY"


def test_timestamps_stored_are_utc(session, camera):
    """PostgreSQL returns timestamptz with an offset; stored instants are UTC."""
    session.refresh(camera)
    assert camera.created_at.utcoffset().total_seconds() == 0
