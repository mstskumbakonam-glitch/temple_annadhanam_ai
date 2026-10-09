"""phase5c temple and hall management: temples, halls, seat status, sessions, staff, attendance

* temples, annadhanam_halls (hall_code unique).
* seats.hall_id becomes a foreign key to annadhanam_halls.hall_code (ON UPDATE CASCADE).
  Existing seats whose hall code has no hall are attached to halls created under
  ONE clearly labelled placeholder temple "UNASSIGNED" (name "Unassigned (migrated)").
  Nothing is invented beyond what is needed to keep existing rows valid.
* seats gain a confirmed status (AVAILABLE / OCCUPIED / RESERVED / OUT_OF_SERVICE),
  its source, timestamp, reservation reference and a version for optimistic locking.
* seat_status_events: history of confirmed status changes.
* annadhanam_sessions with an EXCLUDE constraint (btree_gist) so two non-cancelled
  sessions can never overlap in the same hall.
* staff: phone, designation, shift, temple/hall assignment.
* staff_daily_attendance: the daily register (one row per person per day).

Revision ID: d4e8f2a61b37
Revises: c7d1a2e5f901
Create Date: 2026-10-09 18:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d4e8f2a61b37"
down_revision: Union[str, None] = "c7d1a2e5f901"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SEAT_STATUSES = "'AVAILABLE', 'OCCUPIED', 'RESERVED', 'OUT_OF_SERVICE'"


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    ]


def upgrade() -> None:
    # btree_gist is a trusted extension (PostgreSQL 13+): the database owner may create it.
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")

    # ------------------------------------------------------------- temples
    op.create_table(
        "temples",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("temple_code", sa.String(32), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("address", sa.String(512)),
        sa.Column("district", sa.String(80)),
        sa.Column("contact_phone", sa.String(32)),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("is_demo", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_temples")),
        sa.UniqueConstraint("temple_code", name=op.f("uq_temples_temple_code")),
    )
    op.create_index("ix_temples_district", "temples", ["district"])
    op.create_index("ix_temples_active", "temples", ["active"])
    op.create_index("ix_temples_name_lower", "temples", [sa.text("lower(name)")])

    # --------------------------------------------------------------- halls
    op.create_table(
        "annadhanam_halls",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("hall_code", sa.String(64), nullable=False),
        sa.Column("temple_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("building", sa.String(120)),
        sa.Column("floor", sa.String(40)),
        sa.Column("location_note", sa.String(255)),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("is_demo", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        *_timestamps(),
        sa.CheckConstraint("length(name) > 0", name=op.f("ck_annadhanam_halls_name_not_empty")),
        sa.ForeignKeyConstraint(["temple_id"], ["temples.id"], ondelete="RESTRICT",
                                name=op.f("fk_annadhanam_halls_temple_id_temples")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_annadhanam_halls")),
        sa.UniqueConstraint("hall_code", name=op.f("uq_annadhanam_halls_hall_code")),
    )
    op.create_index(op.f("ix_annadhanam_halls_temple_id"), "annadhanam_halls", ["temple_id"])
    op.create_index("ix_annadhanam_halls_active", "annadhanam_halls", ["active"])

    # Keep existing seats valid: give every hall code already used by a seat a hall.
    op.execute("""
        INSERT INTO temples (temple_code, name, address, active)
        SELECT 'UNASSIGNED', 'Unassigned (migrated)',
               'Placeholder created by migration d4e8f2a61b37 for seats that existed before halls. '
               'Move these halls to their real temple and then delete this record.', true
        WHERE EXISTS (SELECT 1 FROM seats)
    """)
    op.execute("""
        INSERT INTO annadhanam_halls (hall_code, temple_id, name)
        SELECT DISTINCT s.hall_id, t.id, 'Hall ' || s.hall_id || ' (migrated)'
        FROM seats s CROSS JOIN temples t
        WHERE t.temple_code = 'UNASSIGNED'
    """)

    # ----------------------------------------------------------- sessions
    op.create_table(
        "annadhanam_sessions",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("hall_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("meal_type", sa.String(16), nullable=False),
        sa.Column("session_date", sa.Date(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expected_devotees", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("actual_devotees", sa.Integer()),
        sa.Column("status", sa.String(16), server_default=sa.text("'SCHEDULED'"), nullable=False),
        sa.Column("responsible_staff_id", sa.BigInteger()),
        sa.Column("notes", sa.Text()),
        sa.Column("cancel_reason", sa.String(255)),
        sa.Column("is_demo", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        *_timestamps(),
        sa.CheckConstraint("ends_at > starts_at", name=op.f("ck_annadhanam_sessions_ends_after_starts")),
        sa.CheckConstraint("expected_devotees >= 0", name=op.f("ck_annadhanam_sessions_expected_non_negative")),
        sa.CheckConstraint("actual_devotees IS NULL OR actual_devotees >= 0",
                           name=op.f("ck_annadhanam_sessions_actual_non_negative")),
        sa.CheckConstraint("status IN ('SCHEDULED', 'IN_PROGRESS', 'COMPLETED', 'CANCELLED')",
                           name=op.f("ck_annadhanam_sessions_status_valid")),
        sa.CheckConstraint("meal_type IN ('BREAKFAST', 'LUNCH', 'DINNER', 'PRASADAM', 'SPECIAL')",
                           name=op.f("ck_annadhanam_sessions_meal_type_valid")),
        sa.CheckConstraint("(status = 'CANCELLED') = (cancel_reason IS NOT NULL)",
                           name=op.f("ck_annadhanam_sessions_cancel_reason_matches_status")),
        sa.ForeignKeyConstraint(["hall_id"], ["annadhanam_halls.id"], ondelete="RESTRICT",
                                name=op.f("fk_annadhanam_sessions_hall_id_annadhanam_halls")),
        sa.ForeignKeyConstraint(["responsible_staff_id"], ["staff.id"], ondelete="SET NULL",
                                name=op.f("fk_annadhanam_sessions_responsible_staff_id_staff")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_annadhanam_sessions")),
    )
    op.create_index(op.f("ix_annadhanam_sessions_hall_id"), "annadhanam_sessions", ["hall_id"])
    op.create_index(op.f("ix_annadhanam_sessions_responsible_staff_id"), "annadhanam_sessions",
                    ["responsible_staff_id"])
    op.create_index("ix_annadhanam_sessions_session_date", "annadhanam_sessions", ["session_date"])
    op.create_index("ix_annadhanam_sessions_starts_at", "annadhanam_sessions", ["starts_at"])
    op.create_index("ix_annadhanam_sessions_status", "annadhanam_sessions", ["status"])
    op.execute("""
        ALTER TABLE annadhanam_sessions ADD CONSTRAINT no_overlapping_sessions
        EXCLUDE USING gist (hall_id WITH =, tstzrange(starts_at, ends_at, '[)') WITH &&)
        WHERE (status <> 'CANCELLED')
    """)

    # --------------------------------------------------------------- seats
    op.alter_column("seats", "hall_id", server_default=None)
    op.create_foreign_key(op.f("fk_seats_hall_id_annadhanam_halls"), "seats", "annadhanam_halls",
                          ["hall_id"], ["hall_code"], ondelete="RESTRICT", onupdate="CASCADE")
    op.add_column("seats", sa.Column("status", sa.String(16), server_default=sa.text("'AVAILABLE'"), nullable=False))
    op.add_column("seats", sa.Column("status_source", sa.String(16), server_default=sa.text("'MANUAL'"), nullable=False))
    op.add_column("seats", sa.Column("status_since", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False))
    op.add_column("seats", sa.Column("reservation_ref", sa.String(64)))
    op.add_column("seats", sa.Column("reserved_session_id", sa.BigInteger()))
    op.add_column("seats", sa.Column("status_version", sa.Integer(), server_default=sa.text("1"), nullable=False))
    op.create_foreign_key(op.f("fk_seats_reserved_session_id_annadhanam_sessions"), "seats",
                          "annadhanam_sessions", ["reserved_session_id"], ["id"], ondelete="SET NULL")
    op.create_index(op.f("ix_seats_reserved_session_id"), "seats", ["reserved_session_id"])
    op.create_index("ix_seats_hall_id_status", "seats", ["hall_id", "status"])
    op.create_check_constraint(op.f("ck_seats_status_valid"), "seats", f"status IN ({SEAT_STATUSES})")
    op.create_check_constraint(op.f("ck_seats_status_source_valid"), "seats",
                               "status_source IN ('MANUAL', 'AI_CONFIRMED')")
    op.create_check_constraint(
        op.f("ck_seats_reservation_only_when_reserved"), "seats",
        "status = 'RESERVED' OR (reservation_ref IS NULL AND reserved_session_id IS NULL)")

    op.create_table(
        "seat_status_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("seat_id", sa.BigInteger(), nullable=False),
        sa.Column("hall_id", sa.String(64), nullable=False),
        sa.Column("from_status", sa.String(16), nullable=False),
        sa.Column("to_status", sa.String(16), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("actor_role", sa.String(16)),
        sa.Column("reservation_ref", sa.String(64)),
        sa.Column("session_id", sa.BigInteger()),
        sa.Column("previous_duration_seconds", sa.Integer()),
        sa.Column("note", sa.String(255)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(f"to_status IN ({SEAT_STATUSES})", name=op.f("ck_seat_status_events_to_status_valid")),
        sa.ForeignKeyConstraint(["seat_id"], ["seats.id"], ondelete="CASCADE",
                                name=op.f("fk_seat_status_events_seat_id_seats")),
        sa.ForeignKeyConstraint(["session_id"], ["annadhanam_sessions.id"], ondelete="SET NULL",
                                name=op.f("fk_seat_status_events_session_id_annadhanam_sessions")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_seat_status_events")),
    )
    op.create_index(op.f("ix_seat_status_events_seat_id"), "seat_status_events", ["seat_id"])
    op.create_index("ix_seat_status_events_changed_at", "seat_status_events", ["changed_at"])
    op.create_index("ix_seat_status_events_hall_id_changed_at", "seat_status_events", ["hall_id", "changed_at"])

    # --------------------------------------------------------------- staff
    op.add_column("staff", sa.Column("phone", sa.String(32)))
    op.add_column("staff", sa.Column("designation", sa.String(80)))
    op.add_column("staff", sa.Column("shift", sa.String(16)))
    op.add_column("staff", sa.Column("temple_id", sa.BigInteger()))
    op.add_column("staff", sa.Column("hall_id", sa.BigInteger()))
    op.add_column("staff", sa.Column("is_demo", sa.Boolean(), server_default=sa.text("false"), nullable=False))
    op.create_foreign_key(op.f("fk_staff_temple_id_temples"), "staff", "temples", ["temple_id"], ["id"],
                          ondelete="SET NULL")
    op.create_foreign_key(op.f("fk_staff_hall_id_annadhanam_halls"), "staff", "annadhanam_halls",
                          ["hall_id"], ["id"], ondelete="SET NULL")
    op.create_index(op.f("ix_staff_temple_id"), "staff", ["temple_id"])
    op.create_index(op.f("ix_staff_hall_id"), "staff", ["hall_id"])
    op.create_check_constraint(
        op.f("ck_staff_shift_valid"), "staff",
        "shift IS NULL OR shift IN ('MORNING', 'AFTERNOON', 'EVENING', 'NIGHT', 'FULL_DAY')")

    op.create_table(
        "staff_daily_attendance",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("staff_id", sa.BigInteger(), nullable=False),
        sa.Column("attendance_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("check_in_at", sa.DateTime(timezone=True)),
        sa.Column("check_out_at", sa.DateTime(timezone=True)),
        sa.Column("note", sa.String(255)),
        sa.Column("source", sa.String(16), server_default=sa.text("'MANUAL'"), nullable=False),
        *_timestamps(),
        sa.CheckConstraint("status IN ('PRESENT', 'HALF_DAY', 'ABSENT', 'LEAVE')",
                           name=op.f("ck_staff_daily_attendance_status_valid")),
        sa.CheckConstraint("check_out_at IS NULL OR check_in_at IS NULL OR check_out_at >= check_in_at",
                           name=op.f("ck_staff_daily_attendance_check_out_after_check_in")),
        sa.ForeignKeyConstraint(["staff_id"], ["staff.id"], ondelete="RESTRICT",
                                name=op.f("fk_staff_daily_attendance_staff_id_staff")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_staff_daily_attendance")),
        sa.UniqueConstraint("staff_id", "attendance_date",
                            name=op.f("uq_staff_daily_attendance_staff_id_attendance_date")),
    )
    op.create_index(op.f("ix_staff_daily_attendance_staff_id"), "staff_daily_attendance", ["staff_id"])
    op.create_index("ix_staff_daily_attendance_date", "staff_daily_attendance", ["attendance_date"])


def downgrade() -> None:
    op.drop_table("staff_daily_attendance")
    op.drop_constraint(op.f("ck_staff_shift_valid"), "staff", type_="check")
    op.drop_index(op.f("ix_staff_hall_id"), table_name="staff")
    op.drop_index(op.f("ix_staff_temple_id"), table_name="staff")
    op.drop_constraint(op.f("fk_staff_hall_id_annadhanam_halls"), "staff", type_="foreignkey")
    op.drop_constraint(op.f("fk_staff_temple_id_temples"), "staff", type_="foreignkey")
    for column in ("is_demo", "hall_id", "temple_id", "shift", "designation", "phone"):
        op.drop_column("staff", column)

    op.drop_table("seat_status_events")
    op.drop_constraint(op.f("ck_seats_reservation_only_when_reserved"), "seats", type_="check")
    op.drop_constraint(op.f("ck_seats_status_source_valid"), "seats", type_="check")
    op.drop_constraint(op.f("ck_seats_status_valid"), "seats", type_="check")
    op.drop_index("ix_seats_hall_id_status", table_name="seats")
    op.drop_index(op.f("ix_seats_reserved_session_id"), table_name="seats")
    op.drop_constraint(op.f("fk_seats_reserved_session_id_annadhanam_sessions"), "seats", type_="foreignkey")
    for column in ("status_version", "reserved_session_id", "reservation_ref", "status_since",
                   "status_source", "status"):
        op.drop_column("seats", column)
    op.drop_constraint(op.f("fk_seats_hall_id_annadhanam_halls"), "seats", type_="foreignkey")
    op.alter_column("seats", "hall_id", server_default=sa.text("'MAIN'"))

    op.drop_table("annadhanam_sessions")
    op.drop_table("annadhanam_halls")
    op.drop_table("temples")
    # btree_gist is left installed: other objects may use it and it is harmless.
