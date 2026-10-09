"""Shared route dependencies.

Authentication is not required yet. When it is added, a `current_user`
dependency slots in here and is attached to routers via `dependencies=[...]`
without touching individual route functions.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Annotated

from fastapi import Depends, Query

from app.database import get_db  # re-exported: the single session system
from app.schemas.common import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE

__all__ = ["get_db", "Pagination", "PaginationParams", "TimeRange", "TimeRangeParams"]


@dataclass(frozen=True)
class Pagination:
    page: int
    page_size: int


def pagination_params(
    page: int = Query(1, ge=1, description="1-based page number."),
    page_size: int = Query(
        DEFAULT_PAGE_SIZE,
        ge=1,
        le=MAX_PAGE_SIZE,
        description=f"Rows per page (max {MAX_PAGE_SIZE}).",
    ),
) -> Pagination:
    return Pagination(page=page, page_size=page_size)


@dataclass(frozen=True)
class TimeRange:
    start_time: datetime | None
    end_time: datetime | None


def time_range_params(
    start_time: datetime | None = Query(
        None, description="Inclusive lower bound, ISO 8601. Naive values are treated as UTC."
    ),
    end_time: datetime | None = Query(None, description="Exclusive upper bound, ISO 8601."),
) -> TimeRange:
    from app.utils.time import to_utc

    return TimeRange(
        start_time=to_utc(start_time) if start_time else None,
        end_time=to_utc(end_time) if end_time else None,
    )


PaginationParams = Annotated[Pagination, Depends(pagination_params)]
TimeRangeParams = Annotated[TimeRange, Depends(time_range_params)]
