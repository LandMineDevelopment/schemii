import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from schemii.ai_policy import (
    CAPABILITY_ALIASES,
    canonical_capability,
    default_policy,
    effective_bounds,
    effective_capabilities,
    effective_chat_snapshot,
    legacy_schemer_capabilities,
    policy_digest,
    validate_policy,
    capability_unavailable,
)
from schemii.metadata import MetadataStoreError


class AiPolicyTests(unittest.TestCase):
    def test_aliases_and_legacy_schemer_tiers_are_explicit(self):
        self.assertEqual(CAPABILITY_ALIASES, {
            "structured": "structured_read", "write": "structured_write",
            "rawread": "raw_read", "rawwrite": "raw_write",
        })
        self.assertEqual(canonical_capability("rawwrite"), "raw_write")
        self.assertEqual(legacy_schemer_capabilities("data"), (
            "dashboard_read", "dashboard_write", "structured_read", "raw_read",
        ))

    def test_defaults_are_disabled_and_application_resources_stay_separate(self):
        schemii = default_policy("schemii")
        schemer = default_policy("schemer")
        self.assertTrue(all(mode == "disabled" for mode in schemii["capabilities"].values()))
        self.assertEqual(set(schemer["capabilities"]), {"structured_read", "raw_read", "dashboard_read", "dashboard_write"})
        self.assertNotIn("structured_write", schemer["capabilities"])
        with self.assertRaises(MetadataStoreError):
            validate_policy("schemer", default_policy("schemii"))

    def test_safety_floors_cannot_be_relaxed(self):
        policy = default_policy("schemii")
        policy["capabilities"] = {name: "automatic" for name in policy["capabilities"]}
        effective = effective_capabilities("schemii", policy)
        self.assertEqual(effective["raw_write"]["effectiveMode"], "every_action")
        self.assertEqual(effective["structured_read"]["effectiveMode"], "automatic")

    def test_bounds_are_strict_and_null_timeout_inherits(self):
        policy = default_policy("schemii")
        self.assertIsNone(effective_bounds(policy)["operationTimeoutMs"])
        policy["bounds"]["rowsDisclosed"] = 0
        with self.assertRaises(MetadataStoreError):
            validate_policy("schemii", policy)
        policy = default_policy("schemii")
        policy["bounds"]["operationTimeoutMs"] = 1000
        self.assertEqual(validate_policy("schemii", policy)["bounds"]["operationTimeoutMs"], 1000)

    def test_serialization_digest_is_canonical_and_credentials_are_not_contract_fields(self):
        first = default_policy("schemii")
        second = json.loads(json.dumps(first, sort_keys=True))
        self.assertEqual(policy_digest(first), policy_digest(second))
        first["apiKey"] = "secret"
        with self.assertRaises(MetadataStoreError):
            validate_policy("schemii", first)

    def test_unavailable_detail_has_only_allowlisted_local_action(self):
        error = capability_unavailable("schemer", "rawwrite")
        self.assertEqual(error.details["capability"], "raw_write")
        self.assertEqual(error.details["settingsAction"], {"type": "open_local_settings", "path": "/api/ai/settings"})
        self.assertEqual(error.details["reason"], "unsupported_product_capability")

    def test_browser_selection_can_only_narrow_server_settings(self):
        policy = default_policy("schemii")
        policy["capabilities"]["schema"] = "every_action"
        policy["capabilities"]["structured_read"] = "automatic"
        settings = {
            "application": "schemii", "agentId": "default", "revision": 7,
            "schemaVersion": 1, "policyRevisionId": "revision-id", "policyDigest": policy_digest(policy),
            "capabilities": effective_capabilities("schemii", policy), "effectiveBounds": effective_bounds(policy),
        }
        snapshot = effective_chat_snapshot(
            settings, ["schema", "structured", "rawwrite"], target_verified=True,
            disclosure_class="schema-data", requested_modes={"schema": "every_action", "structured": "once_per_chat"},
        )
        self.assertEqual(snapshot["capabilities"]["schema"]["effectiveMode"], "every_action")
        self.assertEqual(snapshot["capabilities"]["structured_read"]["effectiveMode"], "once_per_chat")
        self.assertEqual(snapshot["capabilities"]["raw_write"]["effectiveMode"], "disabled")
        self.assertEqual(snapshot["agentPolicyRevision"], 7)

    def test_target_capability_is_disabled_without_exact_verified_target(self):
        policy = default_policy("schemer")
        policy["capabilities"]["raw_read"] = "automatic"
        settings = {
            "application": "schemer", "agentId": "default", "revision": 2,
            "schemaVersion": 1, "policyRevisionId": "revision-id", "policyDigest": policy_digest(policy),
            "capabilities": effective_capabilities("schemer", policy), "effectiveBounds": effective_bounds(policy),
        }
        snapshot = effective_chat_snapshot(settings, ["data"], target_verified=False, disclosure_class="data")
        self.assertEqual(snapshot["capabilities"]["raw_read"]["effectiveMode"], "disabled")


if __name__ == "__main__":
    unittest.main()
