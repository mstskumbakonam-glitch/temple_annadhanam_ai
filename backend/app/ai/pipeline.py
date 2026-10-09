"""The detection pipeline for ONE camera.

    FramePacket -> YoloDetector -> ByteTrackTracker -> processors -> records

Each layer is a separate object with a narrow interface, so it can be tested,
replaced or extended alone. The pipeline itself only wires them together; it
knows nothing about RTSP, threads, or the database.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime
from typing import Any

from app.ai.analytics import AnalyticsSnapshot, CrowdAnalyticsProcessor
from app.ai.analytics_config import AnalyticsConfig
from app.ai.detector import YoloDetector, is_usable_frame
from app.ai.pipeline_types import PipelineResult
from app.ai.processor import FrameProcessor, TrackLifecycleProcessor
from app.ai.tracker import ByteTrackTracker
from app.camera.frame_buffer import FramePacket

logger = logging.getLogger("temple_annadhanam.ai")


class DetectionPipeline:
    """Owns one camera's detector, tracker and processors.

    Not thread-safe: it is driven by that camera's processing thread only, which
    is what keeps tracker state private to the camera.
    """

    def __init__(
        self,
        camera_id: str,
        detector: YoloDetector,
        tracker: ByteTrackTracker,
        processors: Sequence[FrameProcessor] = (),
        *,
        high_confidence: float = 0.4,
    ) -> None:
        self.camera_id = camera_id
        self.detector = detector
        self.tracker = tracker
        self._processors = list(processors)
        self._high_confidence = high_confidence

    @property
    def analytics(self) -> CrowdAnalyticsProcessor | None:
        """The crowd-analytics processor, if this pipeline has one."""
        for processor in self._processors:
            if isinstance(processor, CrowdAnalyticsProcessor):
                return processor
        return None

    def analytics_snapshot(self) -> AnalyticsSnapshot | None:
        analytics = self.analytics
        return analytics.snapshot() if analytics else None

    def add_processor(self, processor: FrameProcessor) -> None:
        """Register another processor (Phase 6 extension point)."""
        self._processors.append(processor)

    @property
    def tracker_session(self) -> str:
        return self.tracker.session_id

    # ----------------------------------------------------------------- frame
    def process_frame(self, packet: FramePacket) -> PipelineResult:
        """Run one frame through detection, tracking and the processors."""
        if not is_usable_frame(packet.frame):
            # Nothing to detect and nothing to age: skip the models entirely.
            return PipelineResult(
                camera_id=self.camera_id,
                frame_sequence=packet.sequence,
                timestamp=packet.timestamp,
                usable_frame=False,
                detections=(),
                confident_detections=0,
                tracking=None,
            )

        rejected_before = self.detector.rejected_total
        detections = self.detector.detect(packet.frame, packet.timestamp)

        # The tracker is updated on EVERY usable frame, including empty ones, so
        # tracks of people who have left age out and expire.
        tracking = self.tracker.update(detections, packet.timestamp)

        result = PipelineResult(
            camera_id=self.camera_id,
            frame_sequence=packet.sequence,
            timestamp=packet.timestamp,
            usable_frame=True,
            detections=tuple(detections),
            confident_detections=sum(
                1 for d in detections if d.confidence >= self._high_confidence
            ),
            tracking=tracking,
            inference_seconds=self.detector.last_inference_seconds,
            rejected_detections=self.detector.rejected_total - rejected_before,
            frame_shape=(int(packet.frame.shape[0]), int(packet.frame.shape[1])),
        )
        return self._with_events(result)

    def reset_tracking(self, timestamp: datetime) -> PipelineResult:
        """Close the current tracker session (e.g. after a disconnect).

        Every live track expires, so processors can close their state, and a new
        session with a fresh ID space begins.
        """
        tracking = self.tracker.reset(timestamp)
        result = PipelineResult(
            camera_id=self.camera_id,
            frame_sequence=0,
            timestamp=timestamp,
            usable_frame=False,
            detections=(),
            confident_detections=0,
            tracking=tracking,
        )
        return self._with_events(result)

    # --------------------------------------------------------------- helpers
    def _with_events(self, result: PipelineResult) -> PipelineResult:
        events: list[Any] = []
        for processor in self._processors:
            try:
                events.extend(processor.process(result))
            except Exception:
                # One faulty processor must not stop detection or the others.
                logger.exception(
                    "[AI] camera=%s processor %s failed", self.camera_id,
                    type(processor).__name__,
                )
        if not events:
            return result
        return replace(result, events=tuple(events))


def build_pipeline(
    *,
    camera_id: str,
    camera_db_id: int,
    handle: Any,
    detector_settings: Any,
    tracker_settings: Any,
    min_hits: int,
    persist_track_events: bool,
    analytics_config: AnalyticsConfig | None = None,
    persist_count_history: bool = True,
) -> DetectionPipeline:
    """Assemble one camera's pipeline around the shared, already-loaded model."""
    detector = YoloDetector(handle, detector_settings)
    tracker = ByteTrackTracker(camera_id, tracker_settings, handle.names)
    processors = [
        TrackLifecycleProcessor(
            camera_db_id, min_hits=min_hits, enabled=persist_track_events
        ),
        CrowdAnalyticsProcessor(
            camera_db_id, camera_id, analytics_config or AnalyticsConfig(),
            persist_snapshots=persist_count_history,
        ),
    ]
    return DetectionPipeline(
        camera_id,
        detector,
        tracker,
        processors,
        high_confidence=tracker_settings.high_confidence,
    )
