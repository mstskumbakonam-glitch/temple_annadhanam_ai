"""phase5b crowd analytics: per-camera analytics config, alerts, count history

* cameras.analytics_config (JSONB, nullable): counting lines, zones, thresholds.
* crowd_alerts: one row per debounced alert (raised -> cleared).
* crowd_count_snapshots: per-minute people counts per camera / zone, for trends.

Line crossings reuse visitor_events (ENTERED / EXITED were allowed since
Phase 3) with visitor_id NULL, so no visitor identity is invented.

Purely additive: no existing column or row changes. Downgrade drops the two
tables and the column (and with them any analytics history).

Revision ID: c7d1a2e5f901
Revises: ab4842faaacb
Create Date: 2026-10-09 14:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c7d1a2e5f901"
down_revision: Union[str, None] = "ab4842faaacb"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "cameras",
        sa.Column("analytics_config", postgresql.JSONB(none_as_null=True), nullable=True),
    )

    op.create_table(
        "crowd_alerts",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("alert_uid", sa.String(length=32), nullable=False),
        sa.Column("camera_id", sa.BigInteger(), nullable=False),
        sa.Column("zone_id", sa.String(length=32), nullable=True),
        sa.Column("alert_type", sa.String(length=32), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("peak_value", sa.Float(), nullable=True),
        sa.Column("threshold", sa.Float(), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", postgresql.JSONB(none_as_null=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("alert_type IN ('CROWD_DENSITY', 'QUEUE_CONGESTION')",
                           name=op.f("ck_crowd_alerts_alert_type_valid")),
        sa.CheckConstraint("severity IN ('warning', 'critical')",
                           name=op.f("ck_crowd_alerts_severity_valid")),
        sa.CheckConstraint("ended_at IS NULL OR ended_at >= started_at",
                           name=op.f("ck_crowd_alerts_ended_after_started")),
        sa.ForeignKeyConstraint(["camera_id"], ["cameras.id"], ondelete="CASCADE",
                                name=op.f("fk_crowd_alerts_camera_id_cameras")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_crowd_alerts")),
        sa.UniqueConstraint("alert_uid", name=op.f("uq_crowd_alerts_alert_uid")),
    )
    op.create_index(op.f("ix_crowd_alerts_camera_id"), "crowd_alerts", ["camera_id"])
    op.create_index("ix_crowd_alerts_started_at", "crowd_alerts", ["started_at"])
    op.create_index("ix_crowd_alerts_open", "crowd_alerts", ["camera_id"],
                    postgresql_where=sa.text("ended_at IS NULL"))

    op.create_table(
        "crowd_count_snapshots",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("camera_id", sa.BigInteger(), nullable=False),
        sa.Column("zone_id", sa.String(length=32), nullable=False),
        sa.Column("bucket_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("samples", sa.Integer(), nullable=False),
        sa.Column("avg_count", sa.Float(), nullable=False),
        sa.Column("max_count", sa.Integer(), nullable=False),
        sa.Column("entries", sa.Integer(), nullable=False),
        sa.Column("exits", sa.Integer(), nullable=False),
        sa.CheckConstraint("samples > 0", name=op.f("ck_crowd_count_snapshots_samples_positive")),
        sa.CheckConstraint("avg_count >= 0 AND max_count >= 0",
                           name=op.f("ck_crowd_count_snapshots_counts_non_negative")),
        sa.CheckConstraint("entries >= 0 AND exits >= 0",
                           name=op.f("ck_crowd_count_snapshots_crossings_non_negative")),
        sa.ForeignKeyConstraint(["camera_id"], ["cameras.id"], ondelete="CASCADE",
                                name=op.f("fk_crowd_count_snapshots_camera_id_cameras")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_crowd_count_snapshots")),
        sa.UniqueConstraint("camera_id", "zone_id", "bucket_start",
                            name=op.f("uq_crowd_count_snapshots_camera_id_zone_id_bucket_start")),
    )
    op.create_index("ix_crowd_count_snapshots_bucket_start", "crowd_count_snapshots", ["bucket_start"])


def downgrade() -> None:
    op.drop_index("ix_crowd_count_snapshots_bucket_start", table_name="crowd_count_snapshots")
    op.drop_table("crowd_count_snapshots")
    op.drop_index("ix_crowd_alerts_open", table_name="crowd_alerts")
    op.drop_index("ix_crowd_alerts_started_at", table_name="crowd_alerts")
    op.drop_index(op.f("ix_crowd_alerts_camera_id"), table_name="crowd_alerts")
    op.drop_table("crowd_alerts")
    op.drop_column("cameras", "analytics_config")
