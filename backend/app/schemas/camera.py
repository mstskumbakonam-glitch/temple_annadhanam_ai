"""Camera request/response schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import CameraStatus
from app.schemas.common import CameraCode, NonEmptyName


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
        if value is None or not value.strip():
            return None
        url = value.strip()
        if not url.lower().startswith(("rtsp://", "rtsps://", "http://", "https://")):
            raise ValueError("rtsp_url must start with rtsp://, rtsps://, http:// or https://")
        return url


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
