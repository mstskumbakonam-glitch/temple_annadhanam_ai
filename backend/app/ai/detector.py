"""YOLO person detector.

Turns a frame into validated `Detection` objects. It knows nothing about
tracking, cameras' connection state or the database.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import numpy as np

from app.ai.model_loader import ModelConfigurationError, ModelHandle
from app.ai.types import Detection, MalformedDetectionError
from app.utils.time import utc_now

logger = logging.getLogger("temple_annadhanam.ai")


@dataclass(frozen=True)
class DetectorSettings:
    """Inference parameters. Values come from configuration, never from code."""

    confidence_floor: float          # lowest score returned (ByteTrack needs low scores too)
    iou: float
    image_size: int
    target_classes: tuple[str, ...]  # class names; empty = every class the model knows


def _to_numpy(value: Any) -> np.ndarray:
    """Accept torch tensors and plain arrays alike."""
    if hasattr(value, "cpu"):
        value = value.cpu()
    if hasattr(value, "numpy"):
        value = value.numpy()
    return np.asarray(value)


def is_usable_frame(frame: Any) -> bool:
    return (
        isinstance(frame, np.ndarray)
        and frame.size > 0
        and frame.ndim in (2, 3)
        and frame.shape[0] > 1
        and frame.shape[1] > 1
    )


class YoloDetector:
    """Runs the shared model on one camera's frames.

    One detector exists per camera; the model behind it is shared and locked.
    """

    def __init__(self, handle: ModelHandle, settings: DetectorSettings) -> None:
        self._handle = handle
        self._settings = settings
        self._class_ids = self._resolve_class_ids(handle.names, settings.target_classes)

        self.frames_total = 0
        self.empty_frames = 0
        self.rejected_total = 0
        self.last_inference_seconds = 0.0

    @staticmethod
    def _resolve_class_ids(
        names: dict[int, str], targets: tuple[str, ...]
    ) -> list[int] | None:
        if not targets:
            return None
        by_name = {name.lower(): class_id for class_id, name in names.items()}
        missing = [t for t in targets if t.lower() not in by_name]
        if missing:
            raise ModelConfigurationError(
                f"The model has no class named {missing}. Available classes include "
                f"{sorted(by_name)[:10]}... Check AI_TARGET_CLASSES."
            )
        return sorted(by_name[t.lower()] for t in targets)

    @property
    def target_class_ids(self) -> list[int] | None:
        return None if self._class_ids is None else list(self._class_ids)

    def detect(self, frame: Any, frame_timestamp: datetime | None = None) -> list[Detection]:
        """Detect target objects in `frame`. Never raises for bad *data*.

        An unusable frame yields an empty list; malformed boxes are dropped and
        counted in `rejected_total`. Inference errors (a genuine fault) propagate
        so the worker can log and back off.
        """
        self.frames_total += 1
        timestamp = frame_timestamp or utc_now()

        if not is_usable_frame(frame):
            self.empty_frames += 1
            return []

        started = time.monotonic()
        results = self._handle.predict(
            frame,
            conf=self._settings.confidence_floor,
            iou=self._settings.iou,
            imgsz=self._settings.image_size,
            classes=self._class_ids,
        )
        self.last_inference_seconds = time.monotonic() - started
        return self._parse(results, frame.shape, timestamp)

    # ---------------------------------------------------------------- parsing
    def _parse(self, results: Any, frame_shape: tuple[int, ...], timestamp: datetime) -> list[Detection]:
        if not results:
            return []
        boxes = getattr(results[0], "boxes", None)
        if boxes is None:
            return []

        try:
            xyxy = _to_numpy(boxes.xyxy)
            conf = _to_numpy(boxes.conf).reshape(-1)
            cls = _to_numpy(boxes.cls).reshape(-1)
        except Exception:
            logger.warning("[AI] unreadable detection output; frame skipped")
            self.rejected_total += 1
            return []

        if xyxy.size == 0:
            return []
        if xyxy.ndim != 2 or xyxy.shape[1] != 4 or not (len(xyxy) == len(conf) == len(cls)):
            # Inconsistent shapes: none of the rows can be trusted.
            self.rejected_total += max(len(xyxy), 1)
            return []

        allowed = None if self._class_ids is None else set(self._class_ids)
        detections: list[Detection] = []
        for box, score, class_value in zip(xyxy, conf, cls):
            try:
                class_id = int(class_value)
                if allowed is not None and class_id not in allowed:
                    continue
                detections.append(
                    Detection.from_xyxy(
                        box,
                        float(score),
                        class_id,
                        self._handle.names.get(class_id, str(class_id)),
                        timestamp,
                        frame_shape,
                    )
                )
            except (MalformedDetectionError, ValueError, OverflowError):
                self.rejected_total += 1

        detections.sort(key=lambda d: d.confidence, reverse=True)
        return detections
