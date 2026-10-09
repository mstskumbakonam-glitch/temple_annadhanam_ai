"""RTSP capture through OpenCV.

The RTSP URL is a secret (it usually carries a username and password). It exists
in exactly two places: the `cameras` table and the argument to cv2.VideoCapture.
It is never formatted into a log line, an exception message, or an API response.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.utils.logs import mask_url_credentials

logger = logging.getLogger("temple_annadhanam.camera")


# ---------------------------------------------------------------------------
# Camera configuration as loaded from PostgreSQL
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class CameraConfig:
    """What a worker needs to know about its camera. Comes only from the DB."""

    db_id: int                                  # cameras.id
    camera_id: str                              # display code, e.g. ANN-ENT-01
    camera_name: str
    rtsp_url: str = field(repr=False, compare=True)   # secret: hidden from repr()

    @property
    def safe_url(self) -> str:
        """The URL with its password masked - the only form that may be logged."""
        return mask_url_credentials(self.rtsp_url) or ""

    def __str__(self) -> str:
        return f"CameraConfig(camera_id={self.camera_id!r}, url={self.safe_url!r})"


# ---------------------------------------------------------------------------
# Frame source abstraction (lets tests replace RTSP with a scripted stream)
# ---------------------------------------------------------------------------
class FrameSource(Protocol):
    def open(self, timeout: float) -> bool:
        """Connect. Returns True when frames can be read."""

    def read(self) -> tuple[bool, Any]:
        """Return (ok, frame). ok=False means no frame was available."""

    def release(self) -> None:
        """Free the connection. Must be safe to call more than once."""


FrameSourceFactory = Callable[[str], FrameSource]


def configure_opencv_capture(transport: str = "tcp") -> None:
    """Process-wide OpenCV/FFmpeg settings, applied before the first connection.

    * rtsp_transport: TCP avoids the packet loss UDP suffers on busy LANs.
    * FFmpeg log level: FFmpeg's native logging writes straight to stderr and can
      include the stream URL. It bypasses Python's logging (and our redaction),
      so it is silenced; the application logs connection outcomes itself.
    """
    os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", f"rtsp_transport;{transport}")
    os.environ.setdefault("OPENCV_FFMPEG_LOGLEVEL", "-8")
    os.environ.setdefault("OPENCV_LOG_LEVEL", "SILENT")


class OpenCvFrameSource:
    """cv2.VideoCapture wrapper implementing FrameSource."""

    def __init__(self, url: str) -> None:
        self._url = url
        self._capture: Any = None

    def open(self, timeout: float) -> bool:
        import cv2

        timeout_ms = int(timeout * 1000)
        try:
            self._capture = cv2.VideoCapture(
                self._url,
                cv2.CAP_FFMPEG,
                [
                    cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, timeout_ms,
                    cv2.CAP_PROP_READ_TIMEOUT_MSEC, timeout_ms,
                ],
            )
        except TypeError:  # OpenCV build without the params overload
            self._capture = cv2.VideoCapture(self._url)

        if self._capture is None or not self._capture.isOpened():
            self.release()
            return False
        try:
            self._capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # best effort: less lag
        except Exception:
            pass
        return True

    def read(self) -> tuple[bool, Any]:
        if self._capture is None:
            return False, None
        ok, frame = self._capture.read()
        return bool(ok), frame

    def release(self) -> None:
        capture, self._capture = self._capture, None
        if capture is not None:
            try:
                capture.release()
            except Exception:
                logger.debug("release() raised", exc_info=False)


def opencv_source_factory(transport: str = "tcp") -> FrameSourceFactory:
    configure_opencv_capture(transport)
    return lambda url: OpenCvFrameSource(url)
