"""Migration tests: the schema is built and torn down by Alembic, never create_all()."""

import re
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text

from app.config import get_settings

pytestmark = pytest.mark.db

BACKEND_DIR = Path(__file__).resolve().parent.parent

EXPECTED_TABLES = {
    "cameras",
    "visitors",
    "staff",
    "staff_attendance",
    "seats",
    "seat_occupancy",
    "visitor_events",
    "camera_events",
    "crowd_alerts",
    "crowd_count_snapshots",
}

PHASE2_REVISION = "6bce95798004"


@pytest.fixture
def alembic_cfg(test_database_url):
    """Alembic config pointed at the TEST database, restored afterwards."""
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))

    settings = get_settings()
    original = settings.database_url
    object.__setattr__(settings, "database_url", test_database_url)
    try:
        yield config
    finally:
        object.__setattr__(settings, "database_url", original)


PHASE3_REVISION = "de2d51593dcd"
PHASE5_REVISION = "ab4842faaacb"
PHASE5B_REVISION = "c7d1a2e5f901"   # crowd analytics (additive)


def test_revision_history_is_linear_from_phase2_to_head():
    """History is linear: phase 2 baseline -> phase 3 models -> phase 5 event types -> head."""
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    script = ScriptDirectory.from_config(config)

    heads = script.get_heads()
    assert len(heads) == 1, f"Expected a single head, found {heads}"

    chain = [rev.revision for rev in script.walk_revisions()]   # newest first
    assert chain[-1] == PHASE2_REVISION
    assert chain[-2:][0] == PHASE3_REVISION
    assert script.get_revision(PHASE3_REVISION).down_revision == PHASE2_REVISION
    assert script.get_revision(PHASE5_REVISION).down_revision == PHASE3_REVISION
    assert script.get_revision(PHASE5B_REVISION).down_revision == PHASE5_REVISION
    assert heads[0] == chain[0]


def test_upgrade_head_creates_all_tables(alembic_cfg, test_engine):
    command.upgrade(alembic_cfg, "head")
    tables = set(inspect(test_engine).get_table_names())
    missing = EXPECTED_TABLES - tables
    assert not missing, f"Missing tables after upgrade: {missing}"


def test_sequences_exist_after_upgrade(alembic_cfg, test_engine):
    command.upgrade(alembic_cfg, "head")
    with test_engine.connect() as conn:
        names = set(
            conn.scalars(
                text("SELECT sequence_name FROM information_schema.sequences "
                     "WHERE sequence_schema = 'public'")
            )
        )
    assert {"visitor_code_seq", "staff_code_seq"} <= names


def test_downgrade_to_phase2_removes_phase3_objects(alembic_cfg, test_engine):
    command.upgrade(alembic_cfg, "head")
    command.downgrade(alembic_cfg, PHASE2_REVISION)

    tables = set(inspect(test_engine).get_table_names())
    assert not (EXPECTED_TABLES & tables), "Phase 3 tables survived the downgrade"

    with test_engine.connect() as conn:
        names = set(
            conn.scalars(
                text("SELECT sequence_name FROM information_schema.sequences "
                     "WHERE sequence_schema = 'public'")
            )
        )
    assert not ({"visitor_code_seq", "staff_code_seq"} & names), "Sequences leaked"

    # Leave the database at head for the rest of the suite.
    command.upgrade(alembic_cfg, "head")


def test_upgrade_again_after_downgrade(alembic_cfg, test_engine):
    """Full down/up cycle leaves the schema in the expected state."""
    command.upgrade(alembic_cfg, "head")
    command.downgrade(alembic_cfg, PHASE2_REVISION)
    command.upgrade(alembic_cfg, "head")

    tables = set(inspect(test_engine).get_table_names())
    assert EXPECTED_TABLES <= tables


def test_expected_indexes_exist(alembic_cfg, test_engine):
    command.upgrade(alembic_cfg, "head")
    inspector = inspect(test_engine)

    required = {
        "visitors": {"ix_visitors_status", "ix_visitors_entry_time", "ix_visitors_camera_id"},
        "staff_attendance": {
            "ix_staff_attendance_staff_id",
            "ix_staff_attendance_entry_time",
            "ix_staff_attendance_status",
            "uq_staff_attendance_active",
        },
        "seats": {"ix_seats_seat_id", "ix_seats_camera_id"},
        "seat_occupancy": {
            "ix_seat_occupancy_seat_id",
            "ix_seat_occupancy_visitor_id",
            "ix_seat_occupancy_staff_id",
            "ix_seat_occupancy_occupied_at",
            "ix_seat_occupancy_status",
            "uq_seat_occupancy_active",
        },
        "visitor_events": {"ix_visitor_events_visitor_id", "ix_visitor_events_event_time"},
        "camera_events": {"ix_camera_events_camera_id", "ix_camera_events_event_time"},
    }

    for table, expected in required.items():
        found = {index["name"] for index in inspector.get_indexes(table)}
        missing = expected - found
        assert not missing, f"{table} is missing indexes: {missing}"


def test_expected_foreign_keys_exist(alembic_cfg, test_engine):
    command.upgrade(alembic_cfg, "head")
    inspector = inspect(test_engine)

    def referred(table: str) -> set[tuple[str, str]]:
        return {
            (fk["constrained_columns"][0], fk["referred_table"])
            for fk in inspector.get_foreign_keys(table)
        }

    assert {("camera_id", "cameras"), ("current_seat_id", "seats")} <= referred("visitors")
    assert {("staff_id", "staff"), ("camera_id", "cameras")} <= referred("staff_attendance")
    assert {("camera_id", "cameras")} <= referred("seats")
    assert {
        ("seat_id", "seats"),
        ("camera_id", "cameras"),
        ("visitor_id", "visitors"),
        ("staff_id", "staff"),
    } <= referred("seat_occupancy")
    assert {("visitor_id", "visitors"), ("camera_id", "cameras")} <= referred("visitor_events")
    assert {("camera_id", "cameras")} <= referred("camera_events")


def test_unique_constraints_exist(alembic_cfg, test_engine):
    command.upgrade(alembic_cfg, "head")
    inspector = inspect(test_engine)

    def unique_columns(table: str) -> list[list[str]]:
        constraints = [c["column_names"] for c in inspector.get_unique_constraints(table)]
        constraints += [
            i["column_names"] for i in inspector.get_indexes(table) if i.get("unique")
        ]
        return constraints

    assert ["camera_id"] in unique_columns("cameras")
    assert ["visitor_code"] in unique_columns("visitors")
    assert ["staff_code"] in unique_columns("staff")
    assert ["employee_code"] in unique_columns("staff")
    assert ["hall_id", "seat_id"] in unique_columns("seats")


def test_no_pending_model_drift(alembic_cfg, test_engine):
    """Models and migrated schema agree: autogenerate would produce nothing."""
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    from app.models import Base

    command.upgrade(alembic_cfg, "head")
    with test_engine.connect() as connection:
        context = MigrationContext.configure(
            connection,
            opts={"compare_type": True, "compare_server_default": True},
        )
        diff = compare_metadata(context, Base.metadata)
    assert diff == [], f"Model/schema drift detected: {diff}"


# ---------------------------------------------------------------- Phase 5
def test_head_is_the_phase5b_revision():
    script = ScriptDirectory.from_config(Config(str(BACKEND_DIR / "alembic.ini")))
    assert script.get_heads() == [PHASE5B_REVISION]


def test_phase5b_downgrade_drops_only_analytics_objects(alembic_cfg, test_engine):
    command.upgrade(alembic_cfg, "head")
    command.downgrade(alembic_cfg, PHASE5_REVISION)
    insp = inspect(test_engine)
    tables = set(insp.get_table_names())
    assert "crowd_alerts" not in tables and "crowd_count_snapshots" not in tables
    assert "analytics_config" not in {c["name"] for c in insp.get_columns("cameras")}
    assert "camera_events" in tables
    command.upgrade(alembic_cfg, "head")
    assert {"crowd_alerts", "crowd_count_snapshots"} <= set(inspect(test_engine).get_table_names())


def test_camera_event_constraint_matches_the_enum(alembic_cfg, test_engine):
    """The CHECK constraint and CameraEventType must never drift apart."""
    from app.models.enums import CameraEventType

    command.upgrade(alembic_cfg, "head")
    with test_engine.connect() as conn:
        definition = conn.scalar(text(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conname = 'ck_camera_events_event_type_valid'"))
    allowed = set(re.findall(r"'([A-Z_]+)'", definition))
    assert allowed == {member.value for member in CameraEventType}
    assert {"AI_STARTED", "AI_STOPPED"} <= allowed


def test_ai_event_types_are_accepted_after_upgrade(alembic_cfg, test_engine):
    command.upgrade(alembic_cfg, "head")
    with test_engine.begin() as conn:
        conn.execute(text("INSERT INTO cameras (camera_id, camera_name) VALUES ('MIG5-OK', 'x')"))
        try:
            for event in ("AI_STARTED", "AI_STOPPED", "ONLINE", "RECOVERED"):
                conn.execute(text(
                    "INSERT INTO camera_events (camera_id, event_type) "
                    "SELECT id, :e FROM cameras WHERE camera_id = 'MIG5-OK'"), {"e": event})
        finally:
            conn.execute(text("DELETE FROM cameras WHERE camera_id = 'MIG5-OK'"))


def test_unknown_event_types_are_still_rejected(alembic_cfg, test_engine):
    from sqlalchemy.exc import IntegrityError

    command.upgrade(alembic_cfg, "head")
    with pytest.raises(IntegrityError):
        with test_engine.begin() as conn:
            conn.execute(text("INSERT INTO cameras (camera_id, camera_name) VALUES ('MIG5-BAD', 'x')"))
            conn.execute(text(
                "INSERT INTO camera_events (camera_id, event_type) "
                "SELECT id, 'EXPLODED' FROM cameras WHERE camera_id = 'MIG5-BAD'"))
    with test_engine.begin() as conn:
        conn.execute(text("DELETE FROM cameras WHERE camera_id = 'MIG5-BAD'"))


def test_phase5_downgrade_removes_ai_rows_restores_constraint_and_upgrades_again(alembic_cfg, test_engine):
    from sqlalchemy.exc import IntegrityError

    command.upgrade(alembic_cfg, "head")
    with test_engine.begin() as conn:
        conn.execute(text("INSERT INTO cameras (camera_id, camera_name) VALUES ('MIG5-DOWN', 'x')"))
        conn.execute(text(
            "INSERT INTO camera_events (camera_id, event_type) "
            "SELECT id, t FROM cameras, (VALUES ('AI_STARTED'), ('ONLINE')) AS v(t) "
            "WHERE camera_id = 'MIG5-DOWN'"))

    command.downgrade(alembic_cfg, PHASE3_REVISION)
    try:
        with test_engine.connect() as conn:
            remaining = set(conn.scalars(text(
                "SELECT event_type FROM camera_events e JOIN cameras c ON c.id = e.camera_id "
                "WHERE c.camera_id = 'MIG5-DOWN'")))
        assert remaining == {"ONLINE"}                       # only the AI row was removed
        with pytest.raises(IntegrityError):
            with test_engine.begin() as conn:
                conn.execute(text(
                    "INSERT INTO camera_events (camera_id, event_type) "
                    "SELECT id, 'AI_STOPPED' FROM cameras WHERE camera_id = 'MIG5-DOWN'"))
    finally:
        command.upgrade(alembic_cfg, "head")
        with test_engine.begin() as conn:
            conn.execute(text("DELETE FROM cameras WHERE camera_id = 'MIG5-DOWN'"))

    with test_engine.begin() as conn:                        # accepted again after re-upgrade
        conn.execute(text("INSERT INTO cameras (camera_id, camera_name) VALUES ('MIG5-UP', 'x')"))
        conn.execute(text(
            "INSERT INTO camera_events (camera_id, event_type) "
            "SELECT id, 'AI_STOPPED' FROM cameras WHERE camera_id = 'MIG5-UP'"))
        conn.execute(text("DELETE FROM cameras WHERE camera_id = 'MIG5-UP'"))
