import base64
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import Mock, patch


spec = importlib.util.spec_from_file_location(
    "browser_probe", Path(__file__).with_name("verify_browser_isolation.py")
)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class EphemeralConfigurationTests(unittest.TestCase):
    def configuration(self):
        return {
            "model": "inherited-model",
            "model_reasoning_effort": "inherited-effort",
            "mcp_servers": {
                probe.SERVER: {
                    "command": "python3",
                    "args": ["testing/agents/browser.py"],
                    "enabled_tools": sorted(
                        probe.REQUIRED_TOOLS - set(probe.TEST_ONLY_TOOLS)
                    ),
                    "disabled_tools": ["browser_evaluate", "browser_network_request"],
                },
                "other.with.dots": {
                    "url": "https://example.invalid/mcp",
                    "env": {"KEY": "private-value"},
                },
            },
            "plugins": {"example@catalog": {"enabled": True}},
        }

    def test_other_servers_and_plugins_disabled_only_in_throwaway_overrides(self):
        config = self.configuration()
        original = deepcopy(config)
        overrides = probe.ephemeral_overrides(config)
        self.assertFalse(overrides["mcp_servers"]["other.with.dots"]["enabled"])
        self.assertFalse(overrides["plugins"]["example@catalog"]["enabled"])
        self.assertTrue(overrides["mcp_servers"][probe.SERVER]["enabled"])
        self.assertEqual(config, original)
        self.assertFalse(any(key.startswith("model") for key in overrides))

    def test_override_uses_json_tables_without_literal_quotes_or_path_traversal(self):
        config = self.configuration()
        overrides = probe.ephemeral_overrides(config)
        self.assertEqual(set(overrides), {"mcp_servers", "plugins", "features.apps"})
        self.assertEqual(set(overrides["mcp_servers"]), set(config["mcp_servers"]))
        self.assertEqual(set(overrides["plugins"]), set(config["plugins"]))
        self.assertEqual(overrides["mcp_servers"][probe.SERVER]["command"], "python3")

    def test_generated_apps_server_is_disabled_in_ephemeral_threads(self):
        overrides = probe.ephemeral_overrides(self.configuration())
        self.assertIs(overrides["features.apps"], False)

    def test_test_only_setup_tools_added_without_removing_other_restrictions(self):
        overrides = probe.ephemeral_overrides(self.configuration())
        browser = overrides["mcp_servers"][probe.SERVER]
        self.assertTrue(set(probe.TEST_ONLY_TOOLS) <= set(browser["enabled_tools"]))
        self.assertEqual(browser["disabled_tools"], ["browser_network_request"])

    def test_missing_project_allowlist_cannot_be_silently_replaced(self):
        config = self.configuration()
        del config["mcp_servers"][probe.SERVER]["enabled_tools"]
        with self.assertRaisesRegex(probe.ProbeError, "allowlist is missing"):
            probe.ephemeral_overrides(config)

    def test_remote_endpoint_cannot_replace_native_stdio_boundary(self):
        config = self.configuration()
        config["mcp_servers"][probe.SERVER]["url"] = "https://example.invalid/mcp"
        with self.assertRaisesRegex(probe.ProbeError, "stdio launcher"):
            probe.ephemeral_overrides(config)


class TransportTests(unittest.TestCase):
    def test_inventory_reuses_thread_transport_instead_of_schema_discovery(self):
        server = Mock()
        server.request.return_value = {
            "data": [
                {
                    "name": probe.SERVER,
                    "runtimeStatus": "connected",
                    "tools": {name: {"name": name} for name in probe.REQUIRED_TOOLS},
                }
            ],
            "nextCursor": None,
        }
        probe.inventory(server, "thread-one")
        server.request.assert_called_once_with(
            "mcpServerStatus/list",
            {
                "threadId": "thread-one",
                "serverName": probe.SERVER,
                "detail": "full",
                "limit": 100,
            },
        )

    def client(self, response):
        client = probe.AppServer.__new__(probe.AppServer)
        client.next_id = 0
        client.timeout = 0.01
        client.send = Mock()
        client.buffer = bytearray((json.dumps(response) + "\n").encode())
        return client

    def test_request_cannot_start_model_inference(self):
        client = self.client({"id": 1, "result": {}})
        with self.assertRaisesRegex(probe.ProbeError, "prohibited RPC"):
            client.request("turn/start", {})
        client.send.assert_not_called()

    def test_upstream_errors_never_expose_response_details(self):
        client = self.client(
            {"id": 1, "error": {"message": "ambient-secret-and-page-content"}}
        )
        with self.assertRaises(probe.ProbeError) as caught:
            client.request("thread/start", {})
        self.assertNotIn("ambient-secret", str(caught.exception))
        self.assertIn("thread/start", str(caught.exception))

    def test_json_result_comes_from_result_section_not_echoed_javascript(self):
        result = probe.result_json(
            {
                "content": [
                    {
                        "type": "text",
                        "text": '### Result\n{"local":"owned"}\n### Ran Playwright code\nconst unrelated={"local":"foreign"};',
                    }
                ]
            }
        )
        self.assertEqual(result, {"local": "owned"})

    def test_duplicate_or_missing_snapshot_references_fail(self):
        row = '- textbox "Probe text" [ref=e7]'
        self.assertEqual(
            probe.snapshot_ref(
                {"content": [{"type": "text", "text": row}]}, "textbox", "Probe text"
            ),
            "e7",
        )
        for text in ("", row + "\n" + row):
            with self.assertRaises(probe.ProbeError):
                probe.snapshot_ref(
                    {"content": [{"type": "text", "text": text}]},
                    "textbox",
                    "Probe text",
                )

    def test_screenshot_requires_png_image_transport_and_nonzero_dimensions(self):
        header = b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR"
        for dimensions in ((640, 480), (0, 480)):
            data = header + struct.pack(">II", *dimensions) + b"synthetic-header"
            response = {
                "content": [
                    {
                        "type": "image",
                        "mimeType": "image/png",
                        "data": base64.b64encode(data).decode(),
                    }
                ]
            }
            if all(dimensions):
                self.assertEqual(probe.png_response(response), data)
            else:
                with self.assertRaises(probe.ProbeError):
                    probe.png_response(response)
        with self.assertRaises(probe.ProbeError):
            probe.png_response(
                {"content": [{"type": "text", "text": "screenshot.png"}]}
            )


class IsolationAndOwnershipTests(unittest.TestCase):
    def test_extra_transport_cannot_be_assigned_as_one_thread_browser(self):
        sessions = {
            Path("session-" + "a" * 32): {"state": "running"},
            Path("session-" + "b" * 32): {"state": "running"},
        }
        with patch.object(probe, "owned_sessions", return_value=sessions):
            with self.assertRaisesRegex(probe.ProbeError, "unexpected number"):
                probe.wait_session(Path("/unused"), set(), set(), Mock(timeout=1))

    def record(self, pid, parent, born):
        return {"pid": pid, "ppid": parent, "birth_tick": born, "rss": 1, "state": "S"}

    def test_pid_reuse_cannot_establish_cleanup_ownership(self):
        records = {10: self.record(10, 1, 200), 11: self.record(11, 10, 300)}
        self.assertEqual(probe.descendants(records, (10, 100)), {})
        self.assertEqual(set(probe.descendants(records, (10, 200))), {10, 11})

    def test_process_name_parentheses_do_not_shift_birth_tick(self):
        values = ["S", "4"] + ["0"] * 17 + ["918", "4096", "5"]
        result = probe.parse_proc_stat(
            "9 (name with ) parentheses) " + " ".join(values)
        )
        self.assertEqual(probe.identity(result), (9, 918))
        self.assertEqual(result["ppid"], 4)

    def state(self):
        return {
            "cookie": "owned",
            "local": "owned",
            "marker": "owned",
            "title": "owned",
            "httpOnly": True,
            "scriptCookieVisible": False,
            "titles": ["owned"],
            "url": probe.URL,
        }

    def test_storage_cookie_script_visibility_and_page_leaks_are_rejected(self):
        probe.assert_state(self.state(), "owned")
        for field, value in (
            ("cookie", "foreign"),
            ("local", "foreign"),
            ("scriptCookieVisible", True),
            ("title", "foreign"),
            ("titles", ["owned", "foreign"]),
            ("url", "http://localhost:8001/account"),
        ):
            state = self.state()
            state[field] = value
            with self.subTest(field=field), self.assertRaises(probe.ProbeError):
                probe.assert_state(state, "owned")

    def test_anonymous_login_destination_keeps_exact_tab_routing(self):
        destination = probe.LOGIN_URL
        state = self.state()
        state["url"] = destination
        probe.assert_state(state, "owned", url=destination)
        with self.assertRaises(probe.ProbeError):
            probe.assert_state(state, "owned")
        state.update(
            url=destination + "#probe-tab",
            title="owned-tab",
            marker="owned-tab",
            titles=["owned", "owned-tab"],
        )
        probe.assert_state(state, "owned", tab=True, url=destination)
        state["url"] = probe.URL + "#probe-tab"
        with self.assertRaises(probe.ProbeError):
            probe.assert_state(state, "owned", tab=True, url=destination)

    def test_cleanup_attempts_every_thread_even_when_one_browser_close_fails(self):
        server = Mock(timeout=1, known_processes=set())
        server.capture_owned.return_value = {}
        server.request.return_value = {"status": "unsubscribed"}
        server.process.poll.return_value = 0
        clients = [{"thread": "one"}, {"thread": "two"}]
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(probe, "process_snapshot", return_value={}),
            patch.object(
                probe,
                "call",
                side_effect=[probe.ProbeError("Synthetic close failure"), {}],
            ) as call,
        ):
            result = probe.cleanup(server, clients, set(), Path(directory), set())
        self.assertEqual(call.call_count, 2)
        self.assertEqual(server.request.call_count, 2)
        server.close.assert_called_once()
        self.assertEqual(result["browser_close_ok"], 1)
        self.assertEqual(result["unsubscribed"], 2)
        self.assertTrue(result["session_directories_removed"])


if __name__ == "__main__":
    unittest.main()
