"""Errors shared by query execution consumers and the retained-result runtime."""

from schemii.common.metadata.limit_events import LimitEventNotice


class ConsoleServiceError(RuntimeError):
    def __init__(
        self, status: int, code: str, message: str, *,
        details: dict[str, object] | None = None,
        retryable: bool = False,
        limit_event: LimitEventNotice | None = None,
    ) -> None:
        self.status = status
        self.code = code
        self.details = details or {}
        self.retryable = retryable
        self.limit_event = limit_event
        super().__init__(message)
