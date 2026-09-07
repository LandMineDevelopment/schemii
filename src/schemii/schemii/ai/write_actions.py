"""Explicitly authorized SQL batches use the Console's transaction boundary."""
from secrets import token_hex
from pydantic import Field

from schemii.common.api.models import ApiModel
from schemii.common.postgres.console.models import (
    ConsoleTransactionCreate, ConsoleTransactionExecutionCreate, ConsoleTransactionCommand,
)


class WriteBatch(ApiModel):
    statements: list[str] = Field(min_length=1, max_length=1000)


def execute_write(services, owner, workspace_id, workspace_revision, action, *, is_authorized=lambda: True):
    console = services.console
    settings = console.settings(owner)
    transaction = console.create_transaction(owner, workspace_id, ConsoleTransactionCreate(
        console_id="con_" + token_hex(16), expected_workspace_revision=workspace_revision,
        expected_settings_revision=settings.revision))
    commit_attempted = False
    try:
        execution = console.reserve_transaction_execution(owner, workspace_id, transaction.id,
            ConsoleTransactionExecutionCreate(expected_revision=transaction.revision,
                statements=action["statements"]))
        console.run_transaction(owner, execution.id)
        execution = console.get(owner, workspace_id, execution.id)
        current = console.get_transaction(owner, workspace_id, transaction.id)
        if execution.status != "succeeded" or not is_authorized():
            from .service import AiServiceError
            raise AiServiceError(422, "ai_write_failed", "The SQL batch failed; no commit was requested. Check the Console execution receipt.")
        commit_attempted = True
        result = console.commit_transaction(owner, workspace_id, transaction.id,
            ConsoleTransactionCommand(expected_revision=current.revision))
        return {"transactionId": transaction.id, "executionId": execution.id,
                "commitOutcome": result.status, "liveDatabaseChanged": True if result.status == "committed" else None}
    except Exception:
        # Only roll back an open transaction; never retry an uncertain commit.
        if not commit_attempted:
            try:
                current = console.get_transaction(owner, workspace_id, transaction.id)
                if current.status in {"open", "failed"}:
                    console.rollback_transaction(owner, workspace_id, transaction.id,
                        ConsoleTransactionCommand(expected_revision=current.revision))
            except Exception:
                # Preserve the initiating failure; Console owns recovery state.
                pass
        raise
