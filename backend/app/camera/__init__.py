"""Camera capture subsystem: RTSP sources, frame buffering, workers, manager.

Camera configuration lives only in the PostgreSQL `cameras` table.
"""

from app.camera.frame_buffer import FramePacket, LatestFrameBuffer
from app.camera.reconnect import ReconnectPolicy
from app.camera.rtsp import CameraConfig, FrameSource, OpenCvFrameSource
from app.camera.state import (
    CameraRuntimeSnapshot,
    Connection,
    ManagerState,
    manager_status,
    runtime_registry,
)

__all__ = [
    "CameraConfig",
    "CameraRuntimeSnapshot",
    "Connection",
    "FramePacket",
    "FrameSource",
    "LatestFrameBuffer",
    "ManagerState",
    "OpenCvFrameSource",
    "ReconnectPolicy",
    "manager_status",
    "runtime_registry",
]
