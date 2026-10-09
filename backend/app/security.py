"""Minimum safeguards for exposing the API beyond localhost.

* API keys with two roles. A VIEWER key may read (GET); an ADMIN key may also
  change configuration (POST/PUT/DELETE: cameras, RTSP URLs, zones, staff, seats).
  Keys travel in `Authorization: Bearer <key>` (or `X-API-Key`) and are compared
  in constant time. They are never logged.
* With no keys configured the API is open - allowed only when APP_ENV is not
  production (Settings refuses to start a production app without admin keys).
* A per-client rate limit (sliding 60 s window, in memory, per process).
* Conservative security headers on every response.

This is deliberately simple: one temple, a handful of operators. If the system
later needs named users, audit trails per person or SSO, replace the key check
in `authorize()` with an OAuth2/OIDC dependency; routes do not change.
"""

from __future__ import annotations

import hmac
import threading
import time
from collections import OrderedDict, deque
from collections.abc import Awaitable, Callable
from enum import StrEnum

from fastapi import Depends, HTTPException, Request, status

from app.config import Settings, get_settings

READ_METHODS = {"GET", "HEAD", "OPTIONS"}


class Role(StrEnum):
    VIEWER = "viewer"        # read the dashboards
    OPERATOR = "operator"    # also: mark seats, run sessions, mark attendance
    ADMIN = "admin"          # also: temples, halls, staff, cameras, configuration

    @property
    def rank(self) -> int:
        return {"viewer": 0, "operator": 1, "admin": 2}[self.value]

    def allows(self, needed: "Role") -> bool:
        return self.rank >= needed.rank


def _presented_key(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    if header[:7].lower() == "bearer ":
        return header[7:].strip() or None
    return request.headers.get("x-api-key") or None


def _matches(candidate: str, keys: list[str]) -> bool:
    # Compare against every key (no early exit) so timing reveals nothing.
    found = False
    for key in keys:
        found |= hmac.compare_digest(candidate.encode(), key.encode())
    return found


def role_for(request: Request, settings: Settings) -> Role | None:
    """The caller's role, or None when the key is missing or wrong."""
    if not settings.auth_enabled:
        return Role.ADMIN            # open development mode
    key = _presented_key(request)
    if not key:
        return None
    if _matches(key, settings.admin_key_list):
        return Role.ADMIN
    if _matches(key, settings.operator_key_list):
        return Role.OPERATOR
    if _matches(key, settings.viewer_key_list):
        return Role.VIEWER
    return None


def _authenticate(request: Request) -> Role:
    role = role_for(request, get_settings())
    if role is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="A valid API key is required (Authorization: Bearer <key>).",
            headers={"WWW-Authenticate": "Bearer"},
        )
    request.state.role = role
    return role


def _forbid(needed: Role) -> HTTPException:
    return HTTPException(status.HTTP_403_FORBIDDEN, detail=f"This action needs an {needed.value} key."
                         if needed is not Role.VIEWER else "Not allowed.")


def role_guard(write_role: Role = Role.ADMIN, read_role: Role = Role.VIEWER):
    """Dependency factory: reads need `read_role`, writes (POST/PUT/PATCH/DELETE) `write_role`."""
    def guard(request: Request) -> Role:
        role = _authenticate(request)
        needed = read_role if request.method in READ_METHODS else write_role
        if not role.allows(needed):
            raise _forbid(needed)
        return role
    return guard


def authorize(request: Request) -> Role:
    """Default router dependency: reads need a viewer key, writes an admin key."""
    return role_guard(Role.ADMIN)(request)


RequireRole = Depends(authorize)
# Day-to-day hall work (seat status, sessions, attendance): operators may write.
RequireOperatorWrites = Depends(role_guard(Role.OPERATOR))


def require_admin(request: Request) -> Role:
    """Admin for every method (e.g. GET endpoints that expose sensitive data)."""
    role = _authenticate(request)
    if not role.allows(Role.ADMIN):
        raise _forbid(Role.ADMIN)
    return role


def require_operator(request: Request) -> Role:
    """Operator or admin for every method."""
    role = _authenticate(request)
    if not role.allows(Role.OPERATOR):
        raise _forbid(Role.OPERATOR)
    return role


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------
class SlidingWindowLimiter:
    """Allow at most `limit` events per `window` seconds per key. Thread-safe.

    Memory is bounded: at most `max_keys` clients are tracked (least recently
    seen are forgotten first).
    """

    def __init__(self, limit: int, window: float = 60.0, max_keys: int = 10_000,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.limit = limit
        self.window = window
        self._max_keys = max_keys
        self._clock = clock
        self._hits: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = threading.Lock()

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()

    def hit(self, key: str) -> float | None:
        """Record one event. Returns None if allowed, else seconds until allowed."""
        if self.limit <= 0:
            return None
        now = self._clock()
        with self._lock:
            hits = self._hits.get(key)
            if hits is None:
                hits = self._hits[key] = deque()
                if len(self._hits) > self._max_keys:
                    self._hits.popitem(last=False)
            else:
                self._hits.move_to_end(key)
            while hits and now - hits[0] >= self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                return max(0.0, self.window - (now - hits[0]))
            hits.append(now)
            return None


def client_key(request: Request, trusted_proxies: int) -> str:
    """Client address; X-Forwarded-For is honoured only behind N trusted proxies."""
    if trusted_proxies > 0:
        forwarded = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()]
        if len(forwarded) >= trusted_proxies:
            return forwarded[-trusted_proxies]
    return request.client.host if request.client else "unknown"


SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}

EXEMPT_PATHS = {"/api/health"}


def install_http_guards(app, settings: Settings) -> None:
    """Rate limiting and security headers as one HTTP middleware."""
    from fastapi.responses import JSONResponse

    all_limiter = SlidingWindowLimiter(settings.rate_limit_per_minute)
    write_limiter = SlidingWindowLimiter(settings.rate_limit_writes_per_minute)
    app.state.rate_limiters = (all_limiter, write_limiter)

    @app.middleware("http")
    async def guards(request: Request, call_next: Callable[[Request], Awaitable]):
        path = request.url.path
        if path.startswith("/api/") and path not in EXEMPT_PATHS:
            who = client_key(request, settings.trusted_proxy_count)
            wait = all_limiter.hit(who)
            if wait is None and request.method not in READ_METHODS:
                wait = write_limiter.hit(who)
            if wait is not None:
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Too many requests. Slow down.", "code": "rate_limited"},
                    headers={"Retry-After": str(int(wait) + 1), **SECURITY_HEADERS},
                )
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        if path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response
