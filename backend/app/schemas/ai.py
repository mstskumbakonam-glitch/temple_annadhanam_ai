"""AI runtime status schemas.

These describe live state only. They never carry an RTSP URL, a credential, a
face embedding, or any identity.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.camera.state import Connection, ManagerState
from app.schemas.common import CameraCode


class CameraAIStatus(BaseModel):
    camera_id: CameraCode
    camera_name: str | None = None
    connected: bool = Field(description="True while the RTSP stream is delivering frames.")
    ai_running: bool = Field(description="True while this camera's AI pipeline is running.")
    connection_state: Connection
    model_loaded: bool
    person_count: int | None = Field(
        default=None,
        description="Confirmed people visible in the last processed frame. "
        "Null when AI is not running or the camera is not connected (unknown, not zero).",
    )
    active_tracks: int | None = Field(
        default=None,
        description="Visible tracks plus tracks briefly lost but still alive.",
    )
    processing_fps: float | None = Field(default=None, description="Measured AI frames per second.")
    capture_fps: float | None = Field(default=None, description="Measured capture frames per second.")
    last_frame_timestamp: datetime | None = None
    last_detection_timestamp: datetime | None = None
    frames_processed: int = 0
    reconnect_count: int = 0
    rejected_detections: int = 0
    last_error: str | None = Field(
        default=None, description="Short, sanitised reason. Never contains a URL or credential."
    )
    tracker_session: str | None = Field(
        default=None,
        description="Track ids are only meaningful within one (camera, tracker_session).",
    )
    started_at: datetime | None = None


class AIRuntimeStatus(BaseModel):
    autostart_enabled: bool = Field(description="AI_AUTOSTART: whether the API starts the pipeline.")
    state: ManagerState
    detail: str | None = None
    model_loaded: bool
    model_name: str | None = Field(default=None, description="Model file name only.")
    device: str | None = None
    process_fps: float = Field(description="Configured AI frames per second per camera.")
    cameras_total: int
    cameras_running: int
    cameras: list[CameraAIStatus]
