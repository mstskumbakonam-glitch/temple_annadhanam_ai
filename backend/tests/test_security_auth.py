"""API keys, roles, production guards, rate limits and security headers."""

import pytest

from app.config import Settings, get_settings
from app.security import SlidingWindowLimiter

VIEWER = "viewer-key-0123456789abcdefghij"
ADMIN = "admin-key-0123456789abcdefghijkl"


@pytest.fixture
def keys():
    settings = get_settings()
    saved = settings.api_viewer_keys, settings.api_admin_keys
    object.__setattr__(settings, "api_viewer_keys", VIEWER)
    object.__setattr__(settings, "api_admin_keys", ADMIN)
    yield
    object.__setattr__(settings, "api_viewer_keys", saved[0])
    object.__setattr__(settings, "api_admin_keys", saved[1])


def bearer(key):
    return {"Authorization": f"Bearer {key}"}


CAMERA = {"camera_id": "AUTH-CAM", "camera_name": "Gate",
          "rtsp_url": "rtsp://admin:hunter2@10.1.1.1/live"}


# ------------------------------------------------------------------- roles


@pytest.mark.db
def test_liveness_stays_public_but_everything_else_needs_a_key(api_client, keys):
    assert api_client.get("/api/health").status_code == 200
    for path in ["/api/health/db", "/api/cameras", "/api/analytics/live", "/api/alerts",
                 "/api/ai/status", "/api/dashboard/summary", "/api/visitors", "/api/seats"]:
        r = api_client.get(path)
        assert r.status_code == 401, path
        assert r.headers.get("www-authenticate") == "Bearer"


@pytest.mark.db
def test_wrong_key_is_rejected(api_client, keys):
    assert api_client.get("/api/cameras", headers=bearer("nope")).status_code == 401
    assert api_client.get("/api/cameras", headers={"X-API-Key": "nope"}).status_code == 401


@pytest.mark.db
def test_viewer_can_read_but_not_write(api_client, keys):
    assert api_client.get("/api/cameras", headers=bearer(VIEWER)).status_code == 200
    assert api_client.get("/api/cameras", headers={"X-API-Key": VIEWER}).status_code == 200
    r = api_client.post("/api/cameras", json=CAMERA, headers=bearer(VIEWER))
    assert r.status_code == 403
    assert api_client.put("/api/cameras/X/analytics", json={}, headers=bearer(VIEWER)).status_code == 403


@pytest.mark.db
def test_admin_can_write_and_only_admin_sees_camera_addresses(api_client, keys):
    r = api_client.post("/api/cameras", json=CAMERA, headers=bearer(ADMIN))
    assert r.status_code == 201
    assert r.json()["rtsp_url_masked"] == "rtsp://admin:***@10.1.1.1/live"
    viewer_view = api_client.get("/api/cameras/AUTH-CAM", headers=bearer(VIEWER)).json()
    assert viewer_view["rtsp_url_masked"] is None
    assert "10.1.1.1" not in api_client.get("/api/cameras", headers=bearer(VIEWER)).text


@pytest.mark.db
def test_staff_records_are_admin_only(api_client, keys):
    assert api_client.get("/api/staff", headers=bearer(VIEWER)).status_code == 403
    assert api_client.get("/api/staff", headers=bearer(ADMIN)).status_code == 200


@pytest.mark.db
def test_open_mode_without_keys_still_works_for_development(api_client):
    assert not get_settings().auth_enabled
    assert api_client.get("/api/cameras").status_code == 200


# --------------------------------------------------------- production guard
def _prod(**over):
    base = dict(database_url="postgresql+psycopg://u:p@localhost/db", app_env="production",
                api_admin_keys=ADMIN, api_viewer_keys=VIEWER, cors_origins="https://temple.example")
    base.update(over)
    return Settings(_env_file=None, **base)


def test_production_refuses_to_start_without_admin_keys():
    with pytest.raises(ValueError, match="API_ADMIN_KEYS"):
        _prod(api_admin_keys="", api_viewer_keys="")


def test_production_refuses_short_keys_and_wildcard_cors():
    with pytest.raises(ValueError, match="24 characters"):
        _prod(api_viewer_keys="short")
    with pytest.raises(ValueError, match="CORS"):
        _prod(cors_origins="*")


def test_production_hides_docs_by_default():
    assert _prod().docs_visible is False
    assert _prod(docs_enabled=True).docs_visible is True
    assert Settings(_env_file=None, database_url="postgresql+psycopg://u:p@h/d").docs_visible is True


def test_unknown_timezone_is_rejected():
    with pytest.raises(ValueError, match="time zone"):
        Settings(_env_file=None, database_url="postgresql+psycopg://u:p@h/d", site_timezone="Mars/Base")


# ----------------------------------------------------------------- limiter
def test_sliding_window_limiter():
    now = [0.0]
    limiter = SlidingWindowLimiter(3, window=60, clock=lambda: now[0])
    assert [limiter.hit("a") for _ in range(3)] == [None, None, None]
    assert limiter.hit("a") == pytest.approx(60.0)
    assert limiter.hit("b") is None            # per client
    now[0] = 61
    assert limiter.hit("a") is None


def test_limiter_memory_is_bounded():
    limiter = SlidingWindowLimiter(1, max_keys=10)
    for i in range(100):
        limiter.hit(f"client-{i}")
    assert len(limiter._hits) == 10


def test_disabled_limiter_never_blocks():
    limiter = SlidingWindowLimiter(0)
    assert all(limiter.hit("x") is None for _ in range(1000))


@pytest.mark.db
def test_rate_limit_returns_429_with_retry_after(api_client):
    from app.main import app

    _, writes = app.state.rate_limiters
    saved = writes.limit
    writes.limit = 2
    try:
        codes = [api_client.post("/api/cameras", json={**CAMERA, "camera_id": f"RL-{i}"}).status_code
                 for i in range(4)]
    finally:
        writes.limit = saved
    assert codes[:2] == [201, 201] and codes[2:] == [429, 429]


@pytest.mark.db
def test_security_headers(api_client):
    r = api_client.get("/api/health")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["cache-control"] == "no-store"


# ------------------------------------------------------- camera URL safety
@pytest.mark.db
@pytest.mark.parametrize("url", ["file:///etc/passwd", "/etc/passwd", "concat:a|b",
                                 "demo://../secret.mp4", "demo://clip.exe",
                                 "rtsp://cam/live\nX-Injected: 1"])
def test_unsafe_stream_urls_are_rejected_on_create_and_update(api_client, url):
    r = api_client.post("/api/cameras", json={**CAMERA, "camera_id": "URL-CAM", "rtsp_url": url})
    assert r.status_code == 422, url
    api_client.post("/api/cameras", json={**CAMERA, "camera_id": "URL-OK"})
    r = api_client.put("/api/cameras/URL-OK", json={"rtsp_url": url})
    assert r.status_code == 422, url


@pytest.mark.db
def test_demo_url_is_accepted_and_labelled_recorded(api_client):
    r = api_client.post("/api/cameras", json={**CAMERA, "camera_id": "DEMO-1",
                                              "rtsp_url": "demo://queue_clip.mp4"})
    assert r.status_code == 201
    assert r.json()["source_kind"] == "recorded"
    status = {c["camera_id"]: c for c in api_client.get("/api/cameras/status").json()}
    assert status["DEMO-1"]["source_kind"] == "recorded"
