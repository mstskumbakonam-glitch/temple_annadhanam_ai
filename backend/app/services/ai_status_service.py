"""Assembles AI runtime status from PostgreSQL cameras plus live runtime state."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.camera.state import (
    CameraRuntimeSnapshot,
    ManagerStatusHolder,
    RuntimeRegistry,
    manager_status,
    runtime_registry,
)
from app.config import Settings
from app.models import Camera
from app.schemas.ai import AIRuntimeStatus, CameraAIStatus
from app.services import camera_service
from app.utils.logs import redact_text


def _to_status(camera: Camera, snapshot: CameraRuntimeSnapshot) -> CameraAIStatus:
    return CameraAIStatus(
        camera_id=camera.camera_id,
        camera_name=camera.camera_name,
        connected=snapshot.connected,
        ai_running=snapshot.ai_running,
        connection_state=snapshot.connection_state,
        model_loaded=snapshot.model_loaded,
        person_count=snapshot.person_count,
        active_tracks=snapshot.active_tracks,
        processing_fps=(
            round(snapshot.processing_fps, 2) if snapshot.processing_fps is not None else None
        ),
        capture_fps=round(snapshot.capture_fps, 2) if snapshot.capture_fps is not None else None,
        last_frame_timestamp=snapshot.last_frame_timestamp,
        last_detection_timestamp=snapshot.last_detection_timestamp,
        frames_processed=snapshot.frames_processed,
        reconnect_count=snapshot.reconnect_count,
        rejected_detections=snapshot.rejected_detections,
        last_error=redact_text(snapshot.last_error) if snapshot.last_error else None,
        tracker_session=snapshot.tracker_session,
        started_at=snapshot.started_at,
    )


def get_camera_ai_status(
    session: Session,
    camera_id: str,
    registry: RuntimeRegistry = runtime_registry,
) -> CameraAIStatus:
    """Runtime status for one camera. 404s (via NotFoundError) if it is not in the DB."""
    camera = camera_service.get_by_code(session, camera_id)
    return _to_status(camera, registry.snapshot(camera.camera_id))


def get_runtime_status(
    session: Session,
    settings: Settings,
    registry: RuntimeRegistry = runtime_registry,
    manager: ManagerStatusHolder = manager_status,
) -> AIRuntimeStatus:
    cameras = [
        _to_status(camera, registry.snapshot(camera.camera_id))
        for camera in camera_service.list_status(session)
    ]
    status = manager.get()
    return AIRuntimeStatus(
        autostart_enabled=settings.ai_autostart,
        state=status.state,
        detail=redact_text(status.detail) if status.detail else None,
        model_loaded=status.model_loaded,
        model_name=status.model_name,
        device=status.device,
        process_fps=settings.ai_process_fps,
        cameras_total=len(cameras),
        cameras_running=sum(1 for c in cameras if c.ai_running),
        cameras=cameras,
    )
