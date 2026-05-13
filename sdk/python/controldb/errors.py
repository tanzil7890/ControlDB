class ControlDBError(Exception):
    """Base error class for the SDK."""


class AuthenticationError(ControlDBError):
    pass


class AuthorizationError(ControlDBError):
    pass


class ValidationError(ControlDBError):
    pass


class RedactionError(ControlDBError):
    pass


class PolicyDeniedError(ControlDBError):
    def __init__(self, message: str, policy_id: str = "", reason: str = ""):
        super().__init__(message)
        self.policy_id = policy_id
        self.reason = reason


class ApprovalRequiredError(ControlDBError):
    def __init__(self, message: str, approval_id: str = "", policy_id: str = ""):
        super().__init__(message)
        self.approval_id = approval_id
        self.policy_id = policy_id


class CollectorUnavailableError(ControlDBError):
    pass


class PayloadTooLargeError(ControlDBError):
    pass


class IdempotencyConflictError(ControlDBError):
    pass
