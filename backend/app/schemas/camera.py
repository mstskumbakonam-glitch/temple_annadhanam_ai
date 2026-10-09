"""Camera request/response schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import CameraStatus
from app.schemas.common import CameraCode, NonEmptyName


ALLOWED_SCHEMES = ("rtsp://", "rtsps://", "http://", "https://")


def validate_stream_url(value: str | None) -> str | None:
    """Accept camera stream URLs and demo://<file> recordings, nothing else.

    file://, local paths and other OpenCV/FFmpeg protocols are refused: a camera
    URL must never become a way to make the server read arbitrary local files.
    """
    if value is None or not value.strip():
        return None
    url = value.strip()
    from app.camera.video_file import DemoSourceError, demo_file_name, is_demo_url

    if is_demo_url(url):
        try:
            demo_file_name(url)
        except DemoSourceError as exc:
            raise ValueError(str(exc)) from exc
        return url
    if not url.lower().startswith(ALLOWED_SCHEMES):
        raise ValueError(
            "rtsp_url must start with rtsp://, rtsps://, http://, https:// "
            "(or demo://<file> for a recorded demo video)"
        )
    if any(ch in url for ch in ("\n", "\r", " ")):
        raise ValueError("rtsp_url must not contain spaces or line breaks")
    return url


class CameraBase(BaseModel):
    camera_name: NonEmptyName = Field(description="Display name, e.g. 'Main entrance'.")
    location: str | None = Field(default=None, max_length=255)
    enabled: bool = True


class CameraCreate(CameraBase):
    camera_id: CameraCode = Field(description="Unique code, e.g. ANN-ENT-01.")
    rtsp_url: str | None = Field(
        default=None,
        max_length=512,
        description="RTSP stream URL. Credentials are accepted but never returned.",
    )

    @field_validator("rtsp_url")
    @classmethod
    def _check_scheme(cls, value: str | None) -> str | None:
        return validate_stream_url(value)


class CameraUpdate(BaseModel):
    """Partial update. Only supplied fields are changed."""

    camera_name: NonEmptyName | None = None
    location: str | None = Field(default=None, max_length=255)
    rtsp_url: str | None = Field(default=None, max_length=512)
    enabled: bool | None = None
    status: CameraStatus | None = Field(
        default=None,
        description="Set manually in this phase; the camera worker owns it from Phase 5.",
    )
    fps: float | None = Field(default=None, ge=0)

    @field_validator("rtsp_url")
    @classmethod
    def _check_scheme(cls, value: str | None) -> str | None:
        # Previously unvalidated on update: file:///etc/... could be stored here.
        return validate_stream_url(value)


class CameraRead(CameraBase):
    """Camera as returned by the API.

    rtsp_url is exposed only in masked form, so stream passwords never leave
    the database.
    """

    model_config = ConfigDict(from_attributes=True)

    camera_id: CameraCode
    status: CameraStatus
    fps: float | None = None
    last_frame_time: datetime | None = None
    rtsp_url_masked: str | None = Field(
        default=None, description="RTSP URL with the password replaced by '***'."
    )
    source_kind: str = Field(
        default="live", description="'recorded' for demo:// videos, otherwise 'live'."
    )
    has_analytics: bool = Field(default=False, description="Lines or zones are configured.")
    created_at: datetime
    updated_at: datetime


class CameraStatusRead(BaseModel):
    """Compact status row for the dashboard camera grid."""

    model_config = ConfigDict(from_attributes=True)

    camera_id: CameraCode
    camera_name: str
    status: CameraStatus
    enabled: bool
    fps: float | None = None
    last_frame_time: datetime | None = None
    source_kind: str = "live"
