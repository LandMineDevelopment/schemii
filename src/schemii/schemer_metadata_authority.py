from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .ai_metadata_authority import SchemiiMetadataAuthority
from .metadata import MetadataStore, MetadataStoreError
from .ai_policy import DEFAULT_AGENT_ID, LEGACY_CAPABILITIES, capability_unavailable, effective_chat_snapshot


CAPABILITIES = ("metadata", "dashboard", "data")


class SchemerMetadataAuthority(SchemiiMetadataAuthority):
    """Schemer chat and execution authority backed exclusively by metadata PostgreSQL."""

    application_id = "schemer"
    resource_kind = "dashboard"
    query_action_type = "read_query"

    def __init__(self, store: MetadataStore, *, worker_id: str, lease_seconds: int = 90):
        super().__init__(store, worker_id=worker_id, lease_seconds=lease_seconds)

    def provision_chat(self, dashboard_id: str) -> dict[str, Any]:
        return self.store.provision_chat("schemer", "dashboard", dashboard_id)

    def get_settings(self) -> dict[str, Any]:
        return self.store.get_agent_settings("schemer", DEFAULT_AGENT_ID)

    def update_settings(self, body: Any) -> dict[str, Any]:
        if not isinstance(body, dict) or set(body) != {"expectedRevision", "policy"}:
            raise MetadataStoreError("invalid_metadata", "AI settings request fields are invalid", status=400)
        return self.store.update_agent_settings(
            "schemer", DEFAULT_AGENT_ID, body["expectedRevision"], body["policy"],
        )

    def bind_external_session(self, chat_id: str, external_session_id: str, title: str) -> dict[str, Any]:
        try:
            return self.store.bind_chat_external_session(chat_id, external_session_id, title)
        except MetadataStoreError as error:
            if error.code != "metadata_commit_uncertain":
                raise
            return self.store.bind_chat_external_session(chat_id, external_session_id, title)

    def activate_chat(self, chat_id: str, target: dict[str, Any], access_level: str) -> dict[str, Any]:
        enabled = self._access_capabilities(access_level)
        settings = self.get_settings()
        policy = effective_chat_snapshot(
            settings, enabled, target_verified=bool(target), disclosure_class=access_level,
        )
        modes = {
            name: "deny" if item["effectiveMode"] == "disabled" else
                  "approval" if item["effectiveMode"] == "every_action" else item["effectiveMode"]
            for name, item in policy["capabilities"].items()
        }
        metadata_target = self._metadata_target(target) if target else None
        try:
            self.store.activate_chat(
                chat_id, metadata_target, policy=policy, capabilities=modes,
                agent_policy_binding={"policyRevisionId": settings["policyRevisionId"], "schemaVersion": settings["schemaVersion"]},
            )
        except MetadataStoreError as error:
            if error.code != "metadata_commit_uncertain":
                raise
            self.store.activate_chat(
                chat_id, metadata_target, policy=policy, capabilities=modes,
                agent_policy_binding={"policyRevisionId": settings["policyRevisionId"], "schemaVersion": settings["schemaVersion"]},
            )
        return self.get_chat(chat_id)

    def get_chat(self, chat_id: str) -> dict[str, Any]:
        chat = self.store.get_chat(chat_id)
        if chat["state"] != "active":
            raise MetadataStoreError("chat_inactive", "AI chat is not active", status=409)
        current = self.store.get_current_policy(chat_id)
        policy = current["policy"]
        target = chat["target"]
        if policy.get("version") == 2:
            enabled = [legacy for legacy, canonical in LEGACY_CAPABILITIES["schemer"].items()
                       if policy["capabilities"][canonical]["effectiveMode"] != "disabled"]
            access_level = policy["disclosureClass"]
        else:
            enabled = list(policy["capabilities"])
            access_level = policy["accessLevel"]
        return {
            "id": chat["chatId"],
            "dashboardId": chat["resourceId"],
            "externalSessionId": chat["externalSessionId"],
            "title": chat.get("conversationTitle") or chat["displayTitle"],
            "contextTitle": chat["displayTitle"],
            "conversationTitle": chat.get("conversationTitle"),
            "accessLevel": access_level,
            "target": {} if target is None else {
                "profileId": target["profileId"],
                "database": target["databaseName"],
                "namespace": target["namespaceName"],
                "profileFingerprint": target["profileFingerprint"],
            },
            "capabilities": enabled,
            "policyRevision": current["revision"],
            "policySnapshot": policy if policy.get("version") == 2 else None,
            "agentPolicyRevisionId": current.get("agentPolicyRevisionId"),
        }

    def initialize_conversation_title(self, chat_id: str, title: str) -> dict[str, Any]:
        self.store.set_chat_conversation_title(chat_id, title, overwrite=False)
        return self.get_chat(chat_id)

    def rename_conversation(self, chat_id: str, title: str) -> dict[str, Any]:
        self.store.set_chat_conversation_title(chat_id, title, overwrite=True)
        return self.get_chat(chat_id)

    def list_chats(self, dashboard_id: str | None = None) -> list[dict[str, Any]]:
        records = self.store.list_chats(
            resource_kind="dashboard", resource_id=dashboard_id, states=["active"],
        )
        return [self.get_chat(item["chatId"]) for item in records]

    @staticmethod
    def policy_binding(chat: dict[str, Any], action: dict[str, Any]) -> dict[str, Any]:
        action_type = action.get("type")
        capability = "data" if action_type == "read_query" else "metadata" if action_type in {"dashboard_create", "dashboard_open"} else "dashboard"
        snapshot = chat.get("policySnapshot")
        if snapshot is not None:
            canonical = LEGACY_CAPABILITIES["schemer"][capability]
            authority = snapshot["capabilities"][canonical]
            if authority["effectiveMode"] == "disabled":
                raise capability_unavailable(
                    "schemer", canonical, agent_id=snapshot["agentId"], current_mode=authority["effectiveMode"],
                    policy_revision=snapshot["agentPolicyRevision"],
                )
            return {
                "application": "schemer", "agentId": snapshot["agentId"],
                "agentPolicyRevision": snapshot["agentPolicyRevision"],
                "agentPolicyRevisionId": snapshot["agentPolicyRevisionId"],
                "agentPolicySchemaVersion": snapshot["agentPolicySchemaVersion"],
                "chatPolicyRevision": chat["policyRevision"], "policyRevision": chat["policyRevision"],
                "canonicalCapability": canonical, "capability": canonical,
                "configuredMode": authority["configuredMode"], "effectiveMode": authority["effectiveMode"],
                "safetyFloorReason": authority["safetyFloorReason"], "snapshot": snapshot,
                "disclosureClass": snapshot["disclosureClass"], "origin": "model",
            }
        if capability not in chat["capabilities"]:
            raise MetadataStoreError("capability_disabled", "AI action is not enabled for this chat", status=403)
        return {
            "capability": capability,
            "configuredMode": "every_action",
            "effectiveMode": "every_action",
            "policyRevision": chat["policyRevision"],
            "origin": "model",
        }

    @staticmethod
    def _access_capabilities(access_level: Any) -> set[str]:
        if access_level == "metadata":
            return {"metadata"}
        if access_level == "dashboard":
            return {"metadata", "dashboard"}
        if access_level == "data":
            return set(CAPABILITIES)
        raise MetadataStoreError("invalid_metadata", "AI access level is invalid", status=400)


def retire_legacy_schemer_authority(config_dir: Path) -> list[str]:
    """Archive Schemer JSON authority without importing authority or title bindings."""
    retired = config_dir / "retired-json-authority"
    retired.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(retired, 0o700)
    source = config_dir / "ai_authority" / "v1" / "schemer"
    destination = retired / "ai-authority-v1-schemer"
    moved = []
    if source.exists() and not destination.exists():
        try:
            os.replace(source, destination)
            moved.append("ai-authority-v1-schemer")
        except FileNotFoundError:
            pass
    marker = retired / "SCHEMER.txt"
    if not marker.exists():
        marker.write_text(
            "Legacy Schemer JSON authority and SCHEMER_CONTEXT title bindings were retired without import. "
            "They are inert and must never authorize a request.\n",
            encoding="ascii",
        )
        os.chmod(marker, 0o600)
    return moved
