"""Shared schema building blocks: pagination, errors and code validators."""

from __future__ import annotations

import re
from typing import Annotated, Generic, TypeVar

from pydantic import BaseModel, Field, StringConstraints

T = TypeVar("T")

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200

# --- Reusable constrained string types -------------------------------------
# Codes are uppercased and shape-checked here so invalid values are rejected
# with a 422 before they ever reach the service layer.
# Patterns accept either case because Pydantic applies `pattern` BEFORE
# `to_upper`; a case-sensitive pattern would reject 'ann-ent-01' outright
# instead of normalising it to 'ANN-ENT-01'.

CameraCode = Annotated[
    str,
    StringConstraints(strip_whitespace=True, to_upper=True, min_length=1, max_length=64,
                      pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$"),
]

StaffCode = Annotated[
    str,
    StringConstraints(strip_whitespace=True, to_upper=True, min_length=1, max_length=16,
                      pattern=r"(?i)^STAFF-\d{3,}$"),
]

VisitorCode = Annotated[
    str,
    StringConstraints(strip_whitespace=True, to_upper=True, min_length=1, max_length=16,
                      pattern=r"(?i)^VIS-\d{6,}$"),
]

SeatCode = Annotated[
    str,
    StringConstraints(strip_whitespace=True, to_upper=True, min_length=1, max_length=32,
                      pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$"),
]

HallCode = Annotated[
    str,
    StringConstraints(strip_whitespace=True, to_upper=True, min_length=1, max_length=64,
                      pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$"),
]

NonEmptyName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]


class Page(BaseModel, Generic[T]):
    """Envelope for every list endpoint."""

    items: list[T]
    page: int = Field(ge=1, examples=[1])
    page_size: int = Field(ge=1, le=MAX_PAGE_SIZE, examples=[DEFAULT_PAGE_SIZE])
    total: int = Field(ge=0, description="Total matching rows, ignoring pagination.")


class ErrorResponse(BaseModel):
    """Uniform error body. Never contains SQL or stack traces."""

    detail: str = Field(description="Human-readable description of the problem.")
    code: str | None = Field(
        default=None,
        description="Stable machine-readable error code, e.g. 'not_found' or 'conflict'.",
    )
