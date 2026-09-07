"""Assistant orchestration: models propose; existing services authorize and act."""

from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time
from collections import OrderedDict
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any

from schemii.common.ai.pi import PiRuntime, PiError
from schemii.common.postgres.console.models import ConsoleExecutionCreate
from schemii.schemii.designs.models import (
    DesignCheckConstraint,
    DesignColumn,
    DesignFunction,
    DesignIndex,
    DesignKeyConstraint,
    DesignRelationship,
    DesignTable,
    DesignTrigger,
    DesignType,
    DesignView,
    SchemiiDesignReplace,
)
from schemii.schemii.migrations.models import MigrationPlanCreate

from .models import (
    SchemiiAiPreferencesResult,
    SchemiiQueryResult,
    SchemiiTransientResponse,
)
from .repository import AiRepository
from .read_workflow import run_read_workflow
from .prompt import system_prompt
from .action_context import action_context, intent_key, object_ids
from . import reads
from . import actions
from .action_policy import ACTION_POLICIES, disabled_action_ids, operation_receipt, required_action_ids
from .design_permissions import design_permission_ids
from .write_actions import execute_write
from .tools import (
    authority_manifest,
    normalize_tool_call,
    permission_label,
    tool_definitions,
    tool_enabled,
)


class AiServiceError(RuntimeError):
    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.status = status
        self.code = code
        self.details = details or {}
        super().__init__(message)


_TOP_LEVEL_MODELS = {
    "types": (DesignType, "type"),
    "tables": (DesignTable, "table"),
    "relationships": (DesignRelationship, "relationship"),
    "functions": (DesignFunction, "function"),
    "views": (DesignView, "view"),
    "triggers": (DesignTrigger, "trigger"),
}

_TABLE_MEMBER_MODELS = {
    "columns": (DesignColumn, "column"),
    "keys": (DesignKeyConstraint, "key"),
    "checks": (DesignCheckConstraint, "check"),
    "indexes": (DesignIndex, "index"),
}


class AiService:
    def __init__(
        self,
        repository: AiRepository,
        runtime: PiRuntime | None,
        services: Any,
    ) -> None:
        self.repository = repository
        self.runtime = runtime
        self.services = services
        self.policy = services.admin_config.ai
        self._streams: dict[tuple[str, str], dict[str, str]] = {}
        self._transient_lock = threading.RLock()
        self._transient_responses: OrderedDict[
            tuple[str, str, str], tuple[SchemiiTransientResponse, int]
        ] = OrderedDict()
        self._transient_response_bytes = 0

    def status(self, owner: str) -> dict[str, Any]:
        if not self.policy.enabled or self.runtime is None:
            return {"enabled": self.policy.enabled, "healthy": False, "providers": []}
        return self.runtime.status(owner)

    def stream(self, owner: str, chat_id: str) -> dict[str, str | None]:
        chat = self.repository.get_chat(owner, chat_id)
        with self._transient_lock:
            return dict(self._streams.get((owner, chat_id), {})) if chat.status == "working" else {}

    def recover_interrupted(self) -> None:
        """Resolve durable work that could not survive a process restart."""

        sessions = self.repository.recover_interrupted()
        if self.runtime is None:
            return
        for owner, chat_id, session_id in sessions:
            try:
                self.runtime.cancel(owner, session_id)
                self.repository.clear_chat_runtime(owner, chat_id)
            except PiError:
                continue
            except Exception:
                continue

    def cancel_turn(self, owner: str, chat_id: str, turn_id: str) -> Any:
        turn, session_id = self.repository.cancel_turn(owner, chat_id, turn_id)
        if session_id and self.runtime is not None:
            try:
                self.runtime.cancel(owner, turn_id)
                self.repository.clear_chat_runtime(owner, chat_id)
            except PiError:
                pass
        try:
            self.repository.add_event(
                owner,
                chat_id,
                "status",
                {
                    "turnId": turn_id,
                    "stage": "response",
                    "state": "cancelled",
                    "label": "Turn stopped",
                },
            )
        except Exception:
            pass
        return turn

    def transient_responses(
        self, owner: str, chat_id: str
    ) -> list[SchemiiTransientResponse]:
        self.repository.get_chat(owner, chat_id)
        with self._transient_lock:
            self._prune_transient_locked()
            return [
                response.model_copy(deep=True)
                for (item_owner, item_chat, _), (response, _) in self._transient_responses.items()
                if item_owner == owner and item_chat == chat_id
            ]

    def _store_transient_response(
        self, owner: str, chat_id: str, turn_id: str, text: str
    ) -> None:
        now = datetime.now(timezone.utc)
        response = SchemiiTransientResponse(
            turn_id=turn_id,
            text=text,
            created_at=now,
            expires_at=now
            + timedelta(seconds=self.policy.transient_response_ttl_seconds),
        )
        size = len(text.encode("utf-8"))
        key = (owner, chat_id, turn_id)
        with self._transient_lock:
            self._prune_transient_locked()
            old = self._transient_responses.pop(key, None)
            if old is not None:
                self._transient_response_bytes -= old[1]
            self._transient_responses[key] = (response, size)
            self._transient_response_bytes += size
            while (
                self._transient_responses
                and self._transient_response_bytes
                > self.policy.transient_response_memory_bytes
            ):
                _, (_, removed_size) = self._transient_responses.popitem(last=False)
                self._transient_response_bytes -= removed_size

    def _prune_transient_locked(self) -> None:
        now = datetime.now(timezone.utc)
        for key, (response, size) in list(self._transient_responses.items()):
            if response.expires_at > now:
                continue
            self._transient_responses.pop(key, None)
            self._transient_response_bytes -= size

    def _bounded_response(self, text: str) -> str:
        encoded = text.encode("utf-8")
        if len(encoded) <= self.policy.response_bytes:
            return text
        suffix = "\n\n[Response shortened by the server response-size limit.]"
        room = max(0, self.policy.response_bytes - len(suffix.encode("utf-8")))
        return encoded[:room].decode("utf-8", errors="ignore") + suffix

    def _require_available_model(self, owner: str, provider_id: str, model_id: str) -> None:
        status = self.status(owner)
        available = {
            (provider["id"], model["id"])
            for provider in status["providers"]
            if provider["available"]
            for model in provider["models"]
            if model["status"] == "active"
        }
        if (provider_id, model_id) not in available:
            raise AiServiceError(
                422,
                "ai_model_unavailable",
                "The selected AI model is unavailable. Connect its provider or choose another model; your conversation is retained.",
            )

    def create_chat(self, owner: str, workspace_id: str, body: Any) -> Any:
        self.services.workspaces.get(owner, workspace_id)
        self._require_available_model(owner, body.provider_id, body.model_id)
        return self.repository.create_chat(
            owner,
            workspace_id,
            body.title or "New conversation",
            body.provider_id,
            body.model_id,
            body.capabilities,
        )

    def save_preferences(self, owner: str, chat_id: str, body: Any) -> SchemiiAiPreferencesResult:
        current_chat = self.repository.get_chat(owner, chat_id)
        self.services.workspaces.get(owner, current_chat.workspace_id)
        if (current_chat.provider_id, current_chat.model_id) != (body.provider_id, body.model_id):
            self._require_available_model(owner, body.provider_id, body.model_id)
        settings, saved_chat, started_new = self.repository.save_preferences(
            owner,
            chat_id,
            body.expected_settings_revision,
            body.expected_chat_revision,
            body.provider_id,
            body.model_id,
            body.capabilities,
        )
        return SchemiiAiPreferencesResult(
            settings=settings,
            chat=saved_chat,
            started_new_conversation=started_new,
        )

    def send(self, owner: str, chat_id: str, body: Any) -> Any:
        chat = self.repository.get_chat(owner, chat_id)
        design = self.services.designs.get(owner, chat.workspace_id)
        if chat.revision != body.expected_chat_revision:
            raise AiServiceError(
                409,
                "ai_chat_changed",
                "The conversation changed after it was opened",
            )
        if design.revision != body.expected_design_revision:
            raise AiServiceError(
                409,
                "ai_design_changed",
                "The design changed after the message was composed",
                {"currentDesignRevision": design.revision},
            )
        if len(body.text.encode("utf-8")) > self.policy.prompt_bytes:
            self.repository.add_event(
                owner, chat_id, "error", {"code": "ai_prompt_too_large"}
            )
            raise AiServiceError(
                413,
                "ai_prompt_too_large",
                "The assistant message exceeds the configured prompt limit",
            )
        if body.result_context_operation_id and not chat.capabilities.structured_data_read:
            raise AiServiceError(
                403,
                "ai_result_context_not_allowed",
                "This conversation is not allowed to send query rows to the model",
            )
        self._require_available_model(owner, chat.provider_id, chat.model_id)
        if chat.provider_id == "opencode" and not getattr(body, "acknowledge_provider_data_policy", False):
            raise AiServiceError(422, "ai_provider_consent_required",
                                 "Free Zen models may use prompts for training. Do not send personal or confidential data. Confirm the provider notice before sending.")
        return self.repository.create_turn(
            owner,
            chat_id,
            body.text,
            body.result_context_operation_id,
            self.policy.maximum_concurrent_turns,
            self.policy.maximum_concurrent_turns_per_user,
            self.policy.message_history_limit,
        )

    def run_turn(self, owner: str, chat_id: str, turn_id: str) -> None:
        if self.repository.claim_turn(owner, chat_id, turn_id) is None:
            return
        session_id = None
        rerun = False
        def record_stage(stage: str, state: str, label: str) -> None:
            self.repository.add_event(
                owner,
                chat_id,
                "status",
                {
                    "turnId": turn_id,
                    "stage": stage,
                    "state": state,
                    "label": label,
                },
            )

        try:
            record_stage("context", "running", "Preparing workspace context")
            if self.runtime is None:
                raise AiServiceError(
                    503, "ai_runtime_unavailable", "The AI runtime is not configured"
                )
            chat = self.repository.get_chat(owner, chat_id)
            design = self.services.designs.get(owner, chat.workspace_id)
            messages = self.repository.list_messages(
                owner, chat_id, self.policy.message_history_limit
            )
            turn = self.repository.get_turn(owner, chat_id, turn_id)
            request_message = next(message for message in messages if message.turn_id == turn_id and message.role == "user")
            result_context = None
            if turn.result_context_operation_id:
                page = self.query_result(
                    owner, chat_id, turn.result_context_operation_id, None
                )
                rerun = page.rerun
                result_context = {
                    "columns": page.columns,
                    "rows": page.rows,
                    "freshnessNotice": page.freshness_notice,
                }

            workspace = self.services.workspaces.get(owner, chat.workspace_id)
            context = {
                "workspace": {
                    "id": chat.workspace_id,
                    "revision": workspace.revision,
                    "namespace": getattr(workspace, "namespace", None),
                    "connectionAttached": getattr(workspace, "connection_id", None)
                    is not None,
                },
                "design": design.model_dump(by_alias=True),
                "authority": authority_manifest(chat.revision, chat.capabilities),
                "actions": action_context(self.repository, owner, chat_id, self.policy.context_bytes // 4),
                "readLimits": {
                    "maximumQueriesPerBatch": self.policy.maximum_read_queries_per_batch,
                    "maximumToolRounds": self.policy.maximum_tool_rounds,
                    "sampleRowsAcrossResults": self.policy.result_context_rows,
                    "sampleBytesAcrossResults": self.policy.result_context_bytes,
                },
                "conversation": [
                    {"role": message.role, "text": message.text}
                    for message in messages if message.turn_id != turn_id
                ],
                "queryResult": result_context,
            }
            if chat.capabilities.live_catalog and workspace.connection_id is not None:
                with self.services.connections.use(
                    owner, workspace.connection_id
                ) as connection:
                    context["liveCatalog"] = self.services.postgres.introspect(
                        connection, workspace.namespace
                    ).model_dump(by_alias=True)

            context["migrationExecutions"] = self._migration_execution_context(owner, chat.workspace_id)

            encoded_context = json.dumps(
                context, separators=(",", ":"), default=str
            )
            if len(encoded_context.encode("utf-8")) > self.policy.context_bytes:
                raise AiServiceError(
                    413,
                    "ai_context_too_large",
                    "The workspace context is too large for one assistant turn",
                )

            record_stage("context", "completed", "Workspace context ready")
            record_stage("model", "running", f"Waiting for {chat.model_id}")
            self._require_available_model(owner, chat.provider_id, chat.model_id)
            session_id = turn_id
            self.repository.set_chat_runtime(
                owner, chat_id, external_session_id=session_id
            )
            with self._transient_lock:
                self._streams[(owner, chat_id)] = {"turnId": turn_id, "text": ""}

            def on_text(delta):
                with self._transient_lock:
                    current = self._streams.get((owner, chat_id))
                    if current is not None:
                        value = current["text"] + delta
                        other_bytes = sum(len(item["text"].encode("utf-8")) for key, item in self._streams.items() if key != (owner, chat_id))
                        if (len(value.encode("utf-8")) <= self.policy.response_bytes
                                and other_bytes + len(value.encode("utf-8")) <= self.policy.transient_response_memory_bytes):
                            current["text"] = value

            def is_authorized():
                current = self.repository.get_chat(owner, chat_id)
                return (current.revision == chat.revision and current.status == "working"
                        and self.repository.get_turn(owner, chat_id, turn_id).status == "running")
            system = system_prompt(encoded_context)
            outcome = run_read_workflow(self, owner, chat, turn, system, request_message.text,
                                        on_text, is_authorized, record_stage)
            if outcome.paused:
                return
            reply = outcome.reply
            if outcome.used_rows:
                result_context = True
            rerun = rerun or outcome.rerun
            record_stage("model", "completed", "Model response received")
            current_chat = self.repository.get_chat(owner, chat_id)
            if self.repository.get_turn(owner, chat_id, turn_id).status == "cancelled":
                return
            policy_changed = current_chat.revision != chat.revision
            text = self._bounded_response(reply.text or "The assistant completed without a final answer.")
            if policy_changed:
                text = (
                    "Assistant permissions changed while I was working. "
                    "No further tools were run; ask again with the current permissions."
                )
            record_stage("response", "running", "Formatting the response")
            stored_text = text
            if result_context is not None:
                stored_text = (
                    "This answer used temporary query rows and is not stored in conversation "
                    "history. It remains visible briefly in this server session; ask again to "
                    "rerun the query and refresh the analysis."
                )
            if result_context is not None:
                self._store_transient_response(owner, chat_id, turn_id, text)
            try:
                # Publish terminal state only after its ephemeral answer is ready.
                self.repository.finish_turn(
                    owner, chat_id, turn_id, stored_text, rerun=rerun,
                    history_limit=self.policy.message_history_limit,
                )
            except Exception:
                with self._transient_lock:
                    removed = self._transient_responses.pop((owner, chat_id, turn_id), None)
                    if removed is not None:
                        self._transient_response_bytes -= removed[1]
                raise
            record_stage("response", "completed", "Response added to the conversation")
            self.repository.add_event(
                owner, chat_id, "message", {"turnId": turn_id, "rerun": rerun}
            )
        except Exception as error:
            try:
                if self.repository.get_turn(owner, chat_id, turn_id).status == "cancelled":
                    return
            except Exception:
                pass
            code = getattr(error, "code", "ai_turn_failed")
            message = str(error) if isinstance(error, (PiError, AiServiceError)) else "The assistant turn could not be completed. Try again; no unvalidated action was applied."
            try:
                record_stage("response", "failed", "Assistant turn stopped")
            except Exception:
                pass
            try:
                self.repository.fail_turn(owner, chat_id, turn_id, code, message)
            except Exception:
                pass
            try:
                self.repository.add_event(
                    owner,
                    chat_id,
                    "error",
                    {"turnId": turn_id, "code": code, "message": message},
                )
            except Exception:
                pass
        finally:
            with self._transient_lock:
                self._streams.pop((owner, chat_id), None)
            if session_id is not None and self.runtime is not None:
                try:
                    self.repository.clear_chat_runtime(owner, chat_id)
                except Exception:
                    pass

    def _save_tool_proposal(self, owner, chat, turn_id, name, arguments, *, rerun_of=None, replay_capabilities=None):
        current = self.repository.get_chat(owner, chat.id)
        if current.revision != chat.revision:
            raise AiServiceError(409, "ai_policy_changed", "Permissions changed; no action was saved")
        proposed = normalize_tool_call(name, arguments)
        if replay_capabilities is not None:
            # Only the server can attach origins. The public tool schema never
            # accepts this marker, and its contents participate in the digest.
            origins = sorted(set(replay_capabilities))
            if (not rerun_of or proposed.action_type != "data_read" or not origins
                    or not set(origins) <= {"structured_query", "raw_sql_read"}):
                raise AiServiceError(422, "ai_invalid_read_replay", "The saved read origins are invalid")
            proposed = replace(proposed, capability="structured_query" if origins == ["structured_query"] else "raw_sql_read",
                action={**proposed.action, "replayCapabilities": origins})
        if proposed.action_type == "data_read":
            if len(proposed.action.get("queries", proposed.action.get("structuredQueries", []))) > self.policy.maximum_read_queries_per_batch:
                raise AiServiceError(422, "ai_read_batch_limit", f"Use at most {self.policy.maximum_read_queries_per_batch} queries per read batch")
            if rerun_of:
                proposed.action["rerunOf"] = rerun_of
        all_prior = self.repository.list_proposals(owner, chat.id)
        workspace = self.services.workspaces.get(owner, chat.workspace_id)
        design = self.services.designs.get(owner, chat.workspace_id)
        authorized_action, _ = self._authorize_action(current.capabilities, proposed.action_type, proposed.action, design)
        proposed = replace(proposed, action=authorized_action,
            destructive=proposed.destructive or any(
                key.endswith(".delete") for key in authorized_action.get("requiredActions", [])))
        if proposed.action_type == "design_change":
            existing_ids = object_ids(design.model_dump())
            intent = intent_key({key: value for key, value in proposed.action.items()
                                 if key != "requiredActions"}, existing_ids)
            for previous in reversed(all_prior):
                if (previous.action_type != "design_change" or previous.status != "pending"
                        or previous.expires_at <= datetime.now(timezone.utc)
                        or previous.expected_workspace_revision != workspace.revision
                        or previous.expected_design_revision != design.revision):
                    continue
                previous_action = self.repository.proposal_action(owner, chat.id, previous.id)
                previous_normalized = normalize_tool_call(name, {"action": {
                    key: value for key, value in previous_action.items() if key != "requiredActions"
                }}).action
                if intent_key(previous_normalized, existing_ids) == intent:
                    return previous
        if proposed.action_type == "migration_resolve":
            proposed.action["reviewContext"] = actions.drift_review_context(self.services, owner,
                chat.workspace_id, actions.MigrationResolveAction.model_validate(proposed.action))
        prior = [p for p in all_prior if p.turn_id == turn_id]
        if len(prior) >= self.policy.maximum_proposals_per_turn:
            raise AiServiceError(429, "ai_proposal_limit_reached", "This turn reached its proposal limit; split remaining work into a new message")
        size = len(json.dumps(proposed.action).encode())
        used = sum(len(json.dumps(self.repository.proposal_action(owner, chat.id, p.id)).encode()) for p in prior)
        if size > self.policy.proposal_bytes or used + size > self.policy.proposal_bytes_per_turn:
            raise AiServiceError(413, "ai_proposal_size_limit_reached", "This turn reached its proposal-size limit; split the request")
        canonical = {"chat": chat.id, "workspace": chat.workspace_id, "workspaceRevision": workspace.revision,
                     "designRevision": design.revision, "capability": proposed.capability,
                     "actionType": proposed.action_type, "action": proposed.action}
        digest = hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        saved = self.repository.create_proposal(owner, chat.id, turn_id, proposed.capability,
            proposed.action_type, proposed.summary, proposed.action, digest, workspace.revision,
            design.revision, proposed.destructive,
            datetime.now(timezone.utc) + timedelta(seconds=self.policy.proposal_ttl_seconds), current.revision)
        self.repository.add_event(owner, chat.id, "proposal", {"turnId": turn_id, "proposalId": saved.id})
        return saved

    def ready_continuation(self, owner, chat_id):
        chat = self.repository.get_chat(owner, chat_id)
        if chat.status != "waiting_approval":
            return None
        for proposal in reversed(self.repository.list_proposals(owner, chat_id)):
            state = self.repository.continuation(owner, chat_id, proposal.turn_id)
            if not state:
                continue
            pending = [self.repository.get_proposal(owner, chat_id, pid) for pid in state.get("pendingProposalIds", [])]
            if any(p.status in {"pending", "executing"} for p in pending):
                return None
            return self.repository.resume_turn(owner, chat_id, proposal.turn_id)
        return None

    def _read_tool_result(self, owner, chat_id, name, arguments, turn_id):
        chat = self.repository.get_chat(owner, chat_id)
        from .tools import DIRECT_TOOLS
        if name in DIRECT_TOOLS and not tool_enabled(name, chat.capabilities, arguments):
            raise AiServiceError(403, "ai_permission_required", f"Enable the action for {name} in Assistant settings")
        if name == "schemii_get_migration_plan":
            return actions.migration_plan(self.services, owner, chat.workspace_id,
                actions.MigrationPlanReference.model_validate(arguments)).model_dump(mode="json", by_alias=True)
        if name == "schemii_list_relations":
            return actions.list_relations(self.services, owner, chat.workspace_id,
                actions.RelationListAction.model_validate(arguments)).model_dump(mode="json", by_alias=True)
        if name == "schemii_preview_reset":
            return actions.preview_baseline_reset(self.services, owner, chat.workspace_id).model_dump(mode="json", by_alias=True)
        if name == "schemii_migration_status":
            return actions.migration_status(self.services, owner, chat.workspace_id,
                actions.MigrationStatusAction.model_validate(arguments)).model_dump(mode="json", by_alias=True)
        return reads.tool_result(self, owner, chat_id, name, arguments, turn_id)

    def _migration_execution_context(self, owner, workspace_id):
        migrations = getattr(self.services, "migrations", None)
        if migrations is None:
            return []
        return [{"id": execution.id, "status": execution.status,
                 "commitOutcome": execution.commit_outcome, "syncStatus": execution.sync_status,
                 "completedStepCount": execution.completed_step_count,
                 "updatedAt": execution.updated_at.isoformat()}
                for execution in migrations.list_executions(owner, workspace_id, 10)]

    def _refresh_tool_context(self, owner, chat, system):
        """Refresh state after our own authorized edits, not the permission policy."""
        instructions, encoded = system.split("\nCONTEXT ", 1)
        context = json.loads(encoded)
        context["design"] = self.services.designs.get(owner, chat.workspace_id).model_dump(by_alias=True)
        context["actions"] = action_context(self.repository, owner, chat.id, self.policy.context_bytes // 4)
        context["migrationExecutions"] = self._migration_execution_context(owner, chat.workspace_id)
        workspace = self.services.workspaces.get(owner, chat.workspace_id)
        context["workspace"]["revision"] = workspace.revision
        if chat.capabilities.live_catalog and workspace.connection_id is not None:
            with self.services.connections.use(owner, workspace.connection_id) as connection:
                context["liveCatalog"] = self.services.postgres.introspect(connection, workspace.namespace).model_dump(by_alias=True)
        return instructions + "\nCONTEXT " + json.dumps(context, default=str, separators=(",", ":"))

    def _operation_read_context(self, owner, chat_id, operation_id):
        return reads.operation_context(self, owner, chat_id, operation_id)

    def execute_batch(self, owner, chat_id, body):
        """One human approval for exact proposals; stop on the first failure.

        Mixed service actions are not a distributed transaction. The receipt
        explicitly records completed work and what was not attempted.
        """
        chat = self.repository.get_chat(owner, chat_id)
        design = self.services.designs.get(owner, chat.workspace_id)
        workspace = self.services.workspaces.get(owner, chat.workspace_id)
        if len({item.proposal_id for item in body.items}) != len(body.items):
            raise AiServiceError(422, "ai_duplicate_approval", "Select each proposal only once")
        for item in body.items:
            proposal = self.repository.get_proposal(owner, chat_id, item.proposal_id)
            if (item.expected_chat_revision != chat.revision or item.expected_proposal_revision != proposal.revision
                    or item.proposal_digest != proposal.digest or proposal.status != "pending"
                    or proposal.expires_at <= datetime.now(timezone.utc)
                    or proposal.expected_design_revision != design.revision
                    or proposal.expected_workspace_revision != workspace.revision):
                raise AiServiceError(409, "ai_batch_changed", "The reviewed batch changed or expired; review it again")
            action = self.repository.proposal_action(owner, chat_id, proposal.id)
            self._authorize_action(chat.capabilities, proposal.action_type, action, design)
        results = []
        revision = design.revision
        for index, item in enumerate(body.items):
            try:
                result = self.execute(owner, chat_id, item.proposal_id, item, _approved_design_revision=revision)
                results.append(result.model_dump(mode="json", by_alias=True))
                if result.kind in {"design_change", "design_history", "migration_resolve"}:
                    revision = result.result_summary["designRevision"]
            except Exception as error:
                return {"operations": results, "status": "partial" if results else "failed",
                        "errorCode": getattr(error, "code", "ai_batch_failed"),
                        "message": "The batch stopped. Completed actions remain recorded; remaining actions were not attempted.",
                        "notAttempted": [entry.proposal_id for entry in body.items[index + 1:]]}
        return {"operations": results, "status": "completed", "notAttempted": []}

    def execute(self, owner: str, chat_id: str, proposal_id: str, body: Any, *, _approved_design_revision=None) -> Any:
        chat = self.repository.get_chat(owner, chat_id)
        if chat.revision != body.expected_chat_revision:
            raise AiServiceError(
                409,
                "ai_chat_changed",
                "The conversation policy changed; review the proposal again",
            )
        proposal = self.repository.get_proposal(owner, chat_id, proposal_id)
        action = self.repository.proposal_action(owner, chat_id, proposal_id)
        workspace = self.services.workspaces.get(owner, chat.workspace_id)
        design = self.services.designs.get(owner, chat.workspace_id)
        if (
            workspace.revision != proposal.expected_workspace_revision
            or design.revision != (proposal.expected_design_revision if _approved_design_revision is None else _approved_design_revision)
        ):
            raise AiServiceError(
                409,
                "ai_context_changed",
                "The workspace or design changed; request a fresh proposal",
            )
        authorized_action, candidate = self._authorize_action(chat.capabilities, proposal.action_type, action, design)
        operation, proposal, action = self.repository.begin_operation(
            owner,
            chat_id,
            proposal_id,
            body.expected_proposal_revision,
            body.proposal_digest,
            body.expected_chat_revision,
            proposal.capability,
            datetime.now(timezone.utc),
            required_actions=required_action_ids(proposal.action_type, authorized_action),
        )
        # The repository returns the digest-bound payload; only the server's
        # effect classifier can attach design permission IDs for older proposals.
        action = authorized_action
        try:
            resource_kind = None
            resource_id = None
            summary = None
            if proposal.action_type == "design_change":
                saved = self._apply_design(
                    owner, chat.workspace_id, design, action, candidate=candidate
                )
                resource_kind = "design"
                resource_id = chat.workspace_id
                summary = {"designRevision": saved.revision, "effect": "saved_to_design", "liveDatabaseChanged": False}
            elif proposal.action_type == "migration_review":
                plan = actions.create_migration_plan(self.services, owner, chat.workspace_id,
                    workspace.revision, design.revision, actions.MigrationPlanAction.model_validate(action))
                resource_kind = "migrationPlan"
                resource_id = plan.id
                summary = plan.model_dump(mode="json", by_alias=True)
            elif proposal.action_type == "migration_apply":
                execution = actions.execute_migration(self.services, owner, chat.workspace_id,
                    actions.MigrationApplyAction.model_validate(action))
                resource_kind, resource_id = "migrationExecution", execution.id
                summary = execution.model_dump(mode="json", by_alias=True)
            elif proposal.action_type == "migration_resolve":
                resolution = actions.resolve_migration(self.services, owner, chat.workspace_id,
                    actions.MigrationResolveAction.model_validate({k: v for k, v in action.items() if k != "reviewContext"}))
                resource_kind, resource_id = "migrationPlan", resolution.plan_id
                summary = {"resolutionId": resolution.id, "planId": resolution.plan_id,
                    "designRevision": resolution.design_revision, "baselineRevision": resolution.baseline_revision,
                    "freshReviewRequired": True, "liveDatabaseChanged": False}
            elif proposal.action_type == "migration_reconcile":
                execution = actions.reconcile_migration(self.services, owner, chat.workspace_id,
                    actions.MigrationReconcileAction.model_validate(action))
                resource_kind, resource_id = "migrationExecution", execution.id
                summary = execution.model_dump(mode="json", by_alias=True)
            elif proposal.action_type == "design_history":
                if action.get("reset"):
                    result = actions.execute_baseline_reset(self.services, owner, chat.workspace_id,
                        actions.BaselineResetAction.model_validate({k: v for k, v in action.items() if k != "reset"}))
                else:
                    result = actions.execute_history(self.services, owner, chat.workspace_id, design.revision,
                        actions.DesignHistoryAction.model_validate(action))
                resource_kind, resource_id = "design", chat.workspace_id
                summary = {"effect": "design_history_updated", "designRevision": self.services.designs.get(owner, chat.workspace_id).revision, "liveDatabaseChanged": False}
            elif proposal.action_type == "sql_write":
                summary = execute_write(self.services, owner, chat.workspace_id, workspace.revision, action,
                    is_authorized=lambda: (
                        self.repository.get_chat(owner, chat_id).revision == chat.revision
                        and self.repository.get_turn(owner, chat_id, proposal.turn_id).status != "cancelled"))
                resource_kind, resource_id = "consoleTransaction", summary["transactionId"]
            elif proposal.action_type == "data_read":
                summary = reads.execute_batch(self, owner, chat, proposal, operation, action)
                resource_kind = "consoleResult"
                resource_id = next((item.get("resultId") for item in summary["results"] if item.get("resultId")), None)
            elif proposal.action_type == "console_script":
                resource_kind = "consoleDraft"
                resource_id = chat.workspace_id
                summary = {"effect": "draft_prepared", "sqlExecuted": False, "liveDatabaseChanged": False}
            return self.repository.finish_operation(
                owner,
                chat_id,
                operation.id,
                status="succeeded",
                resource_kind=resource_kind,
                resource_id=resource_id,
                result_summary=summary,
            )
        except Exception as error:
            self.repository.finish_operation(
                owner,
                chat_id,
                operation.id,
                status="failed",
                error_code=getattr(error, "code", "ai_operation_failed"),
                error_message=str(error),
            )
            raise

    def _authorize_action(self, capabilities, kind, action, design=None):
        candidate = None
        if kind == "design_change":
            candidate = self._design_candidate(design, action)
            action = {**action, "requiredActions": design_permission_ids(design.content, candidate)}
        disabled = disabled_action_ids(capabilities, kind, action)
        if disabled:
            raise AiServiceError(403, "ai_permission_required",
                "Enable these actions in Assistant settings: " + ", ".join(disabled),
                {"requiredActions": disabled})
        return action, candidate

    def _design_candidate(self, design: Any, action: dict[str, Any]) -> Any:
        content = design.content.model_copy(deep=True)
        actions = action["actions"] if action["type"] == "batch" else [action]
        for child in actions:
            self._apply_design_action(content, child)
        return content

    def _apply_design(
        self, owner: str, workspace_id: str, design: Any, action: dict[str, Any], *, candidate=None
    ) -> Any:
        content = candidate if candidate is not None else self._design_candidate(design, action)
        # Validate the complete transition and save once. No child action may
        # persist independently: later failures must leave design/history intact.
        return self.services.designs.replace(
            owner,
            workspace_id,
            SchemiiDesignReplace(
                expected_design_revision=design.revision,
                content=content,
            ),
        )

    def _apply_design_action(self, content: Any, action: dict[str, Any]) -> None:
        kind = action["type"]
        changed = False

        if kind == "add_table":
            column_ids = {
                column["name"]: self._new_id("column")
                for column in action["columns"]
            }
            primary_columns = {
                name
                for key in action.get("keys", [])
                if key["kind"] == "primary"
                for name in key["columns"]
            }
            content.tables.append(
                DesignTable(
                    id=self._new_id("table"),
                    name=action["name"],
                    columns=[
                        DesignColumn(
                            id=column_ids[column["name"]],
                            **{
                                **column,
                                "nullable": False
                                if column["name"] in primary_columns
                                else column.get("nullable", True),
                            },
                        )
                        for column in action["columns"]
                    ],
                    keys=[
                        DesignKeyConstraint(
                            id=self._new_id("key"),
                            name=key.get("name")
                            or self._key_name(
                                action["name"], key["columns"], key["kind"]
                            ),
                            kind=key["kind"],
                            column_ids=[column_ids[name] for name in key["columns"]],
                        )
                        for key in action.get("keys", [])
                    ],
                )
            )
            changed = True
        elif kind == "rename_table":
            for index, table in enumerate(content.tables):
                if table.id == action["table_id"]:
                    content.tables[index] = table.model_copy(
                        update={"name": action["name"]}
                    )
                    changed = True
                    break
        elif kind == "add_column":
            changed = self._put_table_member(
                content,
                action["table_id"],
                "columns",
                {"id": self._new_id("column"), **action["column"]},
            )
        elif kind == "update_column":
            for table_index, table in enumerate(content.tables):
                if table.id != action["table_id"]:
                    continue
                columns = list(table.columns)
                for column_index, column in enumerate(columns):
                    if column.id == action["column_id"]:
                        columns[column_index] = DesignColumn.model_validate(
                            {
                                **column.model_dump(),
                                **action["changes"],
                                "id": column.id,
                            }
                        )
                        content.tables[table_index] = table.model_copy(
                            update={"columns": columns}
                        )
                        changed = True
                        break
        elif kind == "put_top_level_object":
            changed = self._put_top_level(
                content, action["collection"], action["object"]
            )
        elif kind == "put_table_member":
            changed = self._put_table_member(
                content,
                action["table_id"],
                action["collection"],
                action["object"],
            )
        elif kind == "delete_object":
            changed = self._delete_object(content, action["object_id"])

        if not changed:
            raise AiServiceError(
                404,
                "ai_design_object_not_found",
                "The proposed design target no longer exists",
            )

    @staticmethod
    def _new_id(prefix: str) -> str:
        return f"{prefix}_{secrets.token_hex(16)}"

    @staticmethod
    def _key_name(table: str, columns: list[str], kind: str) -> str:
        suffix = "pkey" if kind == "primary" else "key"
        return f"{table}_{'_'.join(columns)}_{suffix}"[:63]

    def _put_top_level(
        self, content: Any, collection: str, raw_object: dict[str, Any]
    ) -> bool:
        model, prefix = _TOP_LEVEL_MODELS[collection]
        values = dict(raw_object)
        values.setdefault("id", self._new_id(prefix))
        candidate = model.model_validate(values)
        objects = list(getattr(content, collection))
        for index, current in enumerate(objects):
            if current.id == candidate.id:
                objects[index] = candidate
                setattr(content, collection, objects)
                return True
        objects.append(candidate)
        setattr(content, collection, objects)
        return True

    def _put_table_member(
        self,
        content: Any,
        table_id: str,
        collection: str,
        raw_object: dict[str, Any],
    ) -> bool:
        model, prefix = _TABLE_MEMBER_MODELS[collection]
        values = dict(raw_object)
        values.setdefault("id", self._new_id(prefix))
        candidate = model.model_validate(values)
        for table_index, table in enumerate(content.tables):
            if table.id != table_id:
                continue
            members = list(getattr(table, collection))
            for member_index, current in enumerate(members):
                if current.id == candidate.id:
                    members[member_index] = candidate
                    break
            else:
                members.append(candidate)
            content.tables[table_index] = table.model_copy(
                update={collection: members}
            )
            return True
        return False

    @staticmethod
    def _delete_object(content: Any, object_id: str) -> bool:
        for collection in _TOP_LEVEL_MODELS:
            objects = list(getattr(content, collection))
            kept = [item for item in objects if item.id != object_id]
            if len(kept) != len(objects):
                setattr(content, collection, kept)
                return True
        for table_index, table in enumerate(content.tables):
            for collection in _TABLE_MEMBER_MODELS:
                members = list(getattr(table, collection))
                kept = [item for item in members if item.id != object_id]
                if len(kept) != len(members):
                    content.tables[table_index] = table.model_copy(
                        update={collection: kept}
                    )
                    return True
        return False

    def _run_query(
        self, owner: str, workspace_id: str, workspace_revision: int, sql: str
    ) -> Any:
        execution = self.services.console.reserve(
            owner,
            workspace_id,
            ConsoleExecutionCreate(
                console_id=f"con_{secrets.token_hex(16)}",
                expected_workspace_revision=workspace_revision,
                expected_settings_revision=self.services.console.settings(owner).revision,
                mode="managed_read",
                statements=[sql],
            ),
        )
        self.services.console.run(owner, execution.id)
        completed = self.services.console.get(owner, workspace_id, execution.id)
        if completed.status != "succeeded" or not completed.results:
            raise AiServiceError(
                422,
                completed.error_code or "ai_query_failed",
                completed.error_message or "The query failed",
            )
        return completed

    def query_result(
        self,
        owner: str,
        chat_id: str,
        operation_id: str,
        cursor: str | None,
    ) -> SchemiiQueryResult:
        operation, action = self.repository.operation_action(
            owner, chat_id, operation_id
        )
        if operation.kind != "data_read" or operation.status != "succeeded":
            raise AiServiceError(
                409,
                "ai_query_result_unavailable",
                "This operation has no readable query result",
            )
        if "results" in (operation.result_summary or {}):
            bundle = reads.operation_context(self, owner, chat_id, operation_id, for_model=False)
            return SchemiiQueryResult(operation_id=operation_id, results=bundle["results"],
                rerun=any(item.get("rerun", False) for item in bundle["results"]))
        chat = self.repository.get_chat(owner, chat_id)
        summary = operation.result_summary or {}
        rerun = False
        try:
            page = self.services.console.page(
                owner,
                chat.workspace_id,
                summary["executionId"],
                summary["resultId"],
                cursor,
            )
        except Exception as error:
            if (
                getattr(error, "code", "") != "console_result_replay_required"
                or cursor is not None
            ):
                raise
            workspace = self.services.workspaces.get(owner, chat.workspace_id)
            execution = self._run_query(
                owner, chat.workspace_id, workspace.revision, action["sql"]
            )
            result = execution.results[0]
            self.repository.finish_operation(
                owner,
                chat_id,
                operation.id,
                status="succeeded",
                resource_kind="consoleResult",
                resource_id=result.id,
                result_summary={
                    "executionId": execution.id,
                    "resultId": result.id,
                    "rowCount": result.row_count,
                    "hasMore": result.has_more,
                },
            )
            page = self.services.console.page(
                owner, chat.workspace_id, execution.id, result.id, None
            )
            rerun = True
            self.repository.add_event(
                owner,
                chat_id,
                "freshness",
                {
                    "operationId": operation_id,
                    "message": (
                        "Query reran because its transient result had been released; "
                        "data may have changed."
                    ),
                },
            )

        rows = self._bounded_rows(page.rows)
        freshness_notice = None
        if rerun:
            freshness_notice = (
                "Query reran because its transient result had been released; "
                "data may have changed."
            )
        return SchemiiQueryResult(
            operation_id=operation_id,
            columns=[
                column.model_dump(by_alias=True) for column in page.columns
            ],
            rows=rows,
            next_cursor=page.next_cursor,
            rerun=rerun,
            freshness_notice=freshness_notice,
        )

    def _bounded_rows(self, values: list[list[Any]]) -> list[list[Any]]:
        rows: list[list[Any]] = []
        used = 0
        for row in values[: self.policy.result_context_rows]:
            size = len(
                json.dumps(row, separators=(",", ":"), default=str).encode("utf-8")
            )
            if rows and used + size > self.policy.result_context_bytes:
                break
            if size > self.policy.result_context_bytes:
                break
            rows.append(row)
            used += size
        return rows
