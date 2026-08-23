import unittest

from schemii.ai_http import ai_conversation_title, ensure_ai_conversation_title, issue_ai_proposals


class _Authority:
    def __init__(self):
        self.created = []

    def create_proposal(self, session_id, action, binding, authorization_target, schema_concurrency):
        self.created.append(action)
        return {"id": "proposal-1", "action": action, "policyBinding": binding}


class AiProposalIssuanceTests(unittest.TestCase):
    def test_rejected_model_action_is_visible_and_never_queued(self):
        authority = _Authority()
        response = issue_ai_proposals(
            authority,
            {"text": "Proposal submitted.", "actions": [{"type": "raw_write"}]},
            application="schemii",
            session_id="chat-1",
            resource="schema-1",
            access="schema-rawwrite",
            authorization_target={},
            schema_concurrency={"revision": 1, "layoutToken": "layout-1"},
            normalize_action=lambda _action, _access: (_ for _ in ()).throw(ValueError("invalid model input")),
        )

        self.assertEqual(authority.created, [])
        self.assertEqual(response["proposals"], [])
        self.assertEqual(response["proposalDiagnostics"][0]["code"], "proposal_validation_failed")
        self.assertIn("No action was queued", response["proposalDiagnostics"][0]["message"])
        self.assertNotIn("invalid model input", str(response))

    def test_valid_model_action_does_not_emit_a_failure_diagnostic(self):
        authority = _Authority()
        action = {"type": "raw_write", "purpose": "Load rows"}
        response = issue_ai_proposals(
            authority,
            {"text": "Review it.", "actions": [action]},
            application="schemii",
            session_id="chat-1",
            resource="schema-1",
            access="schema-rawwrite",
            authorization_target={},
            schema_concurrency={"revision": 1, "layoutToken": "layout-1"},
            normalize_action=lambda item, _access: item,
            policy_binding=lambda _action: {"effectiveMode": "every_action"},
        )

        self.assertEqual(authority.created, [action])
        self.assertEqual(response["proposals"][0]["proposalId"], "proposal-1")
        self.assertNotIn("proposalDiagnostics", response)


class AiConversationTitleTests(unittest.TestCase):
    def test_marker_only_messages_remain_valid_titles(self):
        for message in ("#", "---", "***"):
            with self.subTest(message=message):
                self.assertEqual(ai_conversation_title(message), message)

    def test_markdown_prefix_is_removed_when_content_remains(self):
        self.assertEqual(ai_conversation_title("## Review migration plan"), "Review migration plan")

    def test_invalid_lazy_title_seed_does_not_break_history(self):
        class Service:
            @staticmethod
            def session_title_seed(_external_session_id):
                return "invalid\x00seed"

        class Authority:
            @staticmethod
            def initialize_conversation_title(*_args):
                raise AssertionError("invalid seed must not be persisted")

        chat = {"id": "chat-1", "externalSessionId": "external-1", "conversationTitle": None}
        self.assertIs(ensure_ai_conversation_title(Service(), Authority(), chat), chat)


if __name__ == "__main__":
    unittest.main()
