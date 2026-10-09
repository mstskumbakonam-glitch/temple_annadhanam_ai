"""One camera's worker: capture, AI processing, health events.

Two threads per camera, sharing nothing except a bounded latest-frame buffer:

    capture thread     RTSP -> LatestFrameBuffer      (continuous, reconnects)
    processing thread  buffer -> pipeline -> records  (paced to AI_PROCESS_FPS)

Capture never waits for inference, so the RTSP socket is always drained and no
stale backlog builds up. Inference never waits for the network, and never runs
faster than the configured rate no matter how fast the camera delivers frames.

Health events are TRANSITION based: a camera that stays down for an hour produces
one OFFLINE event, not one per retry.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from app.camera.frame_buffer import LatestFrameBuffer
from app.camera.reconnect import ReconnectPolicy
from app.camera.rtsp import CameraConfig, FrameSourceFactory
from app.camera.state import CameraRuntime, Connection
from app.models.enums import CameraEventType, CameraStatus
from app.services.runtime_events import CameraEventRecord, CameraStatusRecord, EventSink
from app.utils.logs import log_event
from app.utils.rate import RateMeter
from app.utils.time import utc_now

if TYPE_CHECKING:
    from app.ai.pipeline import DetectionPipeline
    from app.config import Settings

logger = logging.getLogger("temple_annadhanam.camera")

PipelineFactory = Callable[[CameraConfig], "DetectionPipeline"]


@dataclass(frozen=True)
class WorkerSettings:
    process_fps: float = 5.0
    connect_timeout: float = 10.0
    read_timeout: float = 10.0
    reconnect_delay: float = 2.0
    max_reconnect_delay: float = 60.0
    reconnect_jitter: float = 0.1
    max_capture_fps: float = 0.0        # 0 = accept every frame
    heartbeat_seconds: float = 30.0
    stats_log_seconds: float = 10.0
    join_timeout: float = 10.0
    poll_interval: float = 0.02         # sleep after a failed read / while waiting for frames

    @classmethod
    def from_settings(cls, settings: "Settings") -> "WorkerSettings":
        return cls(
            process_fps=settings.ai_process_fps,
            connect_timeout=settings.camera_connect_timeout,
            read_timeout=settings.camera_read_timeout,
            reconnect_delay=settings.camera_reconnect_delay,
            max_reconnect_delay=settings.camera_max_reconnect_delay,
            max_capture_fps=settings.camera_max_capture_fps,
            heartbeat_seconds=settings.camera_heartbeat_seconds,
        )


class ProcessingPacer:
    """Guarantees a minimum spacing between inference starts, so inference can never
    run faster than the configured rate.

    The camera may deliver 25 FPS; AI is asked to run at, say, 5. The next frame is
    always due one full interval after the previous one STARTED. There is no
    catch-up: after a stall the pacer does not try to make up lost frames with a
    burst. The price is a rate a hair under the target (loop latency), which is the
    right side to err on.
    """

    def __init__(self, fps: float, clock: Callable[[], float] = time.monotonic) -> None:
        if fps <= 0:
            raise ValueError("fps must be positive")
        self.interval = 1.0 / fps
        self._clock = clock
        self._next_due = clock()

    def time_until_due(self) -> float:
        return max(0.0, self._next_due - self._clock())

    def mark_started(self) -> None:
        """Call when a frame starts processing."""
        self._next_due = self._clock() + self.interval


class _Link(StrEnum):
    INITIAL = "initial"    # never connected yet
    ONLINE = "online"
    OFFLINE = "offline"    # was online, lost the stream
    ERROR = "error"        # could not connect / unexpected failure


class CameraWorker:
    """Owns one camera end to end. Every worker has its own buffer, pipeline,
    tracker and runtime state, so cameras cannot affect each other."""

    def __init__(
        self,
        config: CameraConfig,
        settings: WorkerSettings,
        *,
        pipeline_factory: PipelineFactory,
        source_factory: FrameSourceFactory,
        sink: EventSink,
        runtime: CameraRuntime,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], Any] = utc_now,
    ) -> None:
        self.config = config
        self.settings = settings
        self.runtime = runtime
        self.buffer = LatestFrameBuffer(config.camera_id)

        self._pipeline_factory = pipeline_factory
        self._source_factory = source_factory
        self._sink = sink
        self._clock = clock
        self._wall = wall_clock

        self._stop = threading.Event()
        self._reset_tracking = threading.Event()
        self._link = _Link.INITIAL
        self._pipeline: "DetectionPipeline | None" = None
        self._capture_thread: threading.Thread | None = None
        self._processing_thread: threading.Thread | None = None
        self._started = False
        self._finalized = False
        self._reconnects = 0
        self._frames_captured = 0

    # ------------------------------------------------------------- lifecycle
    @property
    def camera_id(self) -> str:
        return self.config.camera_id

    def start(self) -> None:
        """Build the pipeline and start both threads. Raises if the pipeline cannot
        be built (e.g. a configuration error); the caller isolates that failure."""
        if self._started:
            raise RuntimeError(f"worker for {self.camera_id} already started")
        self._pipeline = self._pipeline_factory(self.config)
        self._started = True

        self.runtime.update(
            started_at=self._wall(),
            model_loaded=True,
            ai_running=True,
            connected=False,
            connection_state=Connection.CONNECTING,
            tracker_session=self._pipeline.tracker_session,
            last_error=None,
        )
        # Until the stream connects, the camera is not online for our purposes.
        self._sink.submit(CameraStatusRecord(self.config.db_id, CameraStatus.OFFLINE))
        self._emit(CameraEventType.AI_STARTED, "AI pipeline started")
        log_event(logger, logging.INFO, "AI", self.camera_id, "started",
                  process_fps=self.settings.process_fps)

        self._capture_thread = threading.Thread(
            target=self._guarded(self._capture_loop, "capture"),
            name=f"cam-{self.camera_id}-capture", daemon=True)
        self._processing_thread = threading.Thread(
            target=self._guarded(self._processing_loop, "processing"),
            name=f"cam-{self.camera_id}-ai", daemon=True)
        self._capture_thread.start()
        self._processing_thread.start()

    def request_stop(self) -> None:
        """Signal the threads to stop without waiting (lets a manager stop many
        workers in parallel: signal them all, then stop() each)."""
        self._stop.set()

    def stop(self) -> None:
        """Stop both threads and record AI_STOPPED. Safe to call more than once."""
        if not self._started or self._finalized:
            return
        self._finalized = True
        self._stop.set()
        for thread in (self._capture_thread, self._processing_thread):
            if thread is not None:
                thread.join(self.settings.join_timeout)
                if thread.is_alive():
                    log_event(logger, logging.WARNING, "CAMERA", self.camera_id,
                              "thread_did_not_stop", thread=thread.name)

        self._close_open_tracks()
        self._emit(CameraEventType.AI_STOPPED, "AI pipeline stopped")
        self._sink.submit(CameraStatusRecord(self.config.db_id, CameraStatus.OFFLINE))
        self.runtime.update(
            ai_running=False, connected=False, connection_state=Connection.STOPPED,
            person_count=None, active_tracks=None, processing_fps=None, capture_fps=None,
        )
        self.runtime.set_tracks(())
        log_event(logger, logging.INFO, "AI", self.camera_id, "stopped")

    def _close_open_tracks(self) -> None:
        """Give every track that already has a DETECTED row its LOST row.

        Only safe once the processing thread has stopped (the pipeline is single-threaded).
        """
        thread = self._processing_thread
        if self._pipeline is None or (thread is not None and thread.is_alive()):
            return
        try:
            for record in self._pipeline.reset_tracking(self._wall()).events:
                self._sink.submit(record)
        except Exception as exc:
            log_event(logger, logging.WARNING, "AI", self.camera_id, "close_tracks_failed",
                      error=type(exc).__name__)

    @property
    def is_alive(self) -> bool:
        """True while both threads are running (used by the manager's supervisor)."""
        return bool(
            self._started
            and self._capture_thread and self._capture_thread.is_alive()
            and self._processing_thread and self._processing_thread.is_alive()
        )

    @property
    def stopping(self) -> bool:
        return self._stop.is_set()

    # ----------------------------------------------------------------- helpers
    def _guarded(self, target: Callable[[], None], name: str) -> Callable[[], None]:
        """A crash in one thread is logged and surfaced; it never escapes."""
        def run() -> None:
            try:
                target()
            except Exception as exc:
                logger.exception("[CAMERA] camera=%s %s thread crashed", self.camera_id, name)
                self.runtime.update(last_error=f"{name} thread crashed: {type(exc).__name__}")
        return run

    def _emit(self, event_type: CameraEventType, message: str, **metadata: Any) -> None:
        self._sink.submit(
            CameraEventRecord(
                camera_db_id=self.config.db_id,
                event_type=event_type,
                event_time=self._wall(),
                message=message,
                fps=self.runtime.snapshot().capture_fps,
                metadata=metadata or None,
            )
        )

    # ============================================================== capture ==
    def _capture_loop(self) -> None:
        policy = ReconnectPolicy(
            self.settings.reconnect_delay, self.settings.max_reconnect_delay,
            jitter=self.settings.reconnect_jitter,
        )
        meter = RateMeter(window=30, stale_after=self.settings.read_timeout, clock=self._clock)
        accept_gap = 1.0 / self.settings.max_capture_fps if self.settings.max_capture_fps > 0 else 0.0
        source = None
        last_good = last_put = 0.0
        attempts = 0

        try:
            while not self._stop.is_set():
                if source is None:
                    source, attempts = self._open(policy, attempts)
                    if source is not None:
                        last_good = self._clock()
                    continue

                try:
                    ok, frame = source.read()
                except Exception as exc:      # a decoder fault is a failed read, not a crash
                    ok, frame = False, None
                    self.runtime.update(last_error=f"read: {type(exc).__name__}")

                now = self._clock()
                if ok and frame is not None and getattr(frame, "size", 1) > 0:
                    last_good = now
                    if accept_gap and now - last_put < accept_gap:
                        continue
                    last_put = now
                    stamp = self._wall()
                    self.buffer.put(frame, stamp)
                    meter.tick()
                    self._frames_captured += 1
                    self.runtime.update(
                        last_frame_timestamp=stamp,
                        capture_fps=meter.rate(),
                        frames_captured=self._frames_captured,
                        frames_dropped=self.buffer.dropped,
                    )
                    continue

                if now - last_good >= self.settings.read_timeout:
                    self._stream_lost("read_timeout")
                    self._release(source)
                    source = None
                    self._stop.wait(policy.next_delay())
                else:
                    self._stop.wait(self.settings.poll_interval)
        finally:
            self._release(source)

    def _open(self, policy: ReconnectPolicy, attempts: int):
        """One connection attempt. Returns (source or None, attempt counter)."""
        reason = "open_failed"
        source = None
        try:
            source = self._source_factory(self.config.rtsp_url)
            opened = source.open(self.settings.connect_timeout)
        except Exception as exc:
            opened, reason = False, f"exception:{type(exc).__name__}"

        if opened:
            policy.reset()
            self._connected()
            return source, 0

        self._release(source)
        attempts += 1
        self._open_failed(reason, attempts)
        self._stop.wait(policy.next_delay())
        return None, attempts

    @staticmethod
    def _release(source: Any) -> None:
        if source is not None:
            try:
                source.release()
            except Exception:
                pass

    # ------------------------------------------------- connection transitions
    def _connected(self) -> None:
        prior, self._link = self._link, _Link.ONLINE
        recovered = prior is not _Link.INITIAL
        if recovered:
            self._reconnects += 1
        self._emit(
            CameraEventType.RECOVERED if recovered else CameraEventType.ONLINE,
            "stream recovered" if recovered else "stream connected",
        )
        self._sink.submit(CameraStatusRecord(self.config.db_id, CameraStatus.ONLINE))
        self.runtime.update(
            connected=True, connection_state=Connection.ONLINE,
            reconnect_count=self._reconnects, last_error=None,
        )
        log_event(logger, logging.INFO, "CAMERA", self.camera_id,
                  "reconnected" if recovered else "connected")

    def _stream_lost(self, reason: str) -> None:
        if self._link is not _Link.ONLINE:
            return
        self._link = _Link.OFFLINE
        self._emit(CameraEventType.OFFLINE, f"stream lost: {reason}")
        self._sink.submit(CameraStatusRecord(self.config.db_id, CameraStatus.OFFLINE))
        self.buffer.clear()              # never process frames from before the gap
        self._reset_tracking.set()       # the AI thread closes the tracker session (events)
        # Clear the visible state in the same step as the counts, so a reader never
        # sees "person_count=None" next to a list of stale tracks.
        self.runtime.set_tracks(())
        self.runtime.update(
            connected=False, connection_state=Connection.OFFLINE, last_error=reason,
            person_count=None, active_tracks=None, capture_fps=None,
        )
        log_event(logger, logging.WARNING, "CAMERA", self.camera_id, "disconnected", reason=reason)

    def _open_failed(self, reason: str, attempts: int) -> None:
        if self._link is _Link.INITIAL:
            # First ever attempt failed: that is a stream error worth recording once.
            self._link = _Link.ERROR
            self._emit(CameraEventType.ERROR, f"could not connect: {reason}")
            self._sink.submit(CameraStatusRecord(self.config.db_id, CameraStatus.ERROR))
            self.runtime.update(connection_state=Connection.ERROR, last_error=reason)
            log_event(logger, logging.WARNING, "CAMERA", self.camera_id,
                      "connection_failed", reason=reason)
        else:
            # Still down after an earlier OFFLINE/ERROR: not a new fact, so no new event.
            self.runtime.update(last_error=reason)
            log_event(logger, logging.DEBUG, "CAMERA", self.camera_id,
                      "retry_failed", attempt=attempts, reason=reason)

    # ============================================================ processing ==
    def _processing_loop(self) -> None:
        assert self._pipeline is not None
        pipeline = self._pipeline
        pacer = ProcessingPacer(self.settings.process_fps, self._clock)
        meter = RateMeter(window=10, stale_after=5.0, clock=self._clock)
        last_sequence = 0
        processed = rejected = 0
        last_detection = None
        # First DB heartbeat shortly after frames start flowing (not a full interval
        # later), so cameras.last_frame_time / fps appear promptly.
        first_heartbeat_delay = min(2.0, self.settings.heartbeat_seconds)
        next_heartbeat = self._clock() + first_heartbeat_delay
        next_stats = self._clock() + self.settings.stats_log_seconds
        max_age = self.settings.read_timeout

        while not self._stop.is_set():
            if self._reset_tracking.is_set():
                self._reset_tracking.clear()
                self._handle_reset(pipeline)
                meter.reset()
                next_heartbeat = self._clock() + first_heartbeat_delay

            wait = pacer.time_until_due()
            if wait > 0 and self._stop.wait(wait):
                break

            packet = self.buffer.get_newer_than(last_sequence)
            if packet is None:
                # Nothing new: block briefly rather than spin.
                packet = self.buffer.wait_for_frame(
                    last_sequence, timeout=min(0.2, pacer.interval))
                if packet is None:
                    continue
            if self._clock() - packet.monotonic > max_age:
                last_sequence = packet.sequence         # too old to be meaningful
                continue

            pacer.mark_started()
            meter.tick()          # rate is measured at frame START, like the cap itself
            try:
                result = pipeline.process_frame(packet)
            except Exception as exc:
                self.runtime.update(last_error=f"processing: {type(exc).__name__}")
                log_event(logger, logging.ERROR, "AI", self.camera_id,
                          "processing_error", error=type(exc).__name__)
                self._stop.wait(1.0)                    # back off; never spin on a fault
                continue

            last_sequence = packet.sequence
            processed += 1
            rejected += result.rejected_detections
            if result.confident_detections:
                last_detection = result.timestamp

            for record in result.events:
                self._sink.submit(record)
            if result.tracking is not None:
                self.runtime.set_tracks(result.tracking.tracks)
            self.runtime.update(
                person_count=result.person_count,
                active_tracks=result.active_track_count,
                # None (unknown) until there are enough samples to measure a rate
                processing_fps=meter.rate() if meter.count >= 2 else None,
                frames_processed=processed,
                rejected_detections=rejected,
                last_detection_timestamp=last_detection,
                tracker_session=pipeline.tracker_session,
            )

            now = self._clock()
            if now >= next_stats:
                next_stats = now + self.settings.stats_log_seconds
                log_event(logger, logging.INFO, "AI", self.camera_id, "stats",
                          detections=result.confident_detections,
                          tracks=result.person_count,
                          fps=meter.rate() if meter.count >= 2 else "n/a")
            if now >= next_heartbeat:
                next_heartbeat = now + self.settings.heartbeat_seconds
                self._heartbeat()

    def _handle_reset(self, pipeline: "DetectionPipeline") -> None:
        result = pipeline.reset_tracking(self._wall())
        for record in result.events:
            self._sink.submit(record)
        self.runtime.set_tracks(())
        self.runtime.update(
            person_count=None, active_tracks=None, tracker_session=pipeline.tracker_session)

    def _heartbeat(self) -> None:
        """Refresh cameras.last_frame_time / fps occasionally, not per frame."""
        snapshot = self.runtime.snapshot()
        if snapshot.connected and snapshot.last_frame_timestamp is not None:
            self._sink.submit(
                CameraStatusRecord(
                    self.config.db_id, CameraStatus.ONLINE,
                    fps=snapshot.capture_fps, last_frame_time=snapshot.last_frame_timestamp,
                )
            )
