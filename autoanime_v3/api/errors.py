"""HTTP status mapping for stable domain errors."""

from autoanime_v3.domain.errors import (
    AuthenticationError,
    BootstrapLocalOnlyError,
    CsrfValidationError,
    FolderDialogError,
    LocalOnlyError,
    LoginThrottledError,
    NotFoundError,
    ValidationError,
)


def status_for_error(error):
    if isinstance(error, AuthenticationError):
        return 401
    if isinstance(error, (CsrfValidationError, BootstrapLocalOnlyError, LocalOnlyError)):
        return 403
    if isinstance(error, NotFoundError):
        return 404
    if isinstance(error, LoginThrottledError):
        return 429
    if isinstance(error, (ValidationError, FolderDialogError)):
        return 422
    return 409
