import base64
import json
import sys
import unittest
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError, URLError


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from schemii.opencode_service import CUSTOM_TOOLS, OpenCodeService, OpenCodeServiceError


class Response:
    def __init__(self, payload):
        self.body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self, size=-1):
        return self.body[:size]


class ActivityResponse(Response):
    def __init__(self, events):
        lines = []
        for event in events:
            lines.extend(("event: message\n", f"data: {json.dumps(event)}\n", "\n"))
        self.stream = BytesIO("".join(lines).encode("utf-8"))

    def readline(self, size=-1):
        return self.stream.readline(size)


class Opener:
    def __init__(self, *payloads):
        self.payloads = list(payloads)
        self.calls = []

    def __call__(self, request, timeout):
        self.calls.append((request, timeout))
        payload = self.payloads.pop(0)
        if isinstance(payload, Exception):
            raise payload
        if isinstance(payload, Response):
            return payload
        return Response(payload)


def request_json(request):
    return None if request.data is None else json.loads(request.data)


class OpenCodeServiceTests(unittest.TestCase):
    def service(self, opener):
        return OpenCodeService("http://127.0.0.1:4096/", "opencode", "secret", 12, opener=opener)

    def test_status_uses_fixed_discovery_paths_and_redacts_provider_secrets(self):
        opener = Opener(
            {"healthy": True, "version": "1.18.15"},
            {
                "all": [
                    {
                        "id": "anthropic", "name": "Anthropic", "key": "provider-secret",
                        "options": {"apiKey": "option-secret"},
                        "models": {"claude": {"id": "claude", "name": "Claude", "tool_call": True, "headers": {"Authorization": "secret"}}},
                    },
                    {"id": "opencode", "name": "OpenCode", "models": {
                        "deepseek-v4-flash-free": {"id": "deepseek-v4-flash-free", "name": "DeepSeek Free", "cost": {"input": 0}},
                        "north-mini-code-free": {"id": "north-mini-code-free", "name": "North Free", "cost": {"input": 0}},
                    }},
                ],
                "default": {"anthropic": "claude", "opencode": "deepseek-v4-flash-free"}, "connected": ["anthropic", "opencode"],
            },
            {"anthropic": [{"type": "api", "label": "API key"}]},
            [{"name": "schemii-help", "description": "Product help"}],
        )
        result = self.service(opener).status()

        self.assertEqual([call[0].full_url for call in opener.calls], [
            "http://127.0.0.1:4096/global/health",
            "http://127.0.0.1:4096/provider",
            "http://127.0.0.1:4096/provider/auth",
            "http://127.0.0.1:4096/skill",
        ])
        expected_auth = "Basic " + base64.b64encode(b"opencode:secret").decode("ascii")
        self.assertTrue(all(call[0].headers["Authorization"] == expected_auth for call in opener.calls))
        self.assertEqual(result["version"], "1.18.15")
        self.assertEqual(result["providers"][0]["models"][0]["id"], "claude")
        self.assertTrue(result["providers"][0]["connected"])
        zen = next(provider for provider in result["providers"] if provider["id"] == "opencode")
        self.assertTrue(zen["connected"])
        self.assertFalse(zen["authenticated"])
        self.assertEqual([model["id"] for model in zen["models"]], ["deepseek-v4-flash-free", "north-mini-code-free"])
        self.assertEqual(result["authMethods"]["opencode"][0]["helpUrl"], "https://opencode.ai/auth")
        self.assertEqual(result["authMethods"]["anthropic"][0]["id"], 0)
        self.assertEqual(result["skills"][0]["name"], "schemii-help")
        self.assertNotIn("secret", json.dumps(result).lower())

    def test_auth_oauth_and_session_methods_pin_upstream_shapes(self):
        opener = Opener(True, True, {"url": "https://login.example/", "method": "code", "instructions": "Enter code"}, True, {"id": "ses_1", "title": "Chat", "directory": "/workspace"}, {"id": "ses_1", "directory": "/workspace"}, True)
        service = self.service(opener)

        self.assertEqual(service.set_api_key("anthropic", "api-secret"), {"saved": True})
        self.assertEqual(service.delete_api_key("anthropic"), {"deleted": True})
        service.oauth_authorize("anthropic", 1, {"region": "us"})
        service.oauth_callback("anthropic", 1, "callback-code")
        self.assertEqual(service.create_session("Chat", {"providerID": "openrouter", "modelID": "anthropic/claude"}), {"id": "ses_1", "title": "Chat"})
        self.assertEqual(service.delete_session("ses_1"), {"deleted": True})

        self.assertEqual([(call[0].method, call[0].full_url.removeprefix("http://127.0.0.1:4096"), request_json(call[0])) for call in opener.calls], [
            ("PUT", "/auth/anthropic", {"type": "api", "key": "api-secret"}),
            ("DELETE", "/auth/anthropic", None),
            ("POST", "/provider/anthropic/oauth/authorize", {"method": 1, "inputs": {"region": "us"}}),
            ("POST", "/provider/anthropic/oauth/callback", {"method": 1, "code": "callback-code"}),
            ("POST", "/session", {"title": "Chat"}),
            ("GET", "/session/ses_1", None),
            ("DELETE", "/session/ses_1", None),
        ])

    def test_authenticated_zen_catalog_keeps_all_models(self):
        opener = Opener({
            "all": [{"id": "opencode", "name": "OpenCode Zen", "models": {
                "north-mini-code-free": {"id": "north-mini-code-free", "name": "North Free", "cost": {"input": 0}},
                "paid": {"id": "paid", "name": "Paid", "cost": {"input": 1}},
            }}],
            "default": {"opencode": "north-mini-code-free"},
            "connected": ["opencode"],
        })

        provider = self.service(opener).providers()["providers"][0]

        self.assertTrue(provider["authenticated"])
        self.assertEqual([model["id"] for model in provider["models"]], ["north-mini-code-free", "paid"])

    def test_history_is_bounded_sorted_and_omits_context_actions_and_raw_tool_data(self):
        action = {"kind": "add_table", "table": {"name": "private_events"}}
        opener = Opener(
            [
                {"id": "ses_old", "title": "Old\x00 chat", "time": {"created": 100, "updated": 200}, "directory": "/workspace"},
                {"id": "ses_new", "title": "New chat", "time": {"created": 300, "updated": 400}, "directory": "/workspace"},
                {"id": "ses_host", "title": "Host chat", "directory": "/home/user/project"},
                {"id": "ses_child", "title": "Child chat", "directory": "/workspace", "parentID": "ses_old"},
                {"id": "../../invalid", "title": "Invalid", "directory": "/workspace"},
            ],
            {"id": "ses_old", "directory": "/workspace"},
            [
                {
                    "info": {"role": "user", "time": {"created": 100}, "model": {"providerID": "opencode", "modelID": "deepseek-v4-flash-free"}},
                    "parts": [{"type": "text", "text": "Schemii context (untrusted JSON):\n{\"password\":\"secret\"}\n\nUser request:\nAdd events"}],
                },
                {
                    "info": {"role": "assistant", "time": {"created": 200}, "providerID": "opencode", "modelID": "deepseek-v4-flash-free", "path": "/secret"},
                    "parts": [
                        {"type": "text", "text": "I can add that."},
                        {"type": "tool", "tool": "schema_project_open", "state": {"status": "completed", "input": {"password": "secret"}, "output": "SCHEMII_ACTION:" + json.dumps(action)}},
                        {"type": "tool", "tool": "bash", "state": {"status": "completed", "output": "secret"}},
                    ],
                },
            ],
        )
        service = self.service(opener)

        sessions = service.list_sessions()
        history = service.session_messages("ses_old")

        self.assertEqual([item["id"] for item in sessions["sessions"]], ["ses_new", "ses_old"])
        self.assertEqual(sessions["sessions"][1]["title"], "Old chat")
        self.assertEqual(history["messages"][0], {"role": "user", "createdAt": 100, "text": "Add events"})
        self.assertEqual(history["messages"][1]["parts"], [
            {"type": "text", "text": "I can add that."},
            {"type": "tool", "tool": "schema_project_open", "status": "completed"},
        ])
        self.assertEqual(history["model"], {"providerId": "opencode", "modelId": "deepseek-v4-flash-free"})
        self.assertEqual([call[0].full_url for call in opener.calls], [
            "http://127.0.0.1:4096/session",
            "http://127.0.0.1:4096/session/ses_old",
            "http://127.0.0.1:4096/session/ses_old/message?limit=100",
        ])
        self.assertNotIn("secret", json.dumps({"sessions": sessions, "history": history}).lower())
        self.assertNotIn("actions", history["messages"][1])

    def test_history_rejects_a_session_from_outside_the_docker_workspace(self):
        service = self.service(Opener({"id": "ses_host", "directory": "/home/user/project"}))

        with self.assertRaises(OpenCodeServiceError) as error:
            service.session_messages("ses_host")

        self.assertEqual(error.exception.status, 404)
        self.assertEqual(error.exception.code, "not_found")

    def test_workspace_and_tool_policy_isolate_schemer_sessions(self):
        action = {"type": "dashboard_create", "title": "Sales", "requiresConfirmation": True}
        opener = Opener(
            {"id": "ses_dash", "title": "Dashboard", "directory": "/workspace-schemer"},
            {"id": "ses_dash", "directory": "/workspace-schemer"},
            {"parts": [
                {"type": "tool", "tool": "schemer_dashboard_create", "state": {"status": "completed", "input": {"title": "Sales"}, "output": "Created proposal input."}},
                {"type": "tool", "tool": "schema_add_table", "state": {"status": "completed", "output": "SCHEMII_ACTION:{}"}},
            ]},
        )
        service = OpenCodeService(
            "http://127.0.0.1:4096", "opencode", "secret", 12, opener=opener,
            workspace="/workspace-schemer",
            custom_tools={"schemer_dashboard_create"},
            tool_action_types={"schemer_dashboard_create": "dashboard_create"},
            safe_skills={"schemer-help"},
        )

        self.assertEqual(service.create_session("Dashboard"), {"id": "ses_dash", "title": "Dashboard"})
        result = service.prompt("ses_dash", "Create sales", {"providerId": "openai", "modelId": "gpt"}, "Schemer system")

        self.assertEqual(result["actions"], [action])
        self.assertEqual([part.get("tool") for part in result["parts"]], ["schemer_dashboard_create"])
        payload = request_json(opener.calls[2][0])
        self.assertTrue(payload["tools"]["schemer_dashboard_create"])
        self.assertNotIn("schema_add_table", payload["tools"])
        self.assertTrue(all(call[0].get_header("X-opencode-directory") == "/workspace-schemer" for call in opener.calls))

        outside = OpenCodeService(
            "http://127.0.0.1:4096", "opencode", "secret", opener=Opener({"id": "ses_schema", "directory": "/workspace"}),
            workspace="/workspace-schemer", custom_tools=set(), tool_action_types={}, safe_skills=set(),
        )
        with self.assertRaises(OpenCodeServiceError):
            outside.verify_session("ses_schema")

    def test_configured_data_tools_are_the_only_tools_toggled(self):
        action = {"type": "read_query", "profileId": "shared", "database": "demo", "namespace": "public", "sql": "SELECT 1"}
        opener = Opener(
            {"id": "ses_data", "directory": "/workspace-schemer"},
            {"parts": [
                {"type": "text", "text": "Data access is disabled."},
                {"type": "tool", "tool": "schemer_read_query", "state": {"status": "completed", "input": {"profileId": "shared", "database": "demo", "namespace": "public", "sql": "SELECT 1"}, "output": "Created proposal input."}},
            ]},
            [],
        )
        service = OpenCodeService(
            "http://127.0.0.1:4096", "opencode", "secret", opener=opener, workspace="/workspace-schemer",
            custom_tools={"schemer_read_query", "schemer_dashboard_create"},
            tool_action_types={"schemer_read_query": "read_query", "schemer_dashboard_create": "dashboard_create"},
            safe_skills=set(), data_tools={"schemer_read_query"},
        )
        result = service.prompt("ses_data", "Read", {"providerId": "openai", "modelId": "gpt"}, "system", allow_data=False)
        payload = request_json(opener.calls[1][0])
        self.assertFalse(payload["tools"]["schemer_read_query"])
        self.assertTrue(payload["tools"]["schemer_dashboard_create"])
        self.assertEqual(result["actions"], [])

    def test_schema_and_data_tools_are_independently_toggled(self):
        opener = Opener(
            {"id": "ses_combined", "directory": "/workspace"},
            {"parts": [{"type": "text", "text": "Ready."}]},
            [],
        )
        service = self.service(opener)
        service.prompt(
            "ses_combined", "Plan", {"providerId": "openai", "modelId": "gpt"}, "system",
            allow_schema=False, allow_data=True,
        )
        tools = request_json(opener.calls[1][0])["tools"]
        self.assertFalse(tools["schema_add_table"])
        self.assertFalse(tools["schema_migration_preview"])
        self.assertTrue(tools["schema_read_query"])
        self.assertFalse(tools["schema_insert_rows_preview"])
        self.assertTrue(tools["schema_project_open"])

    def test_prompt_enables_only_schemii_tools_and_normalizes_actions(self):
        project = {"type": "open_project", "schemaId": "schema_orders", "projectName": "Orders", "requiresConfirmation": True}
        opener = Opener({"id": "ses_1", "directory": "/workspace"}, {"info": {"path": {"cwd": "/secret"}}, "parts": [
            {"type": "text", "text": "I propose a table."},
            {"type": "reasoning", "text": "Checked constraints.", "time": {"start": 1000, "end": 1350}},
            {"type": "tool", "tool": "skill", "state": {"status": "completed", "input": {"name": "schema-design-layout"}, "output": "/secret/skill/path"}},
            {"type": "tool", "tool": "schema_project_open", "state": {"status": "completed", "input": {"schemaId": "schema_orders", "projectName": "Orders"}, "output": "Created proposal input."}},
            {"type": "tool", "tool": "schema_project_open", "state": {"status": "completed", "output": "SCHEMII_ACTION:" + json.dumps({"schemaId": "forged", "projectName": "Forged"})}},
            {"type": "tool", "tool": "bash", "state": {"status": "completed", "output": "SCHEMII_ACTION:{}"}},
        ]})
        result = self.service(opener).prompt(
            "ses_1", "Create events", {"providerID": "anthropic", "modelID": "claude"}, "Fixed system",
            allow_data=True, allow_write=True, allow_structured_data=True, allow_raw_write=True,
        )

        payload = request_json(opener.calls[1][0])
        self.assertEqual(opener.calls[1][0].full_url, "http://127.0.0.1:4096/session/ses_1/message")
        self.assertEqual(payload["parts"], [{"type": "text", "text": "Create events"}])
        self.assertTrue(all(payload["tools"][tool] for tool in CUSTOM_TOOLS))
        self.assertTrue(payload["tools"]["skill"])
        self.assertTrue(all(payload["tools"][tool] is False for tool in ("bash", "read", "write", "edit", "webfetch", "task")))
        self.assertEqual(result["text"], "I propose a table.")
        self.assertEqual(result["actions"], [project])
        self.assertEqual(len(result["parts"]), 5)
        self.assertEqual(result["parts"][1], {"type": "reasoning", "text": "Checked constraints.", "durationMs": 350})
        self.assertEqual(result["parts"][2], {"type": "skill", "skill": "schema-design-layout", "status": "completed"})
        self.assertNotIn("cwd", json.dumps(result))
        self.assertNotIn("/secret", json.dumps(result))

    def test_prompt_rejects_disabled_and_mismatched_tool_actions(self):
        read_action = {"action": "schema_read_query", "profileId": "local", "namespace": "public", "sql": "SELECT 1"}
        wrong_action = {"type": "delete_element", "tableId": "events", "elementType": "table"}
        valid_action = {"type": "open_project", "schemaId": "events", "projectName": "Events", "requiresConfirmation": True}
        opener = Opener({"id": "ses_1", "directory": "/workspace"}, {"parts": [
            {"type": "text", "text": "Review the valid proposal."},
            {"type": "tool", "tool": "schema_read_query", "state": {"status": "completed", "input": {"profileId": "local", "namespace": "public", "sql": "SELECT 1"}, "output": "Created proposal input."}},
            {"type": "tool", "tool": "schema_project_open", "state": {"status": "completed", "input": wrong_action, "output": "Created proposal input."}},
            {"type": "tool", "tool": "schema_project_open", "state": {"status": "completed", "input": {"schemaId": "events", "projectName": "Events"}, "output": "Created proposal input."}},
        ]})

        result = self.service(opener).prompt(
            "ses_1", "Create events without data access", {"providerID": "opencode", "modelID": "free"}, "Fixed system",
            allow_data=False,
        )

        payload = request_json(opener.calls[1][0])
        self.assertFalse(payload["tools"]["schema_read_query"])
        self.assertEqual(result["actions"], [valid_action])
        self.assertNotIn("schema_read_query", [part.get("tool") for part in result["parts"]])
        self.assertEqual([part.get("tool") for part in result["parts"] if part["type"] == "tool"], ["schema_project_open", "schema_project_open"])

    def test_disabled_invalid_ids_and_upstream_failures_are_sanitized(self):
        self.assertEqual(OpenCodeService("", "opencode", "").status()["enabled"], False)
        opener = Opener(HTTPError("http://upstream/private", 401, "bad", {}, BytesIO(b'{"key":"leaked"}')))
        service = self.service(opener)
        with self.assertRaises(OpenCodeServiceError) as error:
            service.set_api_key("anthropic", "top-secret")
        self.assertEqual(error.exception.payload["error"]["message"], "OpenCode rejected the request")
        self.assertNotIn("secret", str(error.exception.payload))
        with self.assertRaises(OpenCodeServiceError):
            service.delete_session("../../global/health")

        unavailable = self.service(Opener(URLError("connection detail with password")))
        with self.assertRaises(OpenCodeServiceError) as error:
            unavailable.health()
        self.assertEqual(error.exception.payload["error"]["code"], "opencode_unavailable")

    def test_prompt_timeout_has_bounded_abort_and_clear_error(self):
        opener = Opener({"id": "ses_1", "directory": "/workspace"}, TimeoutError("provider stalled"), TimeoutError("abort stalled"))
        service = self.service(opener)

        with self.assertRaises(OpenCodeServiceError) as error:
            service.prompt("ses_1", "Hello", {"providerID": "openai", "modelID": "gpt"}, "Fixed system")

        self.assertEqual(error.exception.status, 504)
        self.assertEqual(error.exception.code, "provider_timeout")
        self.assertEqual([call[1] for call in opener.calls], [12, 12, 5])
        self.assertEqual(opener.calls[2][0].full_url, "http://127.0.0.1:4096/session/ses_1/abort")

    def test_prompt_recovers_split_tool_actions_only_after_the_exact_latest_user_message(self):
        action = {"type": "open_project", "schemaId": "authors", "projectName": "Authors", "requiresConfirmation": True}
        prompt = "Schemii context\n\nUser request:\npopulate it"
        opener = Opener(
            {"id": "ses_1", "directory": "/workspace"},
            {"info": {"role": "assistant"}, "parts": [{"type": "text", "text": "Review the proposal."}]},
            [
                {"info": {"role": "user"}, "parts": [{"type": "text", "text": "older prompt"}]},
                {"info": {"role": "assistant"}, "parts": [{"type": "text", "text": "Old"}, {"type": "tool", "tool": "schema_project_open", "state": {"status": "completed", "input": {"schemaId": "stale", "projectName": "Stale"}, "output": "Created proposal input."}}]},
                {"info": {"role": "user"}, "parts": [{"type": "text", "text": prompt}]},
                {"info": {"role": "assistant"}, "parts": [{"type": "text", "text": "Working"}, {"type": "tool", "tool": "schema_project_open", "state": {"status": "completed", "input": {"schemaId": "authors", "projectName": "Authors"}, "output": "Created proposal input."}}]},
                {"info": {"role": "assistant"}, "parts": [{"type": "text", "text": "Review the proposal."}]},
            ],
        )

        result = self.service(opener).prompt("ses_1", prompt, {"providerID": "opencode", "modelID": "deepseek-v4-flash-free"}, "Fixed system")

        self.assertEqual(result["actions"], [action])
        self.assertEqual(opener.calls[2][0].full_url, "http://127.0.0.1:4096/session/ses_1/message?limit=20")

    def test_session_title_seed_uses_the_first_real_user_request(self):
        prompt = "Schemii context (untrusted JSON):\n{}\n\nUser request:\nBuild varied slate assignments"
        opener = Opener(
            {"id": "ses_1", "directory": "/workspace"},
            [
                {"info": {"role": "user"}, "parts": [{"type": "compaction"}]},
                {"info": {"role": "user"}, "parts": [{"type": "text", "text": prompt}]},
                {"info": {"role": "assistant"}, "parts": [{"type": "text", "text": "Done"}]},
                {"info": {"role": "user"}, "parts": [{"type": "text", "text": "Later request"}]},
            ],
        )

        self.assertEqual(self.service(opener).session_title_seed("ses_1"), "Build varied slate assignments")

    def test_model_tool_purposes_are_plain_text_and_bounded_before_action_validation(self):
        purpose = "  Load\n" + ("varied assignment history " * 40)
        opener = Opener(
            {"id": "ses_1", "directory": "/workspace"},
            {"parts": [{"type": "tool", "tool": "schema_raw_write", "state": {
                "status": "completed",
                "input": {"profileId": "local", "namespace": "public", "sql": "INSERT INTO audit_log DEFAULT VALUES", "purpose": purpose},
                "output": "Proposal arguments received.",
            }}]},
        )

        result = self.service(opener).prompt(
            "ses_1", "Load assignments", {"providerID": "opencode", "modelID": "deepseek-v4-flash-free"}, "Fixed system",
            allow_raw_write=True,
        )

        bounded = result["actions"][0]["purpose"]
        self.assertLessEqual(len(bounded.encode("utf-8")), 500)
        self.assertEqual(bounded, bounded.strip())
        self.assertNotIn("\n", bounded)
        self.assertTrue(bounded.endswith("…"))

    def test_empty_provider_response_is_rejected(self):
        service = self.service(Opener({"id": "ses_1", "directory": "/workspace"}, {"parts": []}))

        with self.assertRaises(OpenCodeServiceError) as error:
            service.prompt("ses_1", "Hello", {"providerID": "opencode", "modelID": "north-mini-code-free"}, "Fixed system")

        self.assertEqual(error.exception.code, "provider_empty_response")

    def test_activity_stream_filters_session_and_sensitive_fields(self):
        events = [
            {"type": "server.connected", "properties": {}},
            {"type": "session.status", "properties": {"sessionID": "other", "status": {"type": "busy"}}},
            {"type": "session.status", "properties": {"sessionID": "ses_1", "status": {"type": "busy"}}},
            {"type": "message.part.updated", "properties": {"sessionID": "ses_1", "part": {"id": "prt_reason", "type": "reasoning", "text": "private reasoning", "time": {"start": 1}}}},
            {"type": "message.part.updated", "properties": {"sessionID": "ses_1", "part": {"id": "prt_tool", "type": "tool", "tool": "schema_project_open", "state": {"status": "running", "input": {"name": "secret"}, "metadata": {"path": "/secret"}}}}},
            {"type": "message.part.updated", "properties": {"sessionID": "ses_1", "part": {"id": "prt_shell", "type": "tool", "tool": "bash", "state": {"status": "running", "input": {"command": "cat /secret"}}}}},
            {"type": "message.part.updated", "properties": {"sessionID": "ses_1", "part": {"id": "prt_skill", "type": "tool", "tool": "skill", "state": {"status": "completed", "input": {"name": "migration-safety"}, "output": "/secret/skill"}}}},
            {"type": "session.status", "properties": {"sessionID": "ses_1", "status": {"type": "idle"}}},
        ]
        opener = Opener(ActivityResponse(events))

        result = list(self.service(opener).activity("ses_1"))

        self.assertEqual(result, [
            {"type": "connection", "state": "connected"},
            {"type": "session", "state": "busy"},
            {"type": "part", "kind": "reasoning", "key": "prt_reason", "state": "running"},
            {"type": "part", "kind": "tool", "key": "prt_tool", "tool": "schema_project_open", "state": "running"},
            {"type": "part", "kind": "skill", "key": "prt_skill", "skill": "migration-safety", "state": "completed"},
            {"type": "session", "state": "idle"},
        ])
        self.assertNotIn("secret", json.dumps(result).lower())


if __name__ == "__main__":
    unittest.main()
