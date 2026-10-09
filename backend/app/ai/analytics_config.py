"""Per-camera crowd-analytics configuration (lines, zones, thresholds).

Stored as JSON in `cameras.analytics_config` and validated here, so the API, the
database and the running pipeline all agree on one shape.

All coordinates are NORMALISED to the frame: x and y in [0, 1], origin top-left.
A configuration therefore keeps working when a camera's resolution changes; it
must be redrawn only when the camera is physically moved or re-aimed.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

AreaCode = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=32,
                      pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$"),
]

Point = tuple[Annotated[float, Field(ge=0.0, le=1.0)], Annotated[float, Field(ge=0.0, le=1.0)]]


class DensityLevel(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"

    @property
    def rank(self) -> int:
        return _RANK[self]


_RANK = {DensityLevel.LOW: 0, DensityLevel.MEDIUM: 1, DensityLevel.HIGH: 2, DensityLevel.CRITICAL: 3}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CountingLine(_Strict):
    """A virtual line. A person is counted when their FEET (bottom-centre of the
    box) cross it. Walking from the left-hand side of the vector start->end to
    its right-hand side is an ENTRY; the reverse is an EXIT. Set `invert` to swap."""

    id: AreaCode
    name: str = Field(default="", max_length=64)
    start: Point
    end: Point
    invert: bool = False
    # A track must be at least this far from the line (fraction of the frame
    # diagonal) on each side for a crossing to count. Absorbs box jitter of
    # someone standing ON the line, which would otherwise count in/out/in/out.
    hysteresis: float = Field(default=0.02, ge=0.0, le=0.2)

    @model_validator(mode="after")
    def _non_degenerate(self) -> "CountingLine":
        if abs(self.start[0] - self.end[0]) + abs(self.start[1] - self.end[1]) < 0.01:
            raise ValueError(f"line '{self.id}' is too short")
        return self


class DensityThresholds(_Strict):
    """Person-count thresholds for one zone: count >= medium -> MEDIUM, and so on.

    If the zone's real floor area is known (`area_m2`), use `per_square_metre`
    and give persons-per-m^2 thresholds instead (crowd-safety guidance usually
    talks about people per m^2)."""

    medium: float = Field(default=10, gt=0)
    high: float = Field(default=20, gt=0)
    critical: float = Field(default=30, gt=0)
    per_square_metre: bool = False

    @model_validator(mode="after")
    def _ordered(self) -> "DensityThresholds":
        if not self.medium < self.high < self.critical:
            raise ValueError("thresholds must satisfy medium < high < critical")
        return self


class Zone(_Strict):
    id: AreaCode
    name: str = Field(default="", max_length=64)
    kind: Literal["area", "queue"] = "area"
    polygon: list[Point] = Field(min_length=3, max_length=64)
    area_m2: float | None = Field(default=None, gt=0, description="Real floor area, if surveyed.")
    thresholds: DensityThresholds = Field(default_factory=DensityThresholds)
    # Queue zones only: raise a congestion alert at this many people.
    queue_alert_length: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _check(self) -> "Zone":
        if self.thresholds.per_square_metre and self.area_m2 is None:
            raise ValueError(f"zone '{self.id}': per_square_metre thresholds need area_m2")
        if _polygon_area(self.polygon) < 1e-4:
            raise ValueError(f"zone '{self.id}' polygon has (almost) no area")
        return self


class AlertSettings(_Strict):
    """Debouncing so one crowded moment produces ONE alert, not one per frame."""

    raise_after_seconds: float = Field(default=10.0, ge=0.0, le=3600)
    clear_after_seconds: float = Field(default=30.0, ge=0.0, le=3600)
    min_level: DensityLevel = DensityLevel.HIGH       # density level that raises an alert
    smoothing_seconds: float = Field(default=3.0, ge=0.0, le=60)  # rolling median window


class AnalyticsConfig(_Strict):
    lines: list[CountingLine] = Field(default_factory=list, max_length=16)
    zones: list[Zone] = Field(default_factory=list, max_length=16)
    alerts: AlertSettings = Field(default_factory=AlertSettings)
    # Tracks must be matched in this many frames before they are counted anywhere,
    # which filters one-frame detector ghosts.
    min_track_hits: int = Field(default=3, ge=1, le=100)
    # Ignore the same track crossing the same line in the same direction again
    # within this many seconds (re-identification flicker at the line).
    recount_cooldown_seconds: float = Field(default=5.0, ge=0.0, le=600)

    @field_validator("lines", "zones")
    @classmethod
    def _unique_ids(cls, items: list) -> list:
        ids = [i.id for i in items]
        if len(ids) != len(set(ids)):
            raise ValueError("ids must be unique")
        return items

    @property
    def is_empty(self) -> bool:
        return not self.lines and not self.zones


def _polygon_area(points: list[Point]) -> float:
    total = 0.0
    for (x1, y1), (x2, y2) in zip(points, points[1:] + points[:1]):
        total += x1 * y2 - x2 * y1
    return abs(total) / 2.0


def parse_analytics_config(raw: dict | None) -> AnalyticsConfig:
    """Validated config; an absent value means 'no analytics', not an error."""
    if not raw:
        return AnalyticsConfig()
    return AnalyticsConfig.model_validate(raw)
