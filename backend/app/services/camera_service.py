"""Camera business logic."""

from __future__ import annotations

from sqlalchemy import Select, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Camera
from app.models.enums import CameraStatus
from app.schemas.camera import CameraCreate, CameraUpdate
from app.services.exceptions import ConflictError, NotFoundError
from app.services.pagination import PageResult, paginate


def _base_query() -> Select:
    return select(Camera).order_by(Camera.camera_id)


def get_by_code(session: Session, camera_id: str) -> Camera:
    """Fetch one camera by its display code, or raise NotFoundError."""
    camera = session.scalar(select(Camera).where(Camera.camera_id == camera_id))
    if camera is None:
        raise NotFoundError(f"Camera '{camera_id}' not found.")
    return camera


def list_cameras(
    session: Session,
    *,
    page: int,
    page_size: int,
    status: str | None = None,
    enabled: bool | None = None,
) -> PageResult:
    statement = _base_query()
    if status is not None:
        statement = statement.where(Camera.status == status)
    if enabled is not None:
        statement = statement.where(Camera.enabled.is_(enabled))
    return paginate(session, statement, page, page_size)


def list_status(session: Session) -> list[Camera]:
    """All cameras for the dashboard grid. Bounded by the number of installed cameras."""
    return list(session.scalars(_base_query()))


def create_camera(session: Session, payload: CameraCreate) -> Camera:
    camera = Camera(
        camera_id=payload.camera_id,
        camera_name=payload.camera_name,
        location=payload.location,
        rtsp_url=payload.rtsp_url,
        enabled=payload.enabled,
        # A camera starts DISABLED when it is created switched off, otherwise
        # OFFLINE until a capture worker reports otherwise in Phase 5.
        status=CameraStatus.OFFLINE if payload.enabled else CameraStatus.DISABLED,
    )
    session.add(camera)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ConflictError(f"Camera '{payload.camera_id}' already exists.") from exc
    session.refresh(camera)
    return camera


def update_camera(session: Session, camera_id: str, payload: CameraUpdate) -> Camera:
    camera = get_by_code(session, camera_id)
    changes = payload.model_dump(exclude_unset=True)

    for field, value in changes.items():
        setattr(camera, field, value)

    # Keep status consistent when a camera is switched off or back on, unless the
    # caller set status explicitly in the same request.
    if "enabled" in changes and "status" not in changes:
        if changes["enabled"] is False:
            camera.status = CameraStatus.DISABLED
        elif camera.status == CameraStatus.DISABLED:
            camera.status = CameraStatus.OFFLINE

    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ConflictError(f"Camera '{camera_id}' could not be updated.") from exc
    session.refresh(camera)
    return camera


def delete_camera(session: Session, camera_id: str) -> None:
    camera = get_by_code(session, camera_id)
    session.delete(camera)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ConflictError(
            f"Camera '{camera_id}' is still referenced by historical records "
            "and cannot be deleted. Disable it instead."
        ) from exc
