from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .ai_operation_maintenance import OperationLeaseLost
from .metadata import MetadataStoreError


LEASE_LOSS_CODES = {"invalid_claim", "operation_not_running", "operation_lease_expired"}
UNKNOWN_OUTCOME = {
    "code": "execution_outcome_unknown",
    "message": "Operation outcome is uncertain; reload authoritative state",
}
_MISSING = object()


@dataclass(frozen=True)
class AiExecutionFailure:
    state: str
    status: int
    error: dict[str, Any]


@dataclass(frozen=True)
class AiExecutionOutcome:
    status: int
    operation: dict[str, Any]
    approval: dict[str, Any]

    @property
    def payload(self) -> dict[str, Any]:
        return {"operation": self.operation, "approval": self.approval}


def known_failure(
    error: Exception,
    known_types: tuple[type[Exception], ...],
    *,
    uncertain: Callable[[Exception, dict[str, Any]], bool] | None = None,
) -> AiExecutionFailure | None:
    if not isinstance(error, known_types):
        return None
    payload = error.payload if hasattr(error, "payload") else error.to_dict()
    detail = payload["error"]
    state = "uncertain" if uncertain is not None and uncertain(error, detail) else "failed"
    return AiExecutionFailure(state, getattr(error, "status", 400), detail)


class AiExecutionRunner:
    """Own one AI operation from preflight through durable finalization."""

    def __init__(self, authority: Any, maintenance: Any = None):
        self.authority = authority
        self.maintenance = maintenance

    def run(
        self,
        *,
        proposal_id: str,
        chat_id: str,
        policy_revision: Any,
        confirmation: Any,
        preflight: Callable[[], Any],
        execute: Callable[[str, Any], Any],
        classify_failure: Callable[[Exception], AiExecutionFailure | None],
        durable_result: Callable[[Any], Any] | None = None,
        expose_execution_result: bool = False,
        release_cancellation: Callable[[str], None] | None = None,
        return_existing_before_preflight: bool = False,
    ) -> AiExecutionOutcome:
        existing_lookup = getattr(self.authority, "operation_for_proposal", None)
        existing = (
            existing_lookup(proposal_id, chat_id)
            if return_existing_before_preflight and existing_lookup is not None else None
        )
        context = _MISSING
        if existing is not None:
            operation, approval = self.authority.authorize_and_claim(
                proposal_id, chat_id, policy_revision, confirmation,
            )
        else:
            context = preflight()
            operation, approval = self.authority.authorize_and_claim(
                proposal_id, chat_id, policy_revision, confirmation,
            )
        execution_owner = operation.pop("executionOwner", False)
        if not execution_owner:
            operation.pop("attemptId", None)
            operation.pop("claimToken", None)
            return AiExecutionOutcome(200, operation, approval)

        attempt_id = operation.pop("attemptId")
        claim_token = operation.pop("claimToken")
        operation_id = operation["id"]
        result = None
        failure = None
        try:
            try:
                if context is _MISSING:
                    context = preflight()
                if self.maintenance is not None:
                    self.maintenance.track(operation_id, attempt_id, claim_token)
                result = execute(operation_id, context)
                projected = durable_result(result) if durable_result is not None else result
            except Exception as error:
                try:
                    failure = classify_failure(error)
                except Exception:
                    failure = None
                if failure is None:
                    failure = AiExecutionFailure("uncertain", 500, dict(UNKNOWN_OUTCOME))
                projected = None

            if failure is None:
                finished, lease_lost = self._finish(
                    operation_id, chat_id, attempt_id, claim_token, "succeeded", result=projected,
                )
                if expose_execution_result and not lease_lost and finished.get("state") == "succeeded":
                    finished = {**finished, "result": result}
                return AiExecutionOutcome(409 if lease_lost else 200, finished, approval)

            finished, lease_lost = self._finish(
                operation_id, chat_id, attempt_id, claim_token, failure.state, error=failure.error,
            )
            return AiExecutionOutcome(409 if lease_lost else failure.status, finished, approval)
        finally:
            try:
                if self.maintenance is not None:
                    self.maintenance.release(attempt_id)
            finally:
                if release_cancellation is not None:
                    release_cancellation(operation_id)

    def _finish(
        self,
        operation_id: str,
        chat_id: str,
        attempt_id: str,
        claim_token: str,
        state: str,
        *,
        result: Any = None,
        error: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], bool]:
        try:
            if self.maintenance is not None:
                self.maintenance.assert_owned(attempt_id)
            return self.authority.finish_operation(
                attempt_id, claim_token, state, result=result, error=error,
            ), False
        except OperationLeaseLost:
            return self.authority.operation(operation_id, chat_id), True
        except MetadataStoreError as failure:
            if failure.code not in LEASE_LOSS_CODES:
                raise
            return self.authority.operation(operation_id, chat_id), True
