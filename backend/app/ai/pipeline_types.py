"""Result type returned by the detection pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.ai.types import Detection, TrackingResult


@dataclass(frozen=True)
class PipelineResult:
    camera_id: str                       # camera display code
    frame_sequence: int
    timestamp: datetime
    usable_frame: bool                   # False for empty / corrupt frames
    detections: tuple[Detection, ...]    # everything the detector returned
    confident_detections: int            # those at or above the high threshold
    tracking: TrackingResult | None      # None when the frame was unusable
    events: tuple[Any, ...] = ()         # records emitted by processors
    inference_seconds: float = 0.0
    rejected_detections: int = 0         # malformed boxes dropped this frame
    frame_shape: tuple[int, int] | None = None   # (height, width) of the processed frame

    @property
    def person_count(self) -> int:
        """Confirmed tracks visible in this frame."""
        return self.tracking.visible_count if self.tracking else 0

    @property
    def active_track_count(self) -> int:
        """Visible tracks plus temporarily lost ones."""
        return self.tracking.active_count if self.tracking else 0
