"""Time helpers. The application works in UTC internally and converts only for display."""

from datetime import datetime, timezone


def utc_now() -> datetime:
    """Current time as a timezone-aware UTC datetime (never a naive one)."""
    return datetime.now(timezone.utc)


def to_utc(value: datetime) -> datetime:
    """Normalise any datetime to UTC. Naive input is assumed to already be UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
