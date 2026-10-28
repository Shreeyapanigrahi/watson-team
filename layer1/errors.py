"""Layer 1 exceptions. Each maps to an HTTP status in api/main.py."""


class WatsonError(Exception):
    pass


class LifecycleViolationError(WatsonError):
    """A state change or graph/index access that the lifecycle forbids."""


class WatsonAccessDeniedError(WatsonError):
    """The user's role or attributes do not permit the action or document."""


class PIIBlockedError(WatsonError):
    """Approval attempted on a PII-flagged document without an explicit override."""


class MetadataIncompleteError(WatsonError):
    """Required metadata is missing, so the document cannot become ACTIVE."""


class UnsupportedFormatError(WatsonError):
    pass


class NotFoundError(WatsonError):
    pass
