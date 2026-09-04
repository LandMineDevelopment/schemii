"""Durable assistant metadata. Query row values are deliberately absent."""

from __future__ import annotations

import copy
import json
import secrets
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterator, Protocol

from schemii.common.admin_config import AiPolicy

from .models import (
    AiCapabilities,
    SchemiiActivityEvent,
    SchemiiAiOperation,
    SchemiiAiSettings,
    SchemiiChat,
    SchemiiMessage,
    SchemiiProposal,
    SchemiiTurn,
)


class AiRepositoryError(RuntimeError):
    pass


class AiNotFoundError(AiRepositoryError):
    pass


class AiConflictError(AiRepositoryError):
    pass


class AiCapacityError(AiRepositoryError):
    def __init__(self, message: str, *, resource: str = "ai_turn", limit_name: str = "maximum_concurrent_turns", configured_limit: int = 0, observed_value: int | None = None) -> None:
        self.resource = resource
        self.limit_name = limit_name
        self.configured_limit = configured_limit
        self.observed_value = observed_value
        super().__init__(message)


_ROW_VALUE_KEYS = {"rows", "records", "cells", "rowvalues", "resultrows"}


def _require_row_free_metadata(value: Any) -> None:
    """Fail closed if an operation/event attempts to retain result row values."""
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).replace("_", "").casefold() in _ROW_VALUE_KEYS:
                raise ValueError("Assistant metadata cannot contain query result rows")
            _require_row_free_metadata(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _require_row_free_metadata(child)


class AiRepository(Protocol):
    def settings(self, owner_id: str) -> SchemiiAiSettings: ...
    def update_settings(self, owner_id: str, expected_revision: int, enabled: bool, provider_id: str | None, model_id: str | None, capabilities: AiCapabilities) -> SchemiiAiSettings: ...
    def save_preferences(self, owner_id: str, chat_id: str, expected_settings_revision: int, expected_chat_revision: int, provider_id: str, model_id: str, capabilities: AiCapabilities) -> tuple[SchemiiAiSettings, SchemiiChat, bool]: ...
    def list_chats(self, owner_id: str, workspace_id: str | None = None) -> list[SchemiiChat]: ...
    def create_chat(self, owner_id: str, workspace_id: str, title: str, provider_id: str, model_id: str, capabilities: AiCapabilities) -> SchemiiChat: ...
    def get_chat(self, owner_id: str, chat_id: str, *, include_deleted: bool = False) -> SchemiiChat: ...
    def update_chat(self, owner_id: str, chat_id: str, expected_revision: int, title: str) -> SchemiiChat: ...
    def update_chat_policy(self, owner_id: str, chat_id: str, expected_revision: int, capabilities: AiCapabilities) -> SchemiiChat: ...
    def delete_chat(self, owner_id: str, chat_id: str) -> SchemiiChat: ...
    def set_chat_runtime(self, owner_id: str, chat_id: str, *, status: str | None = None, external_session_id: str | None = None) -> SchemiiChat: ...
    def clear_chat_runtime(self, owner_id: str, chat_id: str) -> None: ...
    def external_session_id(self, owner_id: str, chat_id: str) -> str | None: ...
    def list_messages(self, owner_id: str, chat_id: str, limit: int) -> list[SchemiiMessage]: ...
    def create_turn(self, owner_id: str, chat_id: str, text: str, result_context_operation_id: str | None, maximum_total: int, maximum_per_owner: int, history_limit: int) -> tuple[SchemiiTurn, SchemiiMessage]: ...
    def get_turn(self, owner_id: str, chat_id: str, turn_id: str) -> SchemiiTurn: ...
    def claim_turn(self, owner_id: str, chat_id: str, turn_id: str) -> SchemiiTurn | None: ...
    def finish_turn(self, owner_id: str, chat_id: str, turn_id: str, text: str, *, rerun: bool = False, history_limit: int = 200) -> SchemiiTurn: ...
    def fail_turn(self, owner_id: str, chat_id: str, turn_id: str, code: str, message: str) -> SchemiiTurn: ...
    def cancel_turn(self, owner_id: str, chat_id: str, turn_id: str) -> tuple[SchemiiTurn, str | None]: ...
    def add_event(self, owner_id: str, chat_id: str, kind: str, payload: dict[str, Any]) -> SchemiiActivityEvent: ...
    def activity(self, owner_id: str, chat_id: str, after: int, limit: int = 200) -> list[SchemiiActivityEvent]: ...
    def create_proposal(self, owner_id: str, chat_id: str, turn_id: str, capability: str, action_type: str, summary: str, action: dict[str, Any], digest: str, workspace_revision: int, design_revision: int, destructive: bool, expires_at: datetime, chat_revision: int | None = None) -> SchemiiProposal: ...
    def list_proposals(self, owner_id: str, chat_id: str) -> list[SchemiiProposal]: ...
    def get_proposal(self, owner_id: str, chat_id: str, proposal_id: str) -> SchemiiProposal: ...
    def proposal_action(self, owner_id: str, chat_id: str, proposal_id: str) -> dict[str, Any]: ...
    def claim_proposal(self, owner_id: str, chat_id: str, proposal_id: str, revision: int, digest: str, now: datetime) -> tuple[SchemiiProposal, dict[str, Any]]: ...
    def dismiss_proposal(self, owner_id: str, chat_id: str, proposal_id: str) -> SchemiiProposal: ...
    def create_operation(self, owner_id: str, chat_id: str, proposal_id: str, kind: str) -> SchemiiAiOperation: ...
    def begin_operation(self, owner_id: str, chat_id: str, proposal_id: str, proposal_revision: int, digest: str, chat_revision: int, capability: str, now: datetime) -> tuple[SchemiiAiOperation, SchemiiProposal, dict[str, Any]]: ...
    def finish_operation(self, owner_id: str, chat_id: str, operation_id: str, *, status: str, resource_kind: str | None = None, resource_id: str | None = None, result_summary: dict[str, Any] | None = None, error_code: str | None = None, error_message: str | None = None) -> SchemiiAiOperation: ...
    def get_operation(self, owner_id: str, chat_id: str, operation_id: str) -> SchemiiAiOperation: ...
    def list_operations(self, owner_id: str, chat_id: str) -> list[SchemiiAiOperation]: ...
    def operation_action(self, owner_id: str, chat_id: str, operation_id: str) -> tuple[SchemiiAiOperation, dict[str, Any]]: ...
    def recover_interrupted(self) -> list[tuple[str, str, str]]: ...


def _now() -> datetime:
    return datetime.now(timezone.utc)


class InMemoryAiRepository:
    """Deterministic adapter used by unit tests and storage-free deployments."""

    def __init__(self, policy: AiPolicy | None = None) -> None:
        self._lock = threading.RLock()
        self._policy = policy or AiPolicy()
        self._settings: dict[str, SchemiiAiSettings] = {}
        self._chats: dict[str, tuple[str, SchemiiChat, str | None]] = {}
        self._messages: dict[str, list[SchemiiMessage]] = {}
        self._turns: dict[str, tuple[str, SchemiiTurn]] = {}
        self._events: dict[str, list[SchemiiActivityEvent]] = {}
        self._proposals: dict[str, tuple[str, SchemiiProposal, dict[str, Any]]] = {}
        self._operations: dict[str, tuple[str, SchemiiAiOperation]] = {}

    def settings(self, owner_id: str) -> SchemiiAiSettings:
        with self._lock:
            return self._settings.get(owner_id, SchemiiAiSettings(revision=1, enabled=True)).model_copy(deep=True)

    def update_settings(self, owner_id, expected_revision, enabled, provider_id, model_id, capabilities):
        with self._lock:
            current = self.settings(owner_id)
            if current.revision != expected_revision:
                raise AiConflictError("Assistant settings changed")
            saved = SchemiiAiSettings(revision=current.revision + 1, enabled=enabled, default_provider_id=provider_id, default_model_id=model_id, default_capabilities=capabilities)
            self._settings[owner_id] = saved
            return saved.model_copy(deep=True)

    def save_preferences(self, owner_id, chat_id, expected_settings_revision, expected_chat_revision, provider_id, model_id, capabilities):
        with self._lock:
            current_settings = self.settings(owner_id)
            current_chat = self.get_chat(owner_id, chat_id)
            if current_settings.revision != expected_settings_revision:
                raise AiConflictError("Assistant settings changed")
            if current_chat.revision != expected_chat_revision:
                raise AiConflictError("Chat changed")
            model_changed = current_chat.provider_id != provider_id or current_chat.model_id != model_id
            if model_changed:
                self._cleanup_locked(_now())
                count = sum(
                    owner == owner_id and item.workspace_id == current_chat.workspace_id
                    for owner, item, _ in self._chats.values()
                )
                if count >= self._policy.maximum_chats_per_workspace:
                    raise AiCapacityError(
                        "This workspace has reached the configured conversation limit. Delete an old conversation before changing models.",
                        resource="ai_chat",
                        limit_name="maximum_chats_per_workspace",
                        configured_limit=self._policy.maximum_chats_per_workspace,
                        observed_value=count,
                    )
                now = _now()
                saved_chat = SchemiiChat(
                    id=f"chat_{secrets.token_hex(16)}",
                    workspace_id=current_chat.workspace_id,
                    revision=1,
                    title="New conversation",
                    provider_id=provider_id,
                    model_id=model_id,
                    capabilities=capabilities,
                    status="idle",
                    created_at=now,
                    updated_at=now,
                )
                self._chats[saved_chat.id] = (owner_id, saved_chat, None)
                self._messages[saved_chat.id] = []
                self._events[saved_chat.id] = []
            elif current_chat.capabilities != capabilities:
                saved_chat = self._replace_chat(
                    owner_id,
                    chat_id,
                    current_chat.model_copy(
                        update={
                            "revision": current_chat.revision + 1,
                            "capabilities": capabilities,
                            "updated_at": _now(),
                        }
                    ),
                )
                for proposal_id, (owner, proposal, action) in list(self._proposals.items()):
                    if owner == owner_id and proposal.chat_id == chat_id and proposal.status == "pending":
                        self._proposals[proposal_id] = (
                            owner,
                            proposal.model_copy(update={"revision": proposal.revision + 1, "status": "dismissed"}),
                            action,
                        )
            else:
                saved_chat = current_chat
            saved_settings = SchemiiAiSettings(
                revision=current_settings.revision + 1,
                enabled=True,
                default_provider_id=provider_id,
                default_model_id=model_id,
                default_capabilities=capabilities,
            )
            self._settings[owner_id] = saved_settings
            return saved_settings.model_copy(deep=True), saved_chat.model_copy(deep=True), model_changed

    def list_chats(self, owner_id, workspace_id=None):
        with self._lock:
            return [chat.model_copy(deep=True) for owner, chat, _ in self._chats.values() if owner == owner_id and chat.status != "deleted" and (workspace_id is None or chat.workspace_id == workspace_id)]

    def create_chat(self, owner_id, workspace_id, title, provider_id, model_id, capabilities):
        now = _now(); chat = SchemiiChat(id=f"chat_{secrets.token_hex(16)}", workspace_id=workspace_id, revision=1, title=title, provider_id=provider_id, model_id=model_id, capabilities=capabilities, status="idle", created_at=now, updated_at=now)
        with self._lock:
            self._cleanup_locked(now)
            count = sum(
                owner == owner_id and item.workspace_id == workspace_id
                for owner, item, _ in self._chats.values()
            )
            if count >= self._policy.maximum_chats_per_workspace:
                raise AiCapacityError(
                    "This workspace has reached the configured conversation limit. Delete an old conversation before starting another.",
                    resource="ai_chat",
                    limit_name="maximum_chats_per_workspace",
                    configured_limit=self._policy.maximum_chats_per_workspace,
                    observed_value=count,
                )
            self._chats[chat.id] = (owner_id, chat, None); self._messages[chat.id] = []; self._events[chat.id] = []
        return chat.model_copy(deep=True)

    def get_chat(self, owner_id, chat_id, *, include_deleted=False):
        with self._lock:
            value = self._chats.get(chat_id)
            if value is None or value[0] != owner_id or (value[1].status == "deleted" and not include_deleted): raise AiNotFoundError("Chat was not found")
            return value[1].model_copy(deep=True)

    def external_session_id(self, owner_id: str, chat_id: str) -> str | None:
        self.get_chat(owner_id, chat_id)
        return self._chats[chat_id][2]

    def _replace_chat(self, owner_id, chat_id, chat, external=None):
        prior = self._chats[chat_id]
        self._chats[chat_id] = (owner_id, chat, prior[2] if external is None else external)
        return chat.model_copy(deep=True)

    def update_chat(self, owner_id, chat_id, expected_revision, title):
        with self._lock:
            current = self.get_chat(owner_id, chat_id)
            if current.revision != expected_revision: raise AiConflictError("Chat changed")
            return self._replace_chat(owner_id, chat_id, current.model_copy(update={"revision": current.revision + 1, "title": title, "updated_at": _now()}))

    def update_chat_policy(self, owner_id, chat_id, expected_revision, capabilities):
        with self._lock:
            current = self.get_chat(owner_id, chat_id)
            if current.revision != expected_revision: raise AiConflictError("Chat changed")
            updated = self._replace_chat(owner_id, chat_id, current.model_copy(update={"revision": current.revision + 1, "capabilities": capabilities, "updated_at": _now()}))
            for proposal_id, (owner, proposal, action) in list(self._proposals.items()):
                if owner == owner_id and proposal.chat_id == chat_id and proposal.status == "pending":
                    self._proposals[proposal_id] = (owner, proposal.model_copy(update={"revision": proposal.revision + 1, "status": "dismissed"}), action)
            return updated

    def delete_chat(self, owner_id, chat_id):
        with self._lock:
            current = self.get_chat(owner_id, chat_id)
            deleted = current.model_copy(update={"revision": current.revision + 1, "status": "deleted", "updated_at": _now()})
            self._chats.pop(chat_id, None)
            self._messages.pop(chat_id, None)
            self._events.pop(chat_id, None)
            for turn_id, (_, turn) in list(self._turns.items()):
                if turn.chat_id == chat_id:
                    self._turns.pop(turn_id, None)
            for proposal_id, (_, proposal, _) in list(self._proposals.items()):
                if proposal.chat_id == chat_id:
                    self._proposals.pop(proposal_id, None)
            for operation_id, (_, operation) in list(self._operations.items()):
                if operation.chat_id == chat_id:
                    self._operations.pop(operation_id, None)
            return deleted

    def set_chat_runtime(self, owner_id, chat_id, *, status=None, external_session_id=None):
        with self._lock:
            current = self.get_chat(owner_id, chat_id)
            updated = current.model_copy(update={"status": status or current.status, "updated_at": _now()})
            return self._replace_chat(owner_id, chat_id, updated, external_session_id)

    def clear_chat_runtime(self, owner_id, chat_id):
        with self._lock:
            current = self.get_chat(owner_id, chat_id)
            self._chats[chat_id] = (owner_id, current, None)

    def list_messages(self, owner_id, chat_id, limit):
        self.get_chat(owner_id, chat_id)
        with self._lock: return [message.model_copy(deep=True) for message in self._messages.get(chat_id, [])[-limit:]]

    def create_turn(self, owner_id, chat_id, text, result_context_operation_id, maximum_total, maximum_per_owner, history_limit):
        with self._lock:
            chat = self.get_chat(owner_id, chat_id)
            active = [(owner, turn) for owner, turn in self._turns.values() if turn.status in {"queued", "running"}]
            owner_active = sum(owner == owner_id for owner, _ in active)
            if len(active) >= maximum_total:
                raise AiCapacityError("Assistant turn capacity is currently full", configured_limit=maximum_total, observed_value=len(active))
            if owner_active >= maximum_per_owner:
                raise AiCapacityError("Your assistant turn capacity is currently full", limit_name="maximum_concurrent_turns_per_user", configured_limit=maximum_per_owner, observed_value=owner_active)
            if any(turn.chat_id == chat_id for _, turn in active): raise AiConflictError("This chat already has an active turn")
            now = _now(); turn = SchemiiTurn(id=f"turn_{secrets.token_hex(16)}", chat_id=chat_id, status="queued", result_context_operation_id=result_context_operation_id, created_at=now)
            sequence = self._messages[chat_id][-1].sequence + 1 if self._messages[chat_id] else 1
            message = SchemiiMessage(id=f"msg_{secrets.token_hex(16)}", chat_id=chat_id, turn_id=turn.id, sequence=sequence, role="user", text=text, created_at=now)
            self._turns[turn.id] = (owner_id, turn); self._messages[chat_id].append(message)
            self._prune_history_locked(chat_id, history_limit)
            self._replace_chat(owner_id, chat_id, chat.model_copy(update={"status": "working", "updated_at": now}))
            return turn.model_copy(deep=True), message.model_copy(deep=True)

    def get_turn(self, owner_id, chat_id, turn_id):
        self.get_chat(owner_id, chat_id)
        with self._lock:
            value = self._turns.get(turn_id)
            if value is None or value[0] != owner_id or value[1].chat_id != chat_id: raise AiNotFoundError("Turn was not found")
            return value[1].model_copy(deep=True)

    def claim_turn(self, owner_id, chat_id, turn_id):
        with self._lock:
            turn = self.get_turn(owner_id, chat_id, turn_id)
            if turn.status != "queued": return None
            turn = turn.model_copy(update={"status": "running", "started_at": _now()}); self._turns[turn_id] = (owner_id, turn); return turn

    def finish_turn(self, owner_id, chat_id, turn_id, text, *, rerun=False, history_limit=200):
        with self._lock:
            turn = self.get_turn(owner_id, chat_id, turn_id).model_copy(update={"status": "succeeded", "completed_at": _now(), "result_context_rerun": rerun})
            self._turns[turn_id] = (owner_id, turn)
            sequence = (self._messages[chat_id][-1].sequence if self._messages[chat_id] else 0) + 1
            self._messages[chat_id].append(SchemiiMessage(id=f"msg_{secrets.token_hex(16)}", chat_id=chat_id, turn_id=turn_id, sequence=sequence, role="assistant", text=text, created_at=_now()))
            self._prune_history_locked(chat_id, history_limit)
            self.set_chat_runtime(owner_id, chat_id, status="idle")
            return turn.model_copy(deep=True)

    def fail_turn(self, owner_id, chat_id, turn_id, code, message):
        with self._lock:
            turn = self.get_turn(owner_id, chat_id, turn_id).model_copy(update={"status": "failed", "error_code": code, "error_message": message, "completed_at": _now()}); self._turns[turn_id] = (owner_id, turn); self.set_chat_runtime(owner_id, chat_id, status="failed"); return turn

    def cancel_turn(self, owner_id, chat_id, turn_id):
        with self._lock:
            turn = self.get_turn(owner_id, chat_id, turn_id)
            if turn.status not in {"queued", "running"}:
                raise AiConflictError("Turn is no longer active")
            cancelled = turn.model_copy(update={"status": "cancelled", "completed_at": _now()})
            self._turns[turn_id] = (owner_id, cancelled)
            session_id = self._chats[chat_id][2]
            self.set_chat_runtime(owner_id, chat_id, status="idle")
            return cancelled.model_copy(deep=True), session_id

    def add_event(self, owner_id, chat_id, kind, payload):
        _require_row_free_metadata(payload)
        self.get_chat(owner_id, chat_id)
        with self._lock:
            sequence = self._events[chat_id][-1].sequence + 1 if self._events[chat_id] else 1
            event = SchemiiActivityEvent(sequence=sequence, kind=kind, payload=copy.deepcopy(payload), created_at=_now())
            self._events[chat_id].append(event)
            self._events[chat_id] = self._events[chat_id][-self._policy.activity_history_limit :]
            return event

    def activity(self, owner_id, chat_id, after, limit=200):
        self.get_chat(owner_id, chat_id)
        with self._lock: return [event.model_copy(deep=True) for event in self._events[chat_id] if event.sequence > after][:limit]

    def create_proposal(self, owner_id, chat_id, turn_id, capability, action_type, summary, action, digest, workspace_revision, design_revision, destructive, expires_at, chat_revision=None):
        self.get_turn(owner_id, chat_id, turn_id); now = _now(); proposal = SchemiiProposal(id=f"prop_{secrets.token_hex(16)}", chat_id=chat_id, turn_id=turn_id, revision=1, capability=capability, action_type=action_type, summary=summary, details=copy.deepcopy(action), digest=digest, destructive=destructive, expected_workspace_revision=workspace_revision, expected_design_revision=design_revision, status="pending", created_at=now, expires_at=expires_at)
        with self._lock:
            chat = self.get_chat(owner_id, chat_id)
            if chat_revision is not None and chat.revision != chat_revision:
                raise AiConflictError("Conversation permissions changed while the proposal was being prepared")
            if not getattr(chat.capabilities, capability, False):
                raise AiConflictError("The proposal's required permission is no longer enabled")
            self._proposals[proposal.id] = (owner_id, proposal, copy.deepcopy(action))
        return proposal

    def list_proposals(self, owner_id, chat_id):
        self.get_chat(owner_id, chat_id)
        with self._lock:
            now = _now()
            for proposal_id, (proposal_owner, proposal, action) in list(self._proposals.items()):
                if proposal_owner == owner_id and proposal.chat_id == chat_id and proposal.status == "pending" and proposal.expires_at <= now:
                    self._proposals[proposal_id] = (
                        proposal_owner,
                        proposal.model_copy(update={"revision": proposal.revision + 1, "status": "expired"}),
                        action,
                    )
            return [p.model_copy(deep=True) for owner, p, _ in self._proposals.values() if owner == owner_id and p.chat_id == chat_id]

    def get_proposal(self, owner_id, chat_id, proposal_id):
        self.get_chat(owner_id, chat_id)
        with self._lock:
            value = self._proposals.get(proposal_id)
            if value is None or value[0] != owner_id or value[1].chat_id != chat_id: raise AiNotFoundError("Proposal was not found")
            return value[1].model_copy(deep=True)

    def proposal_action(self, owner_id, chat_id, proposal_id):
        self.get_proposal(owner_id, chat_id, proposal_id); return copy.deepcopy(self._proposals[proposal_id][2])

    def claim_proposal(self, owner_id, chat_id, proposal_id, revision, digest, now):
        with self._lock:
            proposal = self.get_proposal(owner_id, chat_id, proposal_id)
            if proposal.revision != revision or proposal.digest != digest: raise AiConflictError("Proposal changed")
            if proposal.expires_at <= now: raise AiConflictError("Proposal expired")
            if proposal.status != "pending": raise AiConflictError("Proposal is no longer pending")
            updated = proposal.model_copy(update={"revision": proposal.revision + 1, "status": "executing"}); action = self._proposals[proposal_id][2]; self._proposals[proposal_id] = (owner_id, updated, action); return updated, copy.deepcopy(action)

    def dismiss_proposal(self, owner_id, chat_id, proposal_id):
        with self._lock:
            proposal = self.get_proposal(owner_id, chat_id, proposal_id)
            if proposal.status != "pending": raise AiConflictError("Proposal is no longer pending")
            updated = proposal.model_copy(update={"revision": proposal.revision + 1, "status": "dismissed"}); self._proposals[proposal_id] = (owner_id, updated, self._proposals[proposal_id][2]); return updated

    def create_operation(self, owner_id, chat_id, proposal_id, kind):
        now = _now(); operation = SchemiiAiOperation(id=f"aop_{secrets.token_hex(16)}", chat_id=chat_id, proposal_id=proposal_id, revision=1, kind=kind, status="running", created_at=now, updated_at=now)
        with self._lock: self._operations[operation.id] = (owner_id, operation)
        return operation

    def begin_operation(self, owner_id, chat_id, proposal_id, proposal_revision, digest, chat_revision, capability, now):
        with self._lock:
            chat = self.get_chat(owner_id, chat_id)
            if chat.revision != chat_revision:
                raise AiConflictError("Conversation permissions changed; review the proposal again")
            if not getattr(chat.capabilities, capability, False):
                raise AiConflictError(
                    f"This proposal requires the {capability.replace('_', ' ')} permission"
                )
            proposal, action = self.claim_proposal(
                owner_id, chat_id, proposal_id, proposal_revision, digest, now
            )
            operation = self.create_operation(
                owner_id, chat_id, proposal_id, proposal.action_type
            )
            return operation, proposal, action

    def finish_operation(self, owner_id, chat_id, operation_id, **changes):
        _require_row_free_metadata(changes.get("result_summary"))
        with self._lock:
            operation = self.get_operation(owner_id, chat_id, operation_id); updated = operation.model_copy(update={**changes, "revision": operation.revision + 1, "updated_at": _now()}); self._operations[operation_id] = (owner_id, updated)
            proposal_owner, proposal, action = self._proposals[operation.proposal_id]; proposal_status = "succeeded" if updated.status == "succeeded" else "failed"; self._proposals[proposal.id] = (proposal_owner, proposal.model_copy(update={"revision": proposal.revision + 1, "status": proposal_status}), action)
            return updated

    def get_operation(self, owner_id, chat_id, operation_id):
        self.get_chat(owner_id, chat_id)
        with self._lock:
            value = self._operations.get(operation_id)
            if value is None or value[0] != owner_id or value[1].chat_id != chat_id: raise AiNotFoundError("Operation was not found")
            return value[1].model_copy(deep=True)

    def list_operations(self, owner_id, chat_id):
        self.get_chat(owner_id, chat_id)
        with self._lock:
            operations = [
                operation.model_copy(deep=True)
                for operation_owner, operation in self._operations.values()
                if operation_owner == owner_id and operation.chat_id == chat_id
            ]
            return operations[-100:]

    def operation_action(self, owner_id, chat_id, operation_id):
        operation = self.get_operation(owner_id, chat_id, operation_id); return operation, self.proposal_action(owner_id, chat_id, operation.proposal_id)

    def recover_interrupted(self):
        with self._lock:
            now = _now()
            sessions = [
                (owner, chat.id, external)
                for owner, chat, external in self._chats.values()
                if external
            ]
            for turn_id, (owner, turn) in list(self._turns.items()):
                if turn.status in {"queued", "running"}:
                    self._turns[turn_id] = (owner, turn.model_copy(update={
                        "status": "failed",
                        "error_code": "ai_server_restarted",
                        "error_message": "The assistant server restarted before this turn completed",
                        "completed_at": now,
                    }))
            for operation_id, (owner, operation) in list(self._operations.items()):
                if operation.status != "running":
                    continue
                self._operations[operation_id] = (owner, operation.model_copy(update={
                    "revision": operation.revision + 1,
                    "status": "uncertain",
                    "error_code": "ai_server_restarted",
                    "error_message": "The server restarted while this operation was running",
                    "updated_at": now,
                }))
                proposal_owner, proposal, action = self._proposals[operation.proposal_id]
                self._proposals[proposal.id] = (
                    proposal_owner,
                    proposal.model_copy(update={"revision": proposal.revision + 1, "status": "failed"}),
                    action,
                )
            for chat_id, (owner, chat, external) in list(self._chats.items()):
                status = "failed" if chat.status == "working" else chat.status
                self._chats[chat_id] = (owner, chat.model_copy(update={"status": status, "updated_at": now}), external)
            self._cleanup_locked(now)
            return sessions

    def _cleanup_locked(self, now):
        cutoff = now - timedelta(days=self._policy.chat_retention_days)
        for chat_id, (owner, chat, _) in list(self._chats.items()):
            if chat.status != "working" and chat.updated_at < cutoff:
                self.delete_chat(owner, chat_id)

    def _prune_history_locked(self, chat_id, limit):
        messages = self._messages.get(chat_id, [])
        if len(messages) <= limit:
            return
        removed = messages[:-limit]
        self._messages[chat_id] = messages[-limit:]
        retained_turns = {message.turn_id for message in self._messages[chat_id] if message.turn_id}
        removed_turns = {message.turn_id for message in removed if message.turn_id} - retained_turns
        for turn_id in removed_turns:
            turn = self._turns.get(turn_id)
            if turn is None or turn[1].status in {"queued", "running"}:
                continue
            self._turns.pop(turn_id, None)
            proposal_ids = [proposal_id for proposal_id, (_, proposal, _) in self._proposals.items() if proposal.turn_id == turn_id]
            for proposal_id in proposal_ids:
                self._proposals.pop(proposal_id, None)
                for operation_id, (_, operation) in list(self._operations.items()):
                    if operation.proposal_id == proposal_id:
                        self._operations.pop(operation_id, None)


class PostgresAiRepository:
    """PostgreSQL adapter retaining authority and SQL, never query rows."""

    def __init__(self, connection_factory: Callable[[], Any], policy: AiPolicy | None = None) -> None:
        self._connection_factory = connection_factory
        self._policy = policy or AiPolicy()

    @contextmanager
    def _transaction(self) -> Iterator[Any]:
        connection = self._connection_factory()
        try:
            with connection.transaction(): yield connection
        except Exception: connection.rollback(); raise
        finally: connection.close()

    @staticmethod
    def _chat(row) -> SchemiiChat:
        return SchemiiChat(id=row["id"], workspace_id=row["workspace_id"], revision=row["revision"], title=row["title"], provider_id=row["provider_id"], model_id=row["model_id"], capabilities=AiCapabilities.model_validate(row["capabilities"]), status=row["status"], created_at=row["created_at"], updated_at=row["updated_at"])

    @staticmethod
    def _turn(row) -> SchemiiTurn:
        return SchemiiTurn(id=row["id"], chat_id=row["chat_id"], status=row["status"], result_context_operation_id=row["result_context_operation_id"], result_context_rerun=row["result_context_rerun"], error_code=row["error_code"], error_message=row["error_message"], created_at=row["created_at"], started_at=row["started_at"], completed_at=row["completed_at"])

    @staticmethod
    def _message(row) -> SchemiiMessage:
        return SchemiiMessage.model_validate(row)

    @staticmethod
    def _proposal(row) -> SchemiiProposal:
        return SchemiiProposal(id=row["id"], chat_id=row["chat_id"], turn_id=row["turn_id"], revision=row["revision"], capability=row["capability"], action_type=row["action_type"], summary=row["summary"], details=row["action"], digest=row["digest"], destructive=row["destructive"], expected_workspace_revision=row["expected_workspace_revision"], expected_design_revision=row["expected_design_revision"], status=row["status"], created_at=row["created_at"], expires_at=row["expires_at"])

    @staticmethod
    def _operation(row) -> SchemiiAiOperation:
        return SchemiiAiOperation(id=row["id"], chat_id=row["chat_id"], proposal_id=row["proposal_id"], revision=row["revision"], kind=row["kind"], status=row["status"], resource_kind=row["resource_kind"], resource_id=row["resource_id"], result_summary=row["result_summary"], error_code=row["error_code"], error_message=row["error_message"], created_at=row["created_at"], updated_at=row["updated_at"])

    def settings(self, owner_id):
        with self._transaction() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT * FROM schemii.ai_settings WHERE owner_id = %s", (owner_id,)); row = cursor.fetchone()
            if row is None: return SchemiiAiSettings(revision=1, enabled=True)
            return SchemiiAiSettings(revision=row["revision"], enabled=row["enabled"], default_provider_id=row["default_provider_id"], default_model_id=row["default_model_id"], default_capabilities=AiCapabilities.model_validate(row["capabilities"]))

    def update_settings(self, owner_id, expected_revision, enabled, provider_id, model_id, capabilities):
        with self._transaction() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"ai-settings:{owner_id}",))
            cursor.execute("SELECT revision FROM schemii.ai_settings WHERE owner_id=%s FOR UPDATE", (owner_id,)); row = cursor.fetchone(); current = row["revision"] if row else 1
            if current != expected_revision: raise AiConflictError("Assistant settings changed")
            cursor.execute("INSERT INTO schemii.ai_settings (owner_id, revision, enabled, default_provider_id, default_model_id, capabilities) VALUES (%s,%s,%s,%s,%s,%s::jsonb) ON CONFLICT (owner_id) DO UPDATE SET revision=EXCLUDED.revision, enabled=EXCLUDED.enabled, default_provider_id=EXCLUDED.default_provider_id, default_model_id=EXCLUDED.default_model_id, capabilities=EXCLUDED.capabilities, updated_at=clock_timestamp() RETURNING *", (owner_id, current + 1, enabled, provider_id, model_id, capabilities.model_dump_json(by_alias=True))); saved = cursor.fetchone()
        return SchemiiAiSettings(revision=saved["revision"], enabled=saved["enabled"], default_provider_id=saved["default_provider_id"], default_model_id=saved["default_model_id"], default_capabilities=AiCapabilities.model_validate(saved["capabilities"]))

    def save_preferences(self, owner_id, chat_id, expected_settings_revision, expected_chat_revision, provider_id, model_id, capabilities):
        with self._transaction() as connection, connection.cursor() as cursor:
            self._cleanup(cursor)
            cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"ai-settings:{owner_id}",))
            cursor.execute("SELECT * FROM schemii.ai_settings WHERE owner_id=%s FOR UPDATE", (owner_id,))
            settings_row = cursor.fetchone()
            settings_revision = settings_row["revision"] if settings_row else 1
            if settings_revision != expected_settings_revision:
                raise AiConflictError("Assistant settings changed")
            cursor.execute("SELECT * FROM schemii.ai_chats WHERE owner_id=%s AND id=%s AND status<>'deleted' FOR UPDATE", (owner_id, chat_id))
            chat_row = cursor.fetchone()
            if chat_row is None or chat_row["revision"] != expected_chat_revision:
                raise AiConflictError("Chat changed or was deleted")
            current_chat = self._chat(chat_row)
            model_changed = current_chat.provider_id != provider_id or current_chat.model_id != model_id
            if model_changed:
                cursor.execute("SELECT count(*) AS total FROM schemii.ai_chats WHERE owner_id=%s AND workspace_id=%s AND status<>'deleted'", (owner_id, current_chat.workspace_id))
                count = cursor.fetchone()["total"]
                if count >= self._policy.maximum_chats_per_workspace:
                    raise AiCapacityError(
                        "This workspace has reached the configured conversation limit. Delete an old conversation before changing models.",
                        resource="ai_chat",
                        limit_name="maximum_chats_per_workspace",
                        configured_limit=self._policy.maximum_chats_per_workspace,
                        observed_value=count,
                    )
                new_chat_id = f"chat_{secrets.token_hex(16)}"
                cursor.execute(
                    "INSERT INTO schemii.ai_chats (id,owner_id,workspace_id,title,provider_id,model_id,capabilities) VALUES (%s,%s,%s,'New conversation',%s,%s,%s::jsonb) RETURNING *",
                    (new_chat_id, owner_id, current_chat.workspace_id, provider_id, model_id, capabilities.model_dump_json(by_alias=True)),
                )
                saved_chat = self._chat(cursor.fetchone())
            elif current_chat.capabilities != capabilities:
                cursor.execute(
                    "UPDATE schemii.ai_chats SET revision=revision+1,capabilities=%s::jsonb,updated_at=clock_timestamp() WHERE owner_id=%s AND id=%s AND revision=%s RETURNING *",
                    (capabilities.model_dump_json(by_alias=True), owner_id, chat_id, expected_chat_revision),
                )
                saved_chat = self._chat(cursor.fetchone())
                cursor.execute("UPDATE schemii.ai_proposals SET revision=revision+1,status='dismissed' WHERE owner_id=%s AND chat_id=%s AND status='pending'", (owner_id, chat_id))
            else:
                saved_chat = current_chat
            cursor.execute(
                "INSERT INTO schemii.ai_settings (owner_id,revision,enabled,default_provider_id,default_model_id,capabilities) VALUES (%s,%s,TRUE,%s,%s,%s::jsonb) ON CONFLICT (owner_id) DO UPDATE SET revision=EXCLUDED.revision,enabled=TRUE,default_provider_id=EXCLUDED.default_provider_id,default_model_id=EXCLUDED.default_model_id,capabilities=EXCLUDED.capabilities,updated_at=clock_timestamp() RETURNING *",
                (owner_id, settings_revision + 1, provider_id, model_id, capabilities.model_dump_json(by_alias=True)),
            )
            saved_settings_row = cursor.fetchone()
        saved_settings = SchemiiAiSettings(
            revision=saved_settings_row["revision"],
            enabled=saved_settings_row["enabled"],
            default_provider_id=saved_settings_row["default_provider_id"],
            default_model_id=saved_settings_row["default_model_id"],
            default_capabilities=AiCapabilities.model_validate(saved_settings_row["capabilities"]),
        )
        return saved_settings, saved_chat, model_changed

    def list_chats(self, owner_id, workspace_id=None):
        with self._transaction() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT * FROM schemii.ai_chats WHERE owner_id=%s AND status<>'deleted' AND (%s::text IS NULL OR workspace_id=%s) ORDER BY updated_at DESC", (owner_id, workspace_id, workspace_id)); return [self._chat(row) for row in cursor.fetchall()]

    def create_chat(self, owner_id, workspace_id, title, provider_id, model_id, capabilities):
        chat_id=f"chat_{secrets.token_hex(16)}"
        with self._transaction() as connection, connection.cursor() as cursor:
            self._cleanup(cursor)
            cursor.execute(
                "SELECT count(*) AS total FROM schemii.ai_chats WHERE owner_id=%s AND workspace_id=%s AND status<>'deleted'",
                (owner_id, workspace_id),
            )
            if cursor.fetchone()["total"] >= self._policy.maximum_chats_per_workspace:
                raise AiCapacityError(
                    "This workspace has reached the configured conversation limit. Delete an old conversation before starting another.",
                    resource="ai_chat",
                    limit_name="maximum_chats_per_workspace",
                    configured_limit=self._policy.maximum_chats_per_workspace,
                    observed_value=self._policy.maximum_chats_per_workspace,
                )
            cursor.execute("INSERT INTO schemii.ai_chats (id,owner_id,workspace_id,title,provider_id,model_id,capabilities) VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb) RETURNING *", (chat_id,owner_id,workspace_id,title,provider_id,model_id,capabilities.model_dump_json(by_alias=True))); return self._chat(cursor.fetchone())

    def get_chat(self, owner_id, chat_id, *, include_deleted=False):
        with self._transaction() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT * FROM schemii.ai_chats WHERE owner_id=%s AND id=%s", (owner_id,chat_id)); row=cursor.fetchone()
            if row is None or (row["status"]=="deleted" and not include_deleted): raise AiNotFoundError("Chat was not found")
            return self._chat(row)

    def external_session_id(self, owner_id, chat_id):
        with self._transaction() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT external_session_id FROM schemii.ai_chats WHERE owner_id=%s AND id=%s AND status<>'deleted'",(owner_id,chat_id)); row=cursor.fetchone()
            if row is None: raise AiNotFoundError("Chat was not found")
            return row["external_session_id"]

    def update_chat(self, owner_id, chat_id, expected_revision, title):
        with self._transaction() as connection, connection.cursor() as cursor:
            cursor.execute("UPDATE schemii.ai_chats SET revision=revision+1,title=%s,updated_at=clock_timestamp() WHERE owner_id=%s AND id=%s AND revision=%s AND status<>'deleted' RETURNING *",(title,owner_id,chat_id,expected_revision)); row=cursor.fetchone()
            if row is None: raise AiConflictError("Chat changed or was deleted")
            return self._chat(row)

    def update_chat_policy(self, owner_id, chat_id, expected_revision, capabilities):
        with self._transaction() as connection, connection.cursor() as cursor:
            cursor.execute("UPDATE schemii.ai_chats SET revision=revision+1,capabilities=%s::jsonb,updated_at=clock_timestamp() WHERE owner_id=%s AND id=%s AND revision=%s AND status<>'deleted' RETURNING *",(capabilities.model_dump_json(by_alias=True),owner_id,chat_id,expected_revision)); row=cursor.fetchone()
            if row is None: raise AiConflictError("Chat changed or was deleted")
            cursor.execute("UPDATE schemii.ai_proposals SET revision=revision+1,status='dismissed' WHERE owner_id=%s AND chat_id=%s AND status='pending'",(owner_id,chat_id)); return self._chat(row)

    def delete_chat(self, owner_id, chat_id):
        with self._transaction() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT * FROM schemii.ai_chats WHERE owner_id=%s AND id=%s AND status<>'deleted' FOR UPDATE", (owner_id, chat_id))
            row = cursor.fetchone()
            if row is None:
                raise AiNotFoundError("Chat was not found")
            deleted = self._chat(row).model_copy(update={"revision": row["revision"] + 1, "status": "deleted", "updated_at": _now()})
            cursor.execute("DELETE FROM schemii.ai_chats WHERE owner_id=%s AND id=%s", (owner_id, chat_id))
            return deleted

    def set_chat_runtime(self, owner_id, chat_id, *, status=None, external_session_id=None):
        with self._transaction() as connection, connection.cursor() as cursor:
            cursor.execute("UPDATE schemii.ai_chats SET status=COALESCE(%s,status), external_session_id=COALESCE(%s,external_session_id), updated_at=clock_timestamp() WHERE owner_id=%s AND id=%s AND status<>'deleted' RETURNING *",(status,external_session_id,owner_id,chat_id)); row=cursor.fetchone()
            if row is None: raise AiNotFoundError("Chat was not found")
            return self._chat(row)

    def clear_chat_runtime(self, owner_id, chat_id):
        with self._transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "UPDATE schemii.ai_chats SET external_session_id=NULL WHERE owner_id=%s AND id=%s AND status<>'deleted'",
                (owner_id, chat_id),
            )

    def list_messages(self, owner_id, chat_id, limit):
        self.get_chat(owner_id,chat_id)
        with self._transaction() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT id,chat_id,turn_id,sequence,role,text,created_at FROM (SELECT * FROM schemii.ai_messages WHERE chat_id=%s ORDER BY sequence DESC LIMIT %s) recent ORDER BY sequence",(chat_id,limit)); return [self._message(row) for row in cursor.fetchall()]

    def create_turn(self, owner_id, chat_id, text, result_context_operation_id, maximum_total, maximum_per_owner, history_limit):
        turn_id=f"turn_{secrets.token_hex(16)}"; message_id=f"msg_{secrets.token_hex(16)}"; now=_now()
        with self._transaction() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT * FROM schemii.ai_chats WHERE owner_id=%s AND id=%s AND status<>'deleted' FOR UPDATE",(owner_id,chat_id)); chat=cursor.fetchone()
            if chat is None: raise AiNotFoundError("Chat was not found")
            cursor.execute("SELECT count(*) AS total, count(*) FILTER (WHERE owner_id=%s) AS owner_total FROM schemii.ai_turns WHERE status IN ('queued','running')",(owner_id,)); counts=cursor.fetchone()
            if counts["total"] >= maximum_total:
                raise AiCapacityError("Assistant turn capacity is currently full", configured_limit=maximum_total, observed_value=counts["total"])
            if counts["owner_total"] >= maximum_per_owner:
                raise AiCapacityError("Your assistant turn capacity is currently full", limit_name="maximum_concurrent_turns_per_user", configured_limit=maximum_per_owner, observed_value=counts["owner_total"])
            cursor.execute("SELECT 1 FROM schemii.ai_turns WHERE chat_id=%s AND status IN ('queued','running')",(chat_id,))
            if cursor.fetchone(): raise AiConflictError("This chat already has an active turn")
            cursor.execute("INSERT INTO schemii.ai_turns (id,chat_id,owner_id,status,result_context_operation_id) VALUES (%s,%s,%s,'queued',%s) RETURNING *",(turn_id,chat_id,owner_id,result_context_operation_id)); turn=self._turn(cursor.fetchone())
            cursor.execute("SELECT COALESCE(max(sequence),0)+1 AS next FROM schemii.ai_messages WHERE chat_id=%s",(chat_id,)); sequence=cursor.fetchone()["next"]
            cursor.execute("INSERT INTO schemii.ai_messages (id,chat_id,turn_id,sequence,role,text) VALUES (%s,%s,%s,%s,'user',%s) RETURNING id,chat_id,turn_id,sequence,role,text,created_at",(message_id,chat_id,turn_id,sequence,text)); message=self._message(cursor.fetchone())
            cursor.execute("UPDATE schemii.ai_chats SET status='working',updated_at=clock_timestamp() WHERE id=%s",(chat_id,)); self._prune_messages(cursor,chat_id,history_limit)
            return turn,message

    def get_turn(self, owner_id, chat_id, turn_id):
        with self._transaction() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT turn.* FROM schemii.ai_turns turn JOIN schemii.ai_chats chat ON chat.id=turn.chat_id WHERE turn.owner_id=%s AND turn.chat_id=%s AND turn.id=%s AND chat.status<>'deleted'",(owner_id,chat_id,turn_id)); row=cursor.fetchone()
            if row is None: raise AiNotFoundError("Turn was not found")
            return self._turn(row)

    def claim_turn(self, owner_id, chat_id, turn_id):
        with self._transaction() as connection, connection.cursor() as cursor:
            cursor.execute("UPDATE schemii.ai_turns SET status='running',started_at=clock_timestamp() WHERE owner_id=%s AND chat_id=%s AND id=%s AND status='queued' RETURNING *",(owner_id,chat_id,turn_id)); row=cursor.fetchone(); return self._turn(row) if row else None

    def finish_turn(self, owner_id, chat_id, turn_id, text, *, rerun=False, history_limit=200):
        with self._transaction() as connection, connection.cursor() as cursor:
            cursor.execute("UPDATE schemii.ai_turns SET status='succeeded',result_context_rerun=%s,completed_at=clock_timestamp() WHERE owner_id=%s AND chat_id=%s AND id=%s AND status='running' RETURNING *",(rerun,owner_id,chat_id,turn_id)); row=cursor.fetchone()
            if row is None: raise AiConflictError("Turn is no longer running")
            cursor.execute("SELECT COALESCE(max(sequence),0)+1 AS next FROM schemii.ai_messages WHERE chat_id=%s",(chat_id,)); sequence=cursor.fetchone()["next"]
            cursor.execute("INSERT INTO schemii.ai_messages (id,chat_id,turn_id,sequence,role,text) VALUES (%s,%s,%s,%s,'assistant',%s)",(f"msg_{secrets.token_hex(16)}",chat_id,turn_id,sequence,text)); cursor.execute("UPDATE schemii.ai_chats SET status='idle',updated_at=clock_timestamp() WHERE owner_id=%s AND id=%s",(owner_id,chat_id)); self._prune_messages(cursor,chat_id,history_limit); return self._turn(row)

    def fail_turn(self, owner_id, chat_id, turn_id, code, message):
        with self._transaction() as connection, connection.cursor() as cursor:
            cursor.execute("UPDATE schemii.ai_turns SET status='failed',error_code=%s,error_message=%s,completed_at=clock_timestamp() WHERE owner_id=%s AND chat_id=%s AND id=%s AND status IN ('queued','running') RETURNING *",(code,message,owner_id,chat_id,turn_id)); row=cursor.fetchone()
            if row is None: raise AiConflictError("Turn is no longer active")
            cursor.execute("UPDATE schemii.ai_chats SET status='failed',updated_at=clock_timestamp() WHERE owner_id=%s AND id=%s",(owner_id,chat_id)); return self._turn(row)

    def cancel_turn(self, owner_id, chat_id, turn_id):
        with self._transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT external_session_id FROM schemii.ai_chats WHERE owner_id=%s AND id=%s AND status<>'deleted' FOR UPDATE",
                (owner_id, chat_id),
            )
            chat = cursor.fetchone()
            if chat is None:
                raise AiNotFoundError("Chat was not found")
            cursor.execute(
                "UPDATE schemii.ai_turns SET status='cancelled',completed_at=clock_timestamp() WHERE owner_id=%s AND chat_id=%s AND id=%s AND status IN ('queued','running') RETURNING *",
                (owner_id, chat_id, turn_id),
            )
            row = cursor.fetchone()
            if row is None:
                raise AiConflictError("Turn is no longer active")
            cursor.execute(
                "UPDATE schemii.ai_chats SET status='idle',updated_at=clock_timestamp() WHERE owner_id=%s AND id=%s",
                (owner_id, chat_id),
            )
            return self._turn(row), chat["external_session_id"]

    @staticmethod
    def _prune_messages(cursor, chat_id, limit):
        cursor.execute(
            """
            WITH boundary AS (
                SELECT sequence
                FROM schemii.ai_messages
                WHERE chat_id=%s
                ORDER BY sequence DESC
                OFFSET %s LIMIT 1
            ), old_turns AS (
                SELECT message.turn_id
                FROM schemii.ai_messages message, boundary
                WHERE message.chat_id=%s AND message.turn_id IS NOT NULL
                GROUP BY message.turn_id, boundary.sequence
                HAVING max(message.sequence) <= boundary.sequence
            )
            DELETE FROM schemii.ai_turns turn
            WHERE turn.chat_id=%s
              AND turn.status NOT IN ('queued','running')
              AND turn.id IN (SELECT turn_id FROM old_turns)
            """,
            (chat_id, limit, chat_id, chat_id),
        )
        cursor.execute(
            "DELETE FROM schemii.ai_messages WHERE chat_id=%s AND turn_id IS NULL AND id IN (SELECT id FROM schemii.ai_messages WHERE chat_id=%s ORDER BY sequence DESC OFFSET %s)",
            (chat_id, chat_id, limit),
        )

    def add_event(self, owner_id, chat_id, kind, payload):
        _require_row_free_metadata(payload)
        with self._transaction() as connection, connection.cursor() as cursor:
            cursor.execute("INSERT INTO schemii.ai_activity_events (chat_id,owner_id,kind,payload) SELECT %s,%s,%s,%s::jsonb WHERE EXISTS (SELECT 1 FROM schemii.ai_chats WHERE id=%s AND owner_id=%s AND status<>'deleted') RETURNING id,kind,payload,created_at",(chat_id,owner_id,kind,json.dumps(payload),chat_id,owner_id)); row=cursor.fetchone()
            if row is None: raise AiNotFoundError("Chat was not found")
            cursor.execute(
                "DELETE FROM schemii.ai_activity_events WHERE chat_id=%s AND id IN (SELECT id FROM schemii.ai_activity_events WHERE chat_id=%s ORDER BY id DESC OFFSET %s)",
                (chat_id, chat_id, self._policy.activity_history_limit),
            )
            return SchemiiActivityEvent(sequence=row["id"],kind=row["kind"],payload=row["payload"],created_at=row["created_at"])

    def activity(self, owner_id, chat_id, after, limit=200):
        with self._transaction() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT event.id,event.kind,event.payload,event.created_at FROM schemii.ai_activity_events event JOIN schemii.ai_chats chat ON chat.id=event.chat_id WHERE event.owner_id=%s AND event.chat_id=%s AND event.id>%s AND chat.status<>'deleted' ORDER BY event.id LIMIT %s",(owner_id,chat_id,after,limit)); return [SchemiiActivityEvent(sequence=row["id"],kind=row["kind"],payload=row["payload"],created_at=row["created_at"]) for row in cursor.fetchall()]

    def create_proposal(self, owner_id, chat_id, turn_id, capability, action_type, summary, action, digest, workspace_revision, design_revision, destructive, expires_at, chat_revision=None):
        proposal_id=f"prop_{secrets.token_hex(16)}"
        with self._transaction() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT revision,capabilities FROM schemii.ai_chats WHERE owner_id=%s AND id=%s AND status<>'deleted' FOR UPDATE", (owner_id, chat_id))
            chat = cursor.fetchone()
            if chat is None:
                raise AiNotFoundError("Chat was not found")
            capabilities = AiCapabilities.model_validate(chat["capabilities"])
            if chat_revision is not None and chat["revision"] != chat_revision:
                raise AiConflictError("Conversation permissions changed while the proposal was being prepared")
            if not getattr(capabilities, capability, False):
                raise AiConflictError("The proposal's required permission is no longer enabled")
            cursor.execute("INSERT INTO schemii.ai_proposals (id,chat_id,turn_id,owner_id,capability,action_type,summary,action,digest,expected_workspace_revision,expected_design_revision,destructive,expires_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s) RETURNING *",(proposal_id,chat_id,turn_id,owner_id,capability,action_type,summary,json.dumps(action),digest,workspace_revision,design_revision,destructive,expires_at)); return self._proposal(cursor.fetchone())

    def list_proposals(self, owner_id, chat_id):
        with self._transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "UPDATE schemii.ai_proposals SET revision=revision+1,status='expired' WHERE owner_id=%s AND chat_id=%s AND status='pending' AND expires_at<=clock_timestamp()",
                (owner_id, chat_id),
            )
            cursor.execute("SELECT * FROM schemii.ai_proposals WHERE owner_id=%s AND chat_id=%s ORDER BY created_at",(owner_id,chat_id)); return [self._proposal(row) for row in cursor.fetchall()]

    def get_proposal(self, owner_id, chat_id, proposal_id):
        with self._transaction() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT * FROM schemii.ai_proposals WHERE owner_id=%s AND chat_id=%s AND id=%s",(owner_id,chat_id,proposal_id)); row=cursor.fetchone()
            if row is None: raise AiNotFoundError("Proposal was not found")
            return self._proposal(row)

    def proposal_action(self, owner_id, chat_id, proposal_id):
        with self._transaction() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT action FROM schemii.ai_proposals WHERE owner_id=%s AND chat_id=%s AND id=%s",(owner_id,chat_id,proposal_id)); row=cursor.fetchone()
            if row is None: raise AiNotFoundError("Proposal was not found")
            return row["action"]

    def claim_proposal(self, owner_id, chat_id, proposal_id, revision, digest, now):
        with self._transaction() as connection, connection.cursor() as cursor:
            cursor.execute("UPDATE schemii.ai_proposals SET revision=revision+1,status=CASE WHEN expires_at<=%s THEN 'expired' ELSE 'executing' END WHERE owner_id=%s AND chat_id=%s AND id=%s AND revision=%s AND digest=%s AND status='pending' RETURNING *",(now,owner_id,chat_id,proposal_id,revision,digest)); row=cursor.fetchone()
            if row is None or row["status"]=="expired": raise AiConflictError("Proposal changed, expired, or was already used")
            return self._proposal(row),row["action"]

    def dismiss_proposal(self, owner_id, chat_id, proposal_id):
        with self._transaction() as connection, connection.cursor() as cursor:
            cursor.execute("UPDATE schemii.ai_proposals SET revision=revision+1,status='dismissed' WHERE owner_id=%s AND chat_id=%s AND id=%s AND status='pending' RETURNING *",(owner_id,chat_id,proposal_id)); row=cursor.fetchone()
            if row is None: raise AiConflictError("Proposal is no longer pending")
            return self._proposal(row)

    def create_operation(self, owner_id, chat_id, proposal_id, kind):
        operation_id=f"aop_{secrets.token_hex(16)}"
        with self._transaction() as connection, connection.cursor() as cursor:
            cursor.execute("INSERT INTO schemii.ai_operations (id,proposal_id,chat_id,owner_id,kind,status) VALUES (%s,%s,%s,%s,%s,'running') RETURNING *",(operation_id,proposal_id,chat_id,owner_id,kind)); return self._operation(cursor.fetchone())

    def begin_operation(self, owner_id, chat_id, proposal_id, proposal_revision, digest, chat_revision, capability, now):
        operation_id = f"aop_{secrets.token_hex(16)}"
        capability_key = {
            "design_changes": "designChanges",
            "live_catalog": "liveCatalog",
            "structured_data_read": "structuredDataRead",
            "raw_sql_read": "rawSqlRead",
            "raw_sql_write": "rawSqlWrite",
        }.get(capability)
        if capability_key is None:
            raise AiConflictError("Proposal requires an unknown permission")
        with self._transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT revision,capabilities FROM schemii.ai_chats WHERE owner_id=%s AND id=%s AND status<>'deleted' FOR UPDATE",
                (owner_id, chat_id),
            )
            chat = cursor.fetchone()
            if (
                chat is None
                or chat["revision"] != chat_revision
                or not bool(chat["capabilities"].get(capability_key, False))
            ):
                raise AiConflictError(
                    "Conversation permissions changed or the proposal's required permission is no longer enabled"
                )
            cursor.execute(
                """
                UPDATE schemii.ai_proposals
                SET revision=revision+1,status='executing'
                WHERE owner_id=%s AND chat_id=%s AND id=%s
                  AND revision=%s AND digest=%s
                  AND status='pending' AND expires_at>%s
                RETURNING *
                """,
                (
                    owner_id,
                    chat_id,
                    proposal_id,
                    proposal_revision,
                    digest,
                    now,
                ),
            )
            proposal_row = cursor.fetchone()
            if proposal_row is None:
                raise AiConflictError(
                    "Proposal changed, expired, was already used, or its required permission is no longer enabled"
                )
            cursor.execute(
                "INSERT INTO schemii.ai_operations (id,proposal_id,chat_id,owner_id,kind,status) VALUES (%s,%s,%s,%s,%s,'running') RETURNING *",
                (operation_id, proposal_id, chat_id, owner_id, proposal_row["action_type"]),
            )
            return self._operation(cursor.fetchone()), self._proposal(proposal_row), proposal_row["action"]

    def finish_operation(self, owner_id, chat_id, operation_id, **changes):
        _require_row_free_metadata(changes.get("result_summary"))
        with self._transaction() as connection, connection.cursor() as cursor:
            cursor.execute("UPDATE schemii.ai_operations SET revision=revision+1,status=%s,resource_kind=%s,resource_id=%s,result_summary=%s::jsonb,error_code=%s,error_message=%s,updated_at=clock_timestamp() WHERE owner_id=%s AND chat_id=%s AND id=%s RETURNING *",(changes["status"],changes.get("resource_kind"),changes.get("resource_id"),__import__('json').dumps(changes.get("result_summary")) if changes.get("result_summary") is not None else None,changes.get("error_code"),changes.get("error_message"),owner_id,chat_id,operation_id)); row=cursor.fetchone()
            if row is None: raise AiNotFoundError("Operation was not found")
            cursor.execute("UPDATE schemii.ai_proposals SET revision=revision+1,status=%s WHERE id=%s",("succeeded" if changes["status"]=="succeeded" else "failed",row["proposal_id"])); return self._operation(row)

    def get_operation(self, owner_id, chat_id, operation_id):
        with self._transaction() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT * FROM schemii.ai_operations WHERE owner_id=%s AND chat_id=%s AND id=%s",(owner_id,chat_id,operation_id)); row=cursor.fetchone()
            if row is None: raise AiNotFoundError("Operation was not found")
            return self._operation(row)

    def list_operations(self, owner_id, chat_id):
        with self._transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT recent.* FROM (SELECT operation.* FROM schemii.ai_operations operation JOIN schemii.ai_chats chat ON chat.id=operation.chat_id WHERE operation.owner_id=%s AND operation.chat_id=%s AND chat.status<>'deleted' ORDER BY operation.created_at DESC LIMIT 100) recent ORDER BY recent.created_at",
                (owner_id, chat_id),
            )
            return [self._operation(row) for row in cursor.fetchall()]

    def operation_action(self, owner_id, chat_id, operation_id):
        with self._transaction() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT operation.*, proposal.action FROM schemii.ai_operations operation JOIN schemii.ai_proposals proposal ON proposal.id=operation.proposal_id WHERE operation.owner_id=%s AND operation.chat_id=%s AND operation.id=%s",(owner_id,chat_id,operation_id)); row=cursor.fetchone()
            if row is None: raise AiNotFoundError("Operation was not found")
            return self._operation(row),row["action"]

    def recover_interrupted(self):
        with self._transaction() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT owner_id,id,external_session_id FROM schemii.ai_chats WHERE external_session_id IS NOT NULL"
            )
            sessions = [
                (row["owner_id"], row["id"], row["external_session_id"])
                for row in cursor.fetchall()
            ]
            cursor.execute(
                "UPDATE schemii.ai_turns SET status='failed',error_code='ai_server_restarted',error_message='The assistant server restarted before this turn completed',completed_at=clock_timestamp() WHERE status IN ('queued','running')"
            )
            cursor.execute(
                "UPDATE schemii.ai_operations SET revision=revision+1,status='uncertain',error_code='ai_server_restarted',error_message='The server restarted while this operation was running',updated_at=clock_timestamp() WHERE status='running'"
            )
            cursor.execute(
                "UPDATE schemii.ai_proposals SET revision=revision+1,status='failed' WHERE status='executing'"
            )
            cursor.execute(
                "UPDATE schemii.ai_proposals SET revision=revision+1,status='expired' WHERE status='pending' AND expires_at<=clock_timestamp()"
            )
            cursor.execute(
                "UPDATE schemii.ai_chats SET status='failed',updated_at=clock_timestamp() WHERE status='working'"
            )
            self._cleanup(cursor)
            return sessions

    def _cleanup(self, cursor):
        cursor.execute(
            "DELETE FROM schemii.ai_chats WHERE status='deleted' OR (status<>'working' AND updated_at < clock_timestamp() - (%s * interval '1 day'))",
            (self._policy.chat_retention_days,),
        )
