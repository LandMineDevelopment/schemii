from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .ai_metadata_authority import SchemiiMetadataAuthority
from .ai_tool_contracts import contract_for_action
from .metadata import MetadataStore, MetadataStoreError
from .ai_policy import LEGACY_CAPABILITIES, capability_unavailable, effective_chat_snapshot


CAPABILITIES = ("metadata", "dashboard", "data")


class SchemerMetadataAuthority(SchemiiMetadataAuthority):
    """Schemer chat and execution authority backed exclusively by metadata PostgreSQL."""

    application_id = "schemer"
    resource_kind = "dashboard"
    resource_id_field = "dashboardId"
    query_action_type = "read_query"
    retry_metadata_commit_uncertain = True

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
        chat, current = self._active_chat_records(chat_id)
        policy = current["policy"]
        if policy.get("version") == 2:
            enabled = [legacy for legacy, canonical in LEGACY_CAPABILITIES["schemer"].items()
                       if policy["capabilities"][canonical]["effectiveMode"] != "disabled"]
            access_level = policy["disclosureClass"]
        else:
            enabled = list(policy["capabilities"])
            access_level = policy["accessLevel"]
        return self._chat_envelope(chat, current) | {
            "accessLevel": access_level,
            "capabilities": enabled,
            "policySnapshot": policy if policy.get("version") == 2 else None,
        }

    def list_chats(self, dashboard_id: str | None = None) -> list[dict[str, Any]]:
        records = self.store.list_chats(
            resource_kind="dashboard", resource_id=dashboard_id, states=["active"],
        )
        return [self.get_chat(item["chatId"]) for item in records]

    @staticmethod
    def policy_binding(chat: dict[str, Any], action: dict[str, Any]) -> dict[str, Any]:
        contract = contract_for_action("schemer", action)
        if contract is None or contract.capability is None:
            raise MetadataStoreError("action_temporarily_unavailable", "This action has no Schemer authority contract", status=409)
        canonical = contract.capability
        snapshot = chat.get("policySnapshot")
        if snapshot is not None:
            authority = snapshot["capabilities"].get(canonical)
            if authority is None or authority["effectiveMode"] == "disabled":
                raise capability_unavailable(
                    "schemer", canonical, agent_id=snapshot["agentId"],
                    current_mode="disabled" if authority is None else authority["effectiveMode"],
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
        capability = next((legacy for legacy, mapped in LEGACY_CAPABILITIES["schemer"].items() if mapped == canonical), canonical)
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
