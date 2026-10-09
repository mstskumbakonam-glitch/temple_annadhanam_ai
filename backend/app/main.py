"""FastAPI application entry point.

Phase 4: REST API over the Phase 3 models.
The AI pipeline, camera workers and WebSocket arrive in later phases.
"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import ALL_ROUTERS, register_exception_handlers
from app.config import get_settings
from app.database import SessionLocal, check_database_connection
from app.security import install_http_guards
from app.utils.logs import install_log_redaction

settings = get_settings()
logging.basicConfig(level=settings.log_level.upper())
# Scrub credentials from every log record in the process (RTSP passwords etc.).
install_log_redaction()
logger = logging.getLogger("temple_annadhanam")

DESCRIPTION = """
Management API for a temple annadhanam (community meal) hall.

**Privacy by design**

* Visitors are anonymous: they are counted and tracked with a temporary tracking
  ID and a display code such as `VIS-000001`, and are never identified by face.
* Face recognition applies only to registered staff. `face_embedding` is not
  exposed by any endpoint.
* RTSP credentials are stored but only ever returned in masked form.
"""

TAGS_METADATA = [
    {"name": "Dashboard", "description": "Aggregated counters and hall occupancy."},
    {"name": "Cameras", "description": "CCTV camera configuration and status."},
    {"name": "Visitors", "description": "Anonymous visitor sessions (read-only)."},
    {"name": "Staff", "description": "Registered staff records."},
    {"name": "Attendance", "description": "Staff presence sessions."},
    {"name": "Seats", "description": "Seat configuration, live status and history."},
    {"name": "Events", "description": "Visitor and camera event history (read-only)."},
    {"name": "Crowd Analytics", "description": "Entry/exit lines, zone density, queues, alerts, history."},
    {"name": "AI Runtime", "description": "Live AI pipeline and camera worker status (read-only)."},
    {"name": "system", "description": "Health checks."},
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting %s v%s (%s)", settings.app_name, settings.app_version, settings.app_env)
    logger.info("Database: %s", settings.safe_database_url)
    if not settings.auth_enabled:
        logger.warning(
            "API authentication is OFF (no API_VIEWER_KEYS / API_ADMIN_KEYS). "
            "Acceptable on a development machine only; never expose this API."
        )
    if settings.demo_mode:
        logger.warning("DEMO_MODE is on: demo:// cameras play RECORDED video (%s)",
                       settings.demo_video_path.name)

    result = check_database_connection()
    if result["status"] == "ok":
        logger.info("PostgreSQL %s reachable (database=%s)",
                    result["server_version"], result["database_name"])
    else:
        logger.error("PostgreSQL not reachable at startup: %s", result["detail"])

    manager = None
    if settings.ai_autostart:
        # Imported lazily so a deployment that never enables AI never imports
        # torch/ultralytics. start() returns at once; the supervisor thread loads
        # the model and starts the camera workers in the background.
        from app.camera.manager import CameraManager

        manager = CameraManager(settings, session_factory=SessionLocal)
        manager.start()
        logger.info("AI pipeline starting in the background (AI_AUTOSTART=true)")
    else:
        logger.info("AI pipeline not started (AI_AUTOSTART=false)")
    app.state.ai_manager = manager

    yield

    if manager is not None:
        await asyncio.to_thread(manager.stop)
    logger.info("Shutting down %s", settings.app_name)


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=DESCRIPTION,
    openapi_tags=TAGS_METADATA,
    lifespan=lifespan,
    docs_url="/docs" if settings.docs_visible else None,
    redoc_url="/redoc" if settings.docs_visible else None,
    openapi_url="/openapi.json" if settings.docs_visible else None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    # Auth uses bearer keys, never cookies, so credentialed CORS is not needed.
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-API-Key"],
)
install_http_guards(app, settings)

register_exception_handlers(app)

for router in ALL_ROUTERS:
    app.include_router(router)
