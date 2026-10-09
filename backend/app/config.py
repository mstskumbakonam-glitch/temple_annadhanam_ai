"""Application settings.

All configuration comes from environment variables / the .env file.
No secrets or passwords are hard-coded here.

PostgreSQL is the only supported database. SQLite is explicitly rejected so a
misconfigured environment fails loudly at startup instead of silently running
against the wrong engine.
"""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/.env, resolved relative to this file so it works from any working directory.
ENV_FILE = Path(__file__).resolve().parent.parent / ".env"

REQUIRED_DB_PREFIX = "postgresql+psycopg://"


def validate_postgres_url(value: str, field_name: str) -> str:
    """Accept only PostgreSQL-over-psycopg3 URLs. Reject SQLite and everything else."""
    url = value.strip()
    if not url:
        raise ValueError(f"{field_name} must not be empty.")
    if url.startswith("sqlite"):
        raise ValueError(
            f"{field_name} points at SQLite. This project is PostgreSQL only; "
            f"the URL must start with '{REQUIRED_DB_PREFIX}'."
        )
    if not url.startswith(REQUIRED_DB_PREFIX):
        raise ValueError(
            f"{field_name} must start with '{REQUIRED_DB_PREFIX}' "
            f"(PostgreSQL via psycopg 3). Got: {url.split('://', 1)[0] or url}://..."
        )
    return url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- Application ----
    app_name: str = "Temple Annadhanam AI CCTV"
    app_version: str = "0.1.0"
    app_env: str = "development"
    log_level: str = "INFO"
    cors_origins: str = "http://localhost:5173"
    # Local time zone of the temple: decides where "today" starts for daily totals.
    site_timezone: str = "Asia/Kolkata"

    # ---- Database (PostgreSQL only) ----
    database_url: str = Field(..., description=f"{REQUIRED_DB_PREFIX}user:password@host:port/db")
    test_database_url: str | None = None

    # ---- Connection pool ----
    db_pool_size: int = 5           # persistent connections kept open
    db_max_overflow: int = 10       # extra connections allowed under load
    db_pool_timeout: int = 30       # seconds to wait for a free connection
    db_pool_recycle: int = 1800     # recycle connections after 30 min
    db_echo: bool = False           # log emitted SQL (development only)
    db_connect_timeout: int = 5     # seconds before a connection attempt gives up

    # ---- AI detection / tracking ----
    # There is deliberately NO default model path: Ultralytics would otherwise
    # download weights on first use. The file must be supplied explicitly.
    # A relative path is resolved against the backend/ directory.
    ai_model_path: str | None = None
    ai_confidence: float = Field(default=0.4, ge=0.0, le=1.0)   # ByteTrack "high" threshold
    ai_track_low_confidence: float = Field(default=0.1, ge=0.0, le=1.0)
    ai_iou: float = Field(default=0.5, ge=0.0, le=1.0)          # NMS IoU
    ai_image_size: int = Field(default=640, ge=32, le=4096)
    ai_device: str = "cpu"                                       # cpu, cuda, cuda:0, ...
    ai_process_fps: float = Field(default=5.0, gt=0.0, le=60.0)  # inference rate per camera
    ai_target_classes: str = "person"                            # comma-separated class names
    ai_track_lost_seconds: float = Field(default=3.0, gt=0.0)    # keep a lost track this long
    ai_track_min_hits: int = Field(default=3, ge=1)              # matches before a track is persisted
    ai_persist_track_events: bool = True                         # write DETECTED/LOST rows
    ai_autostart: bool = False                                   # start the pipeline with the API
    ai_sync_interval: float = Field(default=30.0, gt=0.0)        # seconds between camera re-syncs

    # ---- Camera capture ----
    camera_connect_timeout: float = Field(default=10.0, gt=0.0)
    camera_read_timeout: float = Field(default=10.0, gt=0.0)     # no frame for this long => lost
    camera_reconnect_delay: float = Field(default=2.0, gt=0.0)
    camera_max_reconnect_delay: float = Field(default=60.0, gt=0.0)
    camera_max_capture_fps: float = Field(default=0.0, ge=0.0)   # 0 = accept every frame
    camera_rtsp_transport: str = "tcp"                           # tcp is more reliable than udp
    camera_heartbeat_seconds: float = Field(default=30.0, gt=0.0)  # DB last_frame_time refresh

    ai_persist_count_history: bool = True                        # per-minute count rows

    # ---- Demo mode (recorded video through the real pipeline) ----
    # Cameras with rtsp_url demo://<file> play <DEMO_VIDEO_DIR>/<file> in a loop.
    # Results are real detections on RECORDED footage and are labelled as such.
    demo_mode: bool = False
    demo_video_dir: str = "demo_videos"                          # relative to backend/

    # ---- Processed-frame preview (annotated JPEG per camera) ----
    preview_enabled: bool = False
    preview_anonymize: bool = True        # pixelate the head region of every person box
    preview_max_width: int = Field(default=960, ge=160, le=3840)

    # ---- API security ----
    # Comma-separated keys. Viewer keys may read; admin keys may also change
    # configuration. Generate with:  python -c "import secrets; print(secrets.token_urlsafe(32))"
    # With no keys set the API is OPEN, which is allowed only outside production.
    api_viewer_keys: str = ""
    api_admin_keys: str = ""
    api_operator_keys: str = ""           # hall staff: seats, sessions, attendance
    docs_enabled: bool | None = None      # default: on in development, off in production
    rate_limit_per_minute: int = Field(default=600, ge=0)        # per client, 0 = off
    rate_limit_writes_per_minute: int = Field(default=60, ge=0)  # POST/PUT/DELETE
    trusted_proxy_count: int = Field(default=0, ge=0, le=5)      # X-Forwarded-For hops to trust

    # ---- Face recognition (registered staff only) ----
    face_match_threshold: float = 0.45

    # ---- Seat occupancy ----
    seat_occupancy_confirm_frames: int = 5
    seat_empty_confirm_frames: int = 10
    seat_occupancy_timeout: int = 3

    @field_validator("database_url")
    @classmethod
    def _check_database_url(cls, value: str) -> str:
        return validate_postgres_url(value, "DATABASE_URL")

    @field_validator("test_database_url")
    @classmethod
    def _check_test_database_url(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        return validate_postgres_url(value, "TEST_DATABASE_URL")

    @field_validator("site_timezone")
    @classmethod
    def _check_timezone(cls, value: str) -> str:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(value.strip())
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(
                f"SITE_TIMEZONE '{value}' is not a known IANA time zone (e.g. Asia/Kolkata). "
                "On Windows install the 'tzdata' package."
            ) from exc
        return value.strip()

    @field_validator("ai_device")
    @classmethod
    def _check_device(cls, value: str) -> str:
        device = value.strip().lower()
        if not device:
            raise ValueError("AI_DEVICE must not be empty (use 'cpu' or 'cuda').")
        return device

    @model_validator(mode="after")
    def _check_ai_and_camera_ranges(self) -> "Settings":
        if self.ai_track_low_confidence > self.ai_confidence:
            raise ValueError(
                "AI_TRACK_LOW_CONFIDENCE must not exceed AI_CONFIDENCE "
                "(ByteTrack's low threshold sits below its high threshold)."
            )
        if self.camera_max_reconnect_delay < self.camera_reconnect_delay:
            raise ValueError(
                "CAMERA_MAX_RECONNECT_DELAY must be >= CAMERA_RECONNECT_DELAY."
            )
        if self.camera_rtsp_transport.lower() not in {"tcp", "udp"}:
            raise ValueError("CAMERA_RTSP_TRANSPORT must be 'tcp' or 'udp'.")
        if self.is_production:
            if not self.api_admin_keys.strip():
                raise ValueError(
                    "APP_ENV=production requires API_ADMIN_KEYS (and normally API_VIEWER_KEYS). "
                    "An unauthenticated API must not be deployed."
                )
            if "*" in self.cors_origin_list:
                raise ValueError("CORS_ORIGINS='*' is not allowed in production.")
            weak = [k for k in self.viewer_key_list + self.operator_key_list + self.admin_key_list
                    if len(k) < 24]
            if weak:
                raise ValueError("API keys must be at least 24 characters in production.")
        return self

    @property
    def is_production(self) -> bool:
        return self.app_env.strip().lower() in {"production", "prod"}

    @property
    def viewer_key_list(self) -> list[str]:
        return [k.strip() for k in self.api_viewer_keys.split(",") if k.strip()]

    @property
    def operator_key_list(self) -> list[str]:
        return [k.strip() for k in self.api_operator_keys.split(",") if k.strip()]

    @property
    def admin_key_list(self) -> list[str]:
        return [k.strip() for k in self.api_admin_keys.split(",") if k.strip()]

    @property
    def auth_enabled(self) -> bool:
        return bool(self.viewer_key_list or self.operator_key_list or self.admin_key_list)

    @property
    def docs_visible(self) -> bool:
        return self.docs_enabled if self.docs_enabled is not None else not self.is_production

    @property
    def demo_video_path(self) -> Path:
        path = Path(self.demo_video_dir).expanduser()
        return path if path.is_absolute() else Path(__file__).resolve().parent.parent / path

    @property
    def ai_target_class_list(self) -> list[str]:
        return [c.strip().lower() for c in self.ai_target_classes.split(",") if c.strip()]

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def safe_database_url(self) -> str:
        """DATABASE_URL with the password masked, for logs and error messages."""
        return mask_url_password(self.database_url)


def mask_url_password(url: str) -> str:
    """Replace the password in a database URL with '***' so it is never logged."""
    if "://" not in url:
        return url
    scheme, rest = url.split("://", 1)
    if "@" not in rest:
        return url
    credentials, host = rest.rsplit("@", 1)
    if ":" in credentials:
        user, _ = credentials.split(":", 1)
        credentials = f"{user}:***"
    return f"{scheme}://{credentials}@{host}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
