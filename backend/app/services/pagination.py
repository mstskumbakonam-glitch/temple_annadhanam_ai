"""Pagination helper.

Counts and slices in PostgreSQL so a list endpoint never loads an unbounded
result set into memory.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class PageResult:
    items: list[Any]
    page: int
    page_size: int
    total: int


def paginate(session: Session, statement: Select, page: int, page_size: int) -> PageResult:
    """Run `statement` for one page and return the rows plus the total count."""
    count_statement = select(func.count()).select_from(statement.order_by(None).subquery())
    total = session.scalar(count_statement) or 0

    rows = session.execute(statement.limit(page_size).offset((page - 1) * page_size))
    items = list(rows.scalars().unique())
    return PageResult(items=items, page=page, page_size=page_size, total=total)
