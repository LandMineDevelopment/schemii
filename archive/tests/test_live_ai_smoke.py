import unittest

from tests.live_ai_smoke import anonymous_free_models, validate_response


class LiveAiSmokeHelperTests(unittest.TestCase):
    def test_model_selection_accepts_only_active_anonymous_free_catalog(self):
        status = {"providers": [
            {"id": "opencode", "connected": True, "authenticated": False, "models": [
                {"id": "b-free", "name": "B", "status": "active", "toolCall": False},
                {"id": "a-free", "name": "A", "status": "active", "toolCall": True},
                {"id": "big-pickle", "name": "Big Pickle", "status": "active", "toolCall": True},
                {"id": "old-free", "name": "Old", "status": "deprecated", "toolCall": True},
            ]},
            {"id": "opencode", "connected": True, "authenticated": True, "models": [{"id": "key-free", "status": "active"}]},
        ]}
        models = anonymous_free_models(status)
        self.assertEqual([model.model_id for model in models], ["a-free", "b-free", "big-pickle"])
        self.assertTrue(models[0].tool_call)
        self.assertFalse(models[1].tool_call)

    def test_contract_requires_skill_before_tool_and_confirmation(self):
        scenario = {"skill": "connection-setup", "tool": "schema_connection_setup", "action": "connection_setup"}
        valid = {
            "parts": [
                {"type": "skill", "skill": "connection-setup", "status": "completed"},
                {"type": "tool", "tool": "schema_connection_setup", "status": "completed"},
            ],
            "actions": [{
                "type": "connection_setup",
                "requiresConfirmation": True,
                "host": "db.invalid",
                "port": 5432,
                "database": "smoke",
                "user": "smoke_reader",
                "sslmode": "verify-full",
                "requiresPasswordEntry": True,
            }],
        }
        self.assertEqual(validate_response(valid, scenario, native_tools=True), [])
        invalid = {"parts": list(reversed(valid["parts"])), "actions": [{"type": "connection_setup"}]}
        errors = validate_response(invalid, scenario, native_tools=True)
        self.assertTrue(any("not loaded before" in error for error in errors))
        self.assertTrue(any("does not require" in error for error in errors))

    def test_migration_guard_rejects_actions_without_a_target(self):
        scenario = {"skill": "migration-safety", "tool": None, "action": None}
        valid = {"text": "Select an exact target and create a preview before UI confirmation.", "parts": [
            {"type": "skill", "skill": "migration-safety", "status": "completed"},
        ], "actions": []}
        self.assertEqual(validate_response(valid, scenario, native_tools=True), [])
        invalid = {**valid, "actions": [{"type": "migration_apply"}]}
        self.assertTrue(any("forbidden" in error for error in validate_response(invalid, scenario, native_tools=True)))

    def test_table_contract_requires_design_skill_and_confirmed_proposal(self):
        scenario = {"skill": "schema-design-layout", "tool": "schema_add_table", "action": "add_table"}
        valid = {
            "parts": [
                {"type": "skill", "skill": "schema-design-layout", "status": "completed"},
                {"type": "tool", "tool": "schema_add_table", "status": "completed"},
            ],
            "actions": [{
                "type": "add_table",
                "name": "contract_events",
                "purpose": "Store contract events",
                "columns": [
                    {"name": "id", "type": "uuid", "primary": True, "nullable": False},
                    {"name": "event_name", "type": "text", "nullable": False},
                ],
                "requiresConfirmation": True,
            }],
        }
        self.assertEqual(validate_response(valid, scenario, native_tools=True), [])


if __name__ == "__main__":
    unittest.main()
