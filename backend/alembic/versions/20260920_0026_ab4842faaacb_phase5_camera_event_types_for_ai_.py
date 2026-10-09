"""phase5 camera event types for ai lifecycle

Extends the camera_events.event_type CHECK constraint with AI_STARTED and
AI_STOPPED so the AI pipeline's start/stop can be recorded.

Why a migration is needed: the Phase 3 constraint allows only
ONLINE / OFFLINE / ERROR / RECOVERED. Those describe the camera's connection
health and already cover CAMERA_CONNECTED, CAMERA_DISCONNECTED and STREAM_ERROR.
"AI stopped" is not a connection state - the camera may be healthy - and forcing
it into OFFLINE would record something false. No tables or columns change.

Downgrade removes the AI_STARTED / AI_STOPPED rows first, because the narrower
constraint cannot hold them. Those rows only describe pipeline lifecycle.

Revision ID: ab4842faaacb
Revises: de2d51593dcd
Create Date: 2026-09-20 00:26:24.228912
"""
from typing import Sequence, Union

from alembic import op

revision: str = "ab4842faaacb"
down_revision: Union[str, None] = "de2d51593dcd"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CONSTRAINT = "ck_camera_events_event_type_valid"
TABLE = "camera_events"

OLD_VALUES = ("ONLINE", "OFFLINE", "ERROR", "RECOVERED")
NEW_VALUES = OLD_VALUES + ("AI_STARTED", "AI_STOPPED")


def _condition(values: tuple[str, ...]) -> str:
    return "event_type IN (" + ", ".join(f"'{v}'" for v in values) + ")"


def upgrade() -> None:
    op.drop_constraint(op.f(CONSTRAINT), TABLE, type_="check")
    op.create_check_constraint(op.f(CONSTRAINT), TABLE, _condition(NEW_VALUES))


def downgrade() -> None:
    op.execute("DELETE FROM camera_events WHERE event_type IN ('AI_STARTED', 'AI_STOPPED')")
    op.drop_constraint(op.f(CONSTRAINT), TABLE, type_="check")
    op.create_check_constraint(op.f(CONSTRAINT), TABLE, _condition(OLD_VALUES))
