"""Service-layer exceptions.

Services raise these instead of HTTPException so they stay independent of the
web layer. app/api/errors.py translates them into HTTP responses.
"""


class ServiceError(Exception):
    """Base class for expected, client-visible service failures."""

    code = "error"

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class NotFoundError(ServiceError):
    """Requested resource does not exist. -> 404"""

    code = "not_found"


class ConflictError(ServiceError):
    """Request conflicts with existing data, e.g. a duplicate code. -> 409"""

    code = "conflict"


class ValidationError(ServiceError):
    """Request is well-formed but semantically invalid. -> 422"""

    code = "invalid_request"
