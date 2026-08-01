"""Dependency helpers kept free of global application state."""

from autoanime_v3.domain.errors import AuthenticationError, CsrfValidationError


SESSION_COOKIE = "autoanime_session"
CSRF_HEADER = "X-CSRF-Token"


def session_from_request(request):
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise AuthenticationError("Authentication is required")
    return token


def csrf_from_request(request):
    token = request.headers.get(CSRF_HEADER)
    if not token:
        raise CsrfValidationError("CSRF token is required")
    return token

