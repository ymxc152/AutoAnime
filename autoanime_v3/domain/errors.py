"""Stable business errors exposed by services and the HTTP API."""


class DomainError(Exception):
    code = "domain_error"

    def __init__(self, message, details=None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


class NotFoundError(DomainError):
    code = "not_found"


class DuplicateRootError(DomainError):
    code = "duplicate_root"


class UnsafeRootError(DomainError):
    code = "unsafe_root"


class PathOutsideRootError(DomainError):
    code = "path_outside_root"


class RevisionConflictError(DomainError):
    code = "revision_conflict"


class ValidationError(DomainError):
    code = "validation_error"


class AuthenticationError(DomainError):
    code = "authentication_failed"


class LoginThrottledError(DomainError):
    code = "login_throttled"


class CsrfValidationError(DomainError):
    code = "csrf_validation_failed"


class AlreadyBootstrappedError(DomainError):
    code = "already_bootstrapped"


class BootstrapLocalOnlyError(DomainError):
    code = "bootstrap_local_only"


class LocalOnlyError(DomainError):
    code = "local_only"


class FolderDialogError(DomainError):
    code = "folder_dialog_failed"


class LeaseConflictError(DomainError):
    code = "lease_conflict"


class InvalidStateError(DomainError):
    code = "invalid_state"


class ExecutionPolicyError(DomainError):
    code = "execution_policy_forbidden"


class StalePlanError(DomainError):
    code = "stale_plan"


class PlanConflictError(DomainError):
    code = "plan_conflict"


class ImmutablePlanError(DomainError):
    code = "immutable_plan"
