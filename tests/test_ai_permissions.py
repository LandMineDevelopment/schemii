import json
import re
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from schemii.opencode_service import CUSTOM_TOOLS, PROMPT_TOOLS, SAFE_SKILLS, TOOL_ACTION_TYPES
from schemii.schemer_ai import SCHEMER_AI_SKILLS, SCHEMER_AI_TOOL_ACTION_TYPES
from schemii.ai_tool_contracts import (
    AI_TOOL_CONTRACTS,
    SCHEMII_TOOL_CONTRACTS,
    SCHEMER_TOOL_CONTRACTS,
    action_authority,
    effective_schemii_contract,
)


class AiPermissionContractTests(unittest.TestCase):
    def test_embedded_agent_is_default_deny_with_exact_tool_and_skill_allowlists(self):
        config = json.loads((ROOT / "ai/workspace/opencode.json").read_text())
        permission = config["permission"]
        tool_files = {path.stem for path in (ROOT / "ai/workspace/.opencode/tools").glob("schema_*.ts")}
        skill_dirs = {path.parent.name for path in (ROOT / "ai/workspace/.opencode/skills").glob("*/SKILL.md")}

        self.assertEqual(permission["*"], "deny")
        self.assertEqual(tool_files, CUSTOM_TOOLS)
        self.assertEqual(set(TOOL_ACTION_TYPES), CUSTOM_TOOLS)
        self.assertEqual({name for name in CUSTOM_TOOLS if permission.get(name) == "allow"}, CUSTOM_TOOLS)
        self.assertEqual(skill_dirs, SAFE_SKILLS)
        self.assertEqual(permission["skill"]["*"], "deny")
        self.assertEqual({name for name, value in permission["skill"].items() if value == "allow"}, SAFE_SKILLS)
        for denied in ("bash", "shell", "read", "edit", "write", "apply_patch", "glob", "grep", "list", "webfetch", "websearch", "task", "mcp"):
            self.assertEqual(permission[denied], "deny")
            self.assertFalse(PROMPT_TOOLS.get(denied, False))
        self.assertEqual(config["share"], "disabled")
        self.assertFalse(config["snapshot"])
        self.assertFalse(config["formatter"])
        self.assertFalse(config["lsp"])
        self.assertEqual(config["mcp"], {})

    def test_tool_inputs_match_backend_action_registry(self):
        for tool_name in TOOL_ACTION_TYPES:
            source = (ROOT / f"ai/workspace/.opencode/tools/{tool_name}.ts").read_text()
            self.assertIn('return "Proposal arguments received."', source)
            self.assertNotIn("SCHEMII_ACTION:", source)
            self.assertNotRegex(source, re.compile(r"^\s*password\s*:", re.MULTILINE | re.IGNORECASE))
            self.assertNotRegex(source.lower(), r'filesystem|shell|webfetch')

    def test_declarative_tool_contracts_match_every_opencode_registration(self):
        expected = {
            "schemii": (ROOT / "ai/workspace/.opencode/tools", SCHEMII_TOOL_CONTRACTS, TOOL_ACTION_TYPES),
            "schemer": (ROOT / "ai/schemer-workspace/.opencode/tools", SCHEMER_TOOL_CONTRACTS, SCHEMER_AI_TOOL_ACTION_TYPES),
        }
        self.assertEqual(set(AI_TOOL_CONTRACTS), set(expected))
        for application, (directory, contracts, registrations) in expected.items():
            with self.subTest(application=application):
                self.assertEqual(set(contracts), {path.stem for path in directory.glob("*.ts")})
                self.assertEqual({name: item.action_type for name, item in contracts.items()}, registrations)
                for name, contract in contracts.items():
                    self.assertEqual(contract.name, name)
                    self.assertEqual(contract.supported_app, application)
                    self.assertTrue((ROOT / contract.schema).is_file())
                    self.assertTrue(callable(contract.normalizer))
                    self.assertTrue(callable(contract.approval_floor))
                    self.assertIsInstance(contract.executor_adapter, str)
                    self.assertTrue(contract.executor_adapter)

    def test_schemer_sql_and_dashboard_creation_have_exact_authority_contracts(self):
        self.assertEqual(SCHEMER_TOOL_CONTRACTS["schemer_read_query"].capability, "raw_read")
        self.assertEqual(SCHEMER_TOOL_CONTRACTS["schemer_read_query"].approval_floor({}), "every_action")
        self.assertEqual(SCHEMER_TOOL_CONTRACTS["schemer_dashboard_create"].capability, "dashboard_write")
        self.assertEqual(SCHEMER_TOOL_CONTRACTS["schemer_dashboard_create"].approval_floor({}), "every_action")

    def test_server_issued_schemii_actions_have_exact_authority_contracts(self):
        child = {
            "type": "add_table", "name": "events", "purpose": "Track events",
            "columns": [{"name": "id", "type": "bigint"}], "requiresConfirmation": True,
        }
        batch = {"type": "schema_batch", "actions": [child, {**child, "name": "logs"}], "requiresConfirmation": True}
        migration = {
            "type": "migration_apply", "profileId": "local", "database": "demo", "namespace": "public",
            "planId": "ai_plan_one", "destructive": True, "reviewDigest": "a" * 64,
            "requiresConfirmation": True,
        }
        reviewed = {
            "applyPlanId": "ai_plan_two", "planDigest": "b" * 64, "effectsDigest": "c" * 64,
            "rowCount": 2,
        }
        write = {
            "type": "postgres_write_apply", "writeKind": "insert_rows", "profileId": "local",
            "database": "demo", "namespace": "public", "relation": "events", "planId": "ai_plan_two",
            "reviewDigest": "b" * 64, "effectsDigest": "c" * 64, "rowCount": 2,
            "reviewedPlan": reviewed, "requiresConfirmation": True,
        }

        self.assertEqual(effective_schemii_contract(batch), ("schema", None))
        self.assertEqual(action_authority("schemii", batch, "schema", "automatic", origin="model"), ("schema", "automatic"))
        self.assertEqual(action_authority("schemii", migration, "schema", "automatic", origin="server_apply"), ("schema", "every_action"))
        self.assertEqual(action_authority("schemii", write, "structured_write", "every_action", origin="server_apply"), ("structured_write", "every_action"))

        for tampered in (
            {**batch, "unexpected": True},
            {**migration, "reviewDigest": "not-a-digest"},
            {**write, "rowCount": 3},
        ):
            with self.subTest(action=tampered["type"]), self.assertRaises(ValueError):
                action_authority("schemii", tampered, "schema" if tampered["type"] != "postgres_write_apply" else "write", "every_action", origin="model" if tampered["type"] == "schema_batch" else "server_apply")
        with self.assertRaises(ValueError):
            action_authority("schemii", migration, "schema", "every_action", origin="model")
        with self.assertRaises(ValueError):
            action_authority("schemii", migration, "schema", "every_action")
        with self.assertRaises(ValueError):
            effective_schemii_contract({"type": "unknown_server_action"})

    def test_agent_guidance_exposes_table_proposals_without_write_bypasses(self):
        workspace = ROOT / "ai/workspace"
        instructions = (workspace / "AGENTS.md").read_text()
        design_skill = (workspace / ".opencode/skills/schema-design-layout/SKILL.md").read_text()
        help_skill = (workspace / ".opencode/skills/schemii-help/SKILL.md").read_text()
        target_skill = (workspace / ".opencode/skills/target-selection/SKILL.md").read_text()
        add_table = (workspace / ".opencode/tools/schema_add_table.ts").read_text()
        populate = (workspace / ".opencode/tools/schema_populate.ts").read_text()
        read_query = (workspace / ".opencode/tools/schema_read_query.ts").read_text()

        combined = "\n".join((instructions, design_skill, help_skill, target_skill)).lower()
        self.assertNotIn("temporarily unavailable", combined)
        self.assertIn("create, add, design", design_skill.lower())
        self.assertIn("schema_add_table", design_skill)
        self.assertIn("schema_populate", design_skill)
        self.assertIn("durable, confirmed saved-design proposals", instructions.lower())
        self.assertIn("server-issued apply proposal", help_skill.lower())
        self.assertIn("do not directly create it in postgresql", add_table.lower())
        self.assertIn("never insert rows", populate.lower())
        self.assertIn("cannot insert, update, delete, create tables, or create views", read_query.lower())
        self.assertIn("only schemii may issue its separate apply proposal", help_skill.lower())
        self.assertIn("schema_insert_rows_preview", help_skill)
        self.assertIn("schema_create_view_preview", help_skill)
        self.assertIn("enable its matching checkbox", instructions)
        self.assertIn("Do not say the capability is unsupported", instructions)

    def test_schemii_runtime_has_no_json_authority_fallback(self):
        source = (ROOT / "src/schemii/server.py").read_text()
        self.assertNotIn("AiAuthority(", source)
        self.assertNotIn("AiChatStore(", source)
        self.assertIn("SchemiiMetadataAuthority(metadata_store", source)
        self.assertIn("metadata_store.health()", source)

    def test_compose_keeps_workspace_read_only_and_opencode_private_by_default(self):
        ai_compose = (ROOT / "compose.ai.yaml").read_text()
        local_override = (ROOT / "compose.ai.local-db.yaml").read_text()
        self.assertIn("./ai/workspace:/workspace:ro", ai_compose)
        self.assertNotRegex(ai_compose, r'ports:\s*\n\s*-\s*["\']?[^\n]*4096')
        self.assertIn("services: {}", local_override)
        self.assertNotIn("ports:", local_override)
        self.assertNotIn('"0.0.0.0:', local_override)
        self.assertIn("OPENCODE_DISABLE_EXTERNAL_SKILLS: 1", ai_compose)
        self.assertIn("OPENCODE_DISABLE_CLAUDE_CODE_SKILLS: 1", ai_compose)
        self.assertIn("condition: service_healthy", ai_compose)
        self.assertIn("http://127.0.0.1:4096/global/health", ai_compose)
        self.assertIn("Authorization: Basic $$credentials", ai_compose)

    def test_schemer_agent_has_separate_default_deny_workspace(self):
        root = ROOT / "ai/schemer-workspace"
        config = json.loads((root / "opencode.json").read_text())
        permission = config["permission"]
        tools = {path.stem for path in (root / ".opencode/tools").glob("schemer_*.ts")}
        skills = {path.parent.name for path in (root / ".opencode/skills").glob("*/SKILL.md")}
        self.assertEqual(tools, set(SCHEMER_AI_TOOL_ACTION_TYPES))
        self.assertEqual(skills, SCHEMER_AI_SKILLS)
        self.assertIn("schemer-order-safety", skills)
        self.assertNotIn("schemer-layout-safety", skills)
        self.assertNotIn("schemer-layout-safety", (root / "opencode.json").read_text())
        self.assertNotIn("schemer-layout-safety", (root / "AGENTS.md").read_text())
        self.assertEqual({name for name in tools if permission.get(name) == "allow"}, tools)
        self.assertEqual({name for name, value in permission["skill"].items() if value == "allow"}, skills)
        self.assertEqual(permission["*"], "deny")
        for denied in ("bash", "shell", "read", "edit", "write", "apply_patch", "glob", "grep", "list", "webfetch", "websearch", "task", "mcp"):
            self.assertEqual(permission[denied], "deny")
        for tool_name in SCHEMER_AI_TOOL_ACTION_TYPES:
            source = (root / f".opencode/tools/{tool_name}.ts").read_text()
            self.assertIn('return "Proposal arguments received."', source)
            self.assertNotIn("SCHEMER_ACTION:", source)
            self.assertNotRegex(source.lower(), r"password|filesystem|shell")
            if tool_name == "schemer_read_query":
                self.assertIn("database:", source)
                self.assertIn("sql:", source)
            else:
                self.assertNotRegex(source.lower(), r"sql:")
            if tool_name == "schemer_widget_create":
                self.assertIn("source:", source)
                self.assertIn("query:", source)
                self.assertIn("visualizationMode", source)
                self.assertIn("must be supplied together", source)
                self.assertIn('aggregation: tool.schema.literal("count_rows")', source)
                self.assertIn('column: tool.schema.null()', source)
                self.assertNotRegex(source, r"widgetId:|layout:")

    def test_schemer_reuses_private_opencode_credentials_without_mounting_provider_data(self):
        overlay = (ROOT / "compose.schemer.ai.yaml").read_text()
        ai_compose = (ROOT / "compose.ai.yaml").read_text()
        self.assertIn("SCHEMER_OPENCODE_URL: http://opencode:4096", overlay)
        self.assertIn("SCHEMER_OPENCODE_USERNAME: ${SCHEMII_OPENCODE_USERNAME", overlay)
        self.assertIn("SCHEMER_OPENCODE_PASSWORD_FILE: /run/secrets/opencode_password", overlay)
        self.assertIn("- opencode_password", overlay)
        self.assertIn("SCHEMER_OPENCODE_TIMEOUT: ${SCHEMII_OPENCODE_TIMEOUT:-120}", overlay)
        self.assertIn("SCHEMII_OPENCODE_TIMEOUT: ${SCHEMII_OPENCODE_TIMEOUT:-300}", ai_compose)
        self.assertIn("./ai/schemer-workspace:/workspace-schemer:ro", overlay)
        self.assertIn("condition: service_healthy", overlay)
        self.assertNotIn("schemii-opencode-data:/", overlay)


if __name__ == "__main__":
    unittest.main()
