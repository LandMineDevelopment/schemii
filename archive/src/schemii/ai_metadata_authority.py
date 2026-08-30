from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from .metadata import MetadataStore, MetadataStoreError
from .ai_policy import DEFAULT_AGENT_ID, LEGACY_CAPABILITIES, capability_unavailable, effective_chat_snapshot
from .ai_tool_contracts import action_authority


CAPABILITIES = ("schema", "structured", "write", "rawread", "rawwrite")
APPROVAL_MODES = {"every_action": "approval", "once_per_chat": "once_per_chat", "automatic": "automatic"}


def _grant_mode(mode: str) -> str:
    return "deny" if mode == "disabled" else "approval" if mode == "every_action" else mode


class SchemiiMetadataAuthority:
    """Schemii authority coordinator backed exclusively by transactional metadata."""

    application_id = "schemii"
    resource_kind = "schema"
    resource_id_field = "schemaId"
    query_action_type = "schema_read_query"
    retry_metadata_commit_uncertain = False

    def __init__(self, store: MetadataStore, *, worker_id: str, lease_seconds: int = 90):
        self.store = store
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds

    def health(self) -> dict[str, Any]:
        return self.store.health()

    def get_settings(self) -> dict[str, Any]:
        return self.store.get_agent_settings(self.application_id, DEFAULT_AGENT_ID)

    def update_settings(self, body: Any) -> dict[str, Any]:
        if not isinstance(body, dict) or set(body) != {"expectedRevision", "policy"}:
            raise MetadataStoreError("invalid_metadata", "AI settings request fields are invalid", status=400)
        return self.store.update_agent_settings(
            self.application_id, DEFAULT_AGENT_ID, body["expectedRevision"], body["policy"],
        )

    def provision_chat(self, resource_id: str) -> dict[str, Any]:
        return self.store.provision_chat(self.application_id, self.resource_kind, resource_id)

    def bind_external_session(self, chat_id: str, external_session_id: str, title: str) -> dict[str, Any]:
        try:
            return self.store.bind_chat_external_session(chat_id, external_session_id, title)
        except MetadataStoreError as error:
            if not self.retry_metadata_commit_uncertain or error.code != "metadata_commit_uncertain":
                raise
            return self.store.bind_chat_external_session(chat_id, external_session_id, title)

    def activate_chat(
        self,
        chat_id: str,
        target: dict[str, Any],
        capabilities: list[str],
        approvals: dict[str, str],
    ) -> dict[str, Any]:
        enabled = self._capabilities(capabilities)
        configured = self._approvals(approvals, optional=True)
        settings = self.get_settings()
        policy = effective_chat_snapshot(
            settings, enabled, target_verified=bool(target), disclosure_class="schema",
            requested_modes=configured,
        )
        modes = {
            capability: _grant_mode(item["effectiveMode"])
            for capability, item in policy["capabilities"].items()
        }
        self.store.activate_chat(
            chat_id,
            self._metadata_target(target) if target else None,
            policy=policy,
            capabilities=modes,
            agent_policy_binding={
                "policyRevisionId": settings["policyRevisionId"],
                "schemaVersion": settings["schemaVersion"],
            },
        )
        return self.get_chat(chat_id)

    def fail_chat(self, chat_id: str, reason: str) -> dict[str, Any]:
        return self.store.fail_chat(chat_id, reason)

    def get_chat(self, chat_id: str) -> dict[str, Any]:
        chat, current = self._active_chat_records(chat_id)
        policy = current["policy"]
        grants = {
            item["capability"]: {"policyRevision": item["policyRevision"]}
            for item in self.store.list_grants(chat_id, active_only=True)
        }
        target = chat["target"]
        if policy.get("version") == 2:
            legacy = LEGACY_CAPABILITIES["schemii"]
            enabled = [name for name in CAPABILITIES if policy["capabilities"][legacy[name]]["effectiveMode"] != "disabled"]
            approvals = {name: policy["capabilities"][legacy[name]]["configuredMode"] for name in CAPABILITIES}
        else:
            enabled = [item for item in CAPABILITIES if item in policy["capabilities"]]
            approvals = dict(policy["approvals"])
        return self._chat_envelope(chat, current) | {
            "capabilities": enabled,
            "approvals": approvals,
            "policySnapshot": copy.deepcopy(policy) if policy.get("version") == 2 else None,
            "grants": grants,
        }

    def initialize_conversation_title(self, chat_id: str, title: str) -> dict[str, Any]:
        self.store.set_chat_conversation_title(chat_id, title, overwrite=False)
        return self.get_chat(chat_id)

    def rename_conversation(self, chat_id: str, title: str) -> dict[str, Any]:
        self.store.set_chat_conversation_title(chat_id, title, overwrite=True)
        return self.get_chat(chat_id)

    def _active_chat_records(self, chat_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        chat = self.store.get_chat(chat_id)
        if chat["state"] != "active":
            raise MetadataStoreError("chat_inactive", "AI chat is not active", status=409)
        return chat, self.store.get_current_policy(chat_id)

    def _chat_envelope(
        self, chat: dict[str, Any], current: dict[str, Any],
    ) -> dict[str, Any]:
        target = chat["target"]
        return {
            "id": chat["chatId"],
            self.resource_id_field: chat["resourceId"],
            "externalSessionId": chat["externalSessionId"],
            "title": chat.get("conversationTitle") or chat["displayTitle"],
            "contextTitle": chat["displayTitle"],
            "conversationTitle": chat.get("conversationTitle"),
            "target": {} if target is None else {
                "profileId": target["profileId"],
                "database": target["databaseName"],
                "namespace": target["namespaceName"],
                "profileFingerprint": target["profileFingerprint"],
            },
            "policyRevision": current["revision"],
            "agentPolicyRevisionId": current.get("agentPolicyRevisionId"),
        }

    def list_chats(self, schema_id: str, target: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        records = self.store.list_chats(resource_kind="schema", resource_id=schema_id, states=["active"])
        chats = [self.get_chat(item["chatId"]) for item in records]
        return chats if target is None else [chat for chat in chats if chat["target"] == target]

    def update_policy(self, chat_id: str, capabilities: Any, approvals: Any, expected_revision: Any) -> dict[str, Any]:
        enabled = self._capabilities(capabilities)
        configured = self._approvals(approvals)
        chat = self.get_chat(chat_id)
        if chat.get("agentPolicyRevisionId") is not None:
            raise MetadataStoreError("policy_immutable", "Settings-linked chat policy snapshots are immutable", status=409)
        if not chat["target"] and any(item != "schema" for item in enabled):
            raise MetadataStoreError("chat_target_required", "Start a new target-bound chat to enable data capabilities", status=409)
        modes = {
            capability: APPROVAL_MODES[configured[capability]] if capability in enabled else "deny"
            for capability in CAPABILITIES
        }
        modes["metadata"] = "approval"
        self.store.update_policy(
            chat_id,
            expected_revision,
            {"version": 1, "capabilities": [item for item in CAPABILITIES if item in enabled], "approvals": configured},
            modes,
        )
        return self.get_chat(chat_id)

    def begin_delete(self, chat_id: str) -> dict[str, Any]:
        chat = self.store.get_chat(chat_id)
        self.store.begin_chat_deletion(chat_id)
        return chat

    def finish_delete(self, chat_id: str) -> dict[str, Any]:
        return self.store.mark_chat_deleted(chat_id)

    def create_proposal(
        self,
        chat_id: str,
        action: dict[str, Any],
        policy_binding: dict[str, Any],
        authorization_target: dict[str, Any],
        schema_concurrency: dict[str, Any],
    ) -> dict[str, Any]:
        capability = policy_binding.get("capability") or "metadata"
        policy_binding = copy.deepcopy(policy_binding)
        if policy_binding.get("snapshot", {}).get("version") == 2:
            chat = self.store.get_chat(chat_id)
            policy_binding.update({
                "resource": {
                    "kind": chat["resourceKind"], "id": chat["resourceId"],
                    "revision": schema_concurrency.get("revision"),
                    "layoutToken": schema_concurrency.get("layoutToken"),
                },
                "target": copy.deepcopy(authorization_target),
            })
        binding = {
            "policyBinding": policy_binding,
            "authorizationTarget": copy.deepcopy(authorization_target),
            "schemaConcurrency": copy.deepcopy(schema_concurrency),
        }
        created = self.store.create_proposal(
            chat_id, capability, policy_binding["policyRevision"], binding, action,
        )
        return self.proposal(created["proposalId"], chat_id)

    def policy_binding(self, chat: dict[str, Any], action: dict[str, Any], capability: str, *, origin: str = "model") -> dict[str, Any]:
        snapshot = chat.get("policySnapshot")
        if snapshot is None:
            raise MetadataStoreError("policy_not_found", "Settings-linked chat policy snapshot is unavailable", status=409)
        canonical = LEGACY_CAPABILITIES["schemii"].get(capability, capability)
        authority = snapshot["capabilities"].get(canonical)
        if authority is None or authority["effectiveMode"] == "disabled":
            raise capability_unavailable(
                "schemii", canonical, agent_id=snapshot["agentId"],
                current_mode="disabled" if authority is None else authority["effectiveMode"],
                policy_revision=snapshot["agentPolicyRevision"],
            )
        return {
            "application": "schemii", "agentId": snapshot["agentId"],
            "agentPolicyRevision": snapshot["agentPolicyRevision"],
            "agentPolicyRevisionId": snapshot["agentPolicyRevisionId"],
            "agentPolicySchemaVersion": snapshot["agentPolicySchemaVersion"],
            "chatPolicyRevision": chat["policyRevision"], "policyRevision": chat["policyRevision"],
            "canonicalCapability": canonical, "capability": canonical,
            "configuredMode": authority["configuredMode"], "effectiveMode": authority["effectiveMode"],
            "safetyFloorReason": authority["safetyFloorReason"],
            "snapshot": copy.deepcopy(snapshot), "disclosureClass": snapshot["disclosureClass"],
            "origin": origin,
        }

    def proposal(self, proposal_id: str, chat_id: str) -> dict[str, Any]:
        record = self.store.get_proposal(proposal_id)
        self._owned(record, chat_id)
        binding = record["binding"]
        return {
            "id": record["proposalId"], "chatId": record["chatId"], "state": record["state"],
            "action": record["action"], "policyBinding": binding["policyBinding"],
            "authorizationTarget": binding["authorizationTarget"],
            "schemaConcurrency": binding["schemaConcurrency"],
            "cancellationRequested": record.get("cancellationRequestedAt") is not None,
        }

    def pending_proposals(self, chat_id: str) -> list[dict[str, Any]]:
        return [self.proposal(item["proposalId"], chat_id) for item in self.store.list_proposals(chat_id, states=["ready", "authorized", "cancelled"])]

    def request_query_cancellation(self, proposal_id: str, chat_id: str) -> dict[str, Any]:
        proposal = self.proposal(proposal_id, chat_id)
        if proposal["action"].get("type") != self.query_action_type:
            raise MetadataStoreError("operation_not_cancellable", "Only running AI queries can be cancelled", status=409)
        return self.store.request_proposal_cancellation(
            proposal_id, chat_id,
            expected_application=self.application_id, expected_resource_kind=self.resource_kind,
        )

    def authorize_and_claim(
        self,
        proposal_id: str,
        chat_id: str,
        policy_revision: Any,
        confirmation: Any,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        proposal = self.proposal(proposal_id, chat_id)
        policy = proposal["policyBinding"]
        try:
            _, mode = action_authority(
                self.application_id, proposal["action"],
                policy.get("canonicalCapability", policy.get("capability")), policy.get("effectiveMode"),
                origin=policy.get("origin"),
            )
        except (KeyError, ValueError) as exc:
            raise MetadataStoreError(
                "authority_binding_mismatch", "Proposal action authority does not match the server contract", status=409,
            ) from exc
        approved = False
        if mode != "automatic":
            expected_mode = "once_per_chat" if mode == "once_per_chat" else "every_action"
            approved = isinstance(confirmation, dict) and confirmation == {"accepted": True, "mode": expected_mode}
            if not approved and mode == "every_action":
                raise MetadataStoreError("approval_required", "This AI action requires approval", status=400)
        elif confirmation is not None:
            raise MetadataStoreError("invalid_metadata", "Automatic approval is server-owned", status=400)
        created = self.store.authorize_and_create_operation(
            proposal_id,
            expected_policy_revision=policy_revision,
            approved=approved,
            required_effective_mode=mode,
            worker_id=self.worker_id,
            lease_seconds=self.lease_seconds,
        )
        operation = self.operation(created["operationId"], chat_id)
        operation.update({
            "executionOwner": created["executionOwner"],
            "attemptId": created.get("attemptId"),
            "claimToken": created.get("claimToken"),
        })
        approval = {
            "capability": policy.get("capability"), "configuredMode": policy["configuredMode"],
            "effectiveMode": mode, "source": "automatic" if mode == "automatic" else "explicit",
            "policyRevision": policy["policyRevision"],
        }
        return operation, approval

    def operation(self, operation_id: str, chat_id: str) -> dict[str, Any]:
        record = self.store.get_operation(operation_id)
        self._owned(record, chat_id)
        outcome = record["outcome"] or {}
        return {
            "id": record["operationId"], "proposalId": record["proposalId"], "state": record["state"],
            "result": outcome.get("result"), "error": outcome.get("error"),
            "reconcileRequired": record["state"] == "uncertain",
            "cancellationRequested": bool(record.get("cancellationRequested")),
        }

    def operation_for_proposal(self, proposal_id: str, chat_id: str) -> dict[str, Any] | None:
        for operation in self.store.list_operations(chat_id):
            if operation["proposalId"] == proposal_id:
                return self.operation(operation["operationId"], chat_id)
        return None

    def consume_bound(self, operation_id: str, name: str, amount: int, evidence: dict[str, Any]) -> dict[str, Any]:
        return self.store.consume_operation_bound(operation_id, name, amount, evidence)

    def finish_operation(self, attempt_id: str, claim_token: str, state: str, *, result=None, error=None) -> dict[str, Any]:
        try:
            finished = self.store.finish_operation(attempt_id, claim_token, state, result=result, error=error)
        except MetadataStoreError as failure:
            if failure.code != "metadata_commit_uncertain":
                raise
            # The exact token and outcome make this metadata-only retry idempotent.
            finished = self.store.finish_operation(attempt_id, claim_token, state, result=result, error=error)
        return {
            "id": finished["operationId"], "state": finished["state"],
            "result": finished.get("result"), "error": finished.get("error"),
        }

    def resolve_operation(self, operation_id: str, chat_id: str, state: str, *, result=None, error=None) -> dict[str, Any]:
        self.operation(operation_id, chat_id)
        self.store.resolve_uncertain_operation(operation_id, state, result=result, error=error)
        return self.operation(operation_id, chat_id)

    def create_result(self, chat_id: str, binding: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        result = self.store.create_result(chat_id, binding, payload)
        return {"id": result["resultRefId"]}

    def reserve_result(self, result_id: str, chat_id: str, binding: dict[str, Any]) -> dict[str, Any]:
        return self.store.reserve_result(result_id, chat_id, binding)

    def begin_result_delivery(self, delivery_id: str, token: str) -> dict[str, Any]:
        return self.store.begin_result_delivery(delivery_id, token)

    def consume_result(self, delivery_id: str, token: str) -> dict[str, Any]:
        return self.store.consume_result(delivery_id, token)

    def release_result(self, delivery_id: str, token: str) -> dict[str, Any]:
        return self.store.release_result(delivery_id, token)

    def uncertain_result(self, delivery_id: str, token: str) -> dict[str, Any]:
        return self.store.mark_result_uncertain(delivery_id, token)

    @staticmethod
    def _owned(record: dict[str, Any], chat_id: str) -> None:
        if record["chatId"] != chat_id:
            raise MetadataStoreError("authority_binding_mismatch", "Authority record belongs to another chat", status=403)

    @staticmethod
    def _metadata_target(target: dict[str, Any]) -> dict[str, Any]:
        fingerprint = target["profileFingerprint"]
        connected = hashlib.sha256(json.dumps(
            [target["profileId"], target["database"], target["namespace"], fingerprint],
            ensure_ascii=True, separators=(",", ":"),
        ).encode("utf-8")).hexdigest()
        return {
            "profileId": target["profileId"], "databaseName": target["database"],
            "namespaceName": target["namespace"], "profileFingerprint": fingerprint,
            "connectedTargetFingerprint": connected,
        }

    @staticmethod
    def _capabilities(value: Any) -> set[str]:
        if not isinstance(value, list) or len(value) != len(set(value)) or any(item not in CAPABILITIES for item in value):
            raise MetadataStoreError("invalid_metadata", "AI capabilities are invalid", status=400)
        return set(value)

    @staticmethod
    def _approvals(value: Any, *, optional: bool = False) -> dict[str, str]:
        if optional and value is None:
            return {}
        if not isinstance(value, dict) or set(value) != set(CAPABILITIES) or any(mode not in APPROVAL_MODES for mode in value.values()):
            raise MetadataStoreError("invalid_metadata", "AI approval settings are invalid", status=400)
        if optional and "automatic" in value.values():
            raise MetadataStoreError("invalid_metadata", "Automatic AI approval is server-owned", status=400)
        return dict(value)


def retire_legacy_schemii_authority(config_dir: Path) -> list[str]:
    """Archive legacy executable JSON without importing or interpreting any record."""
    retired = config_dir / "retired-json-authority"
    retired.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(retired, 0o700)
    sources = {
        "ai-chats-v1": config_dir / "ai_chats" / "v1",
        "ai-authority-v1-schemii": config_dir / "ai_authority" / "v1" / "schemii",
    }
    moved = []
    for name, source in sources.items():
        destination = retired / name
        if source.exists() and not destination.exists():
            try:
                os.replace(source, destination)
                moved.append(name)
            except FileNotFoundError:
                pass
    marker = retired / "README.txt"
    if not marker.exists():
        marker.write_text(
            "Legacy Schemii JSON authority was retired without import. Records in this directory are inert and must never be executed.\n",
            encoding="ascii",
        )
        os.chmod(marker, 0o600)
    return moved
