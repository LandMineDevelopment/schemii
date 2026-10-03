import base64
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch


spec = importlib.util.spec_from_file_location(
    "browser_probe", Path(__file__).with_name("verify_browser_isolation.py")
)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)

cleanup_spec = importlib.util.spec_from_file_location(
    "browser_cleanup_probe", Path(__file__).with_name("verify_browser_cleanup.py")
)
cleanup_probe = importlib.util.module_from_spec(cleanup_spec)
with patch.dict(sys.modules, {"verify_browser_isolation": probe}):
    cleanup_spec.loader.exec_module(cleanup_probe)


class CleanupSignalOwnershipTests(unittest.TestCase):
    def record(self, pid=50, birth=100, state="S"):
        return {"pid": pid, "birth_tick": birth, "state": state}

    def test_reused_or_unknown_pid_never_receives_a_signal(self):
        for records in ({}, {50: self.record(birth=101)}):
            with (
                patch.object(probe, "process_snapshot", return_value=records),
                patch.object(cleanup_probe.os, "kill") as kill,
                patch.object(cleanup_probe.os, "killpg") as killpg,
            ):
                with self.assertRaisesRegex(probe.ProbeError, "identity changed"):
                    cleanup_probe.signal_owned(50, 100, 9)
                kill.assert_not_called()
                killpg.assert_not_called()

    def test_group_signal_requires_the_captured_group_leader(self):
        with (
            patch.object(probe, "process_snapshot", return_value={50: self.record()}),
            patch.object(cleanup_probe.os, "getpgid", return_value=49),
            patch.object(cleanup_probe.os, "killpg") as killpg,
        ):
            with self.assertRaisesRegex(probe.ProbeError, "group is not owned"):
                cleanup_probe.signal_owned(50, 100, 9, group=True)
            killpg.assert_not_called()

    def test_live_cleanup_count_ignores_exited_zombies_and_unowned_peers(self):
        records = {
            50: self.record(),
            51: self.record(pid=51, state="Z"),
            52: self.record(pid=52),
        }
        with patch.object(probe, "process_snapshot", return_value=records):
            self.assertEqual(
                cleanup_probe.live_identities({(50, 100), (51, 100)}), {(50, 100)}
            )


class CleanupInitializationTests(unittest.TestCase):
    def test_failed_or_interrupted_initialization_closes_transport_before_reraising(
        self,
    ):
        for error in (
            probe.ProbeError("Synthetic initialize failure"),
            KeyboardInterrupt(),
        ):
            server = Mock()
            server.request.side_effect = error
            with (
                tempfile.TemporaryDirectory() as directory,
                patch.object(probe, "AppServer", return_value=server),
                patch.object(probe, "cleanup") as cleanup,
            ):
                root = Path(directory)
                with self.assertRaises(type(error)):
                    cleanup_probe.Connection(root, 1)
                cleanup.assert_called_once_with(
                    server, [], set(), root / "artifacts" / "native-browsers", set()
                )


class GenerationOwnershipTests(unittest.TestCase):
    def record(self, pid, parent, *, birth=None, state="S", rss=1048576):
        return {
            "pid": pid,
            "ppid": parent,
            "birth_tick": birth or pid * 10,
            "state": state,
            "rss": rss,
        }

    def client(self, path):
        return {
            "thread": "same-thread",
            "session": path,
            "metadata": {
                "pid": 10,
                "birth_tick": 100,
                "child_pid": 20,
                "child_birth_tick": 200,
                "guardian_pid": 30,
                "guardian_birth_tick": 300,
            },
        }

    def records(self):
        return {
            10: self.record(10, 1),
            20: self.record(20, 10),
            21: self.record(21, 20),
            30: self.record(30, 10),
            40: self.record(40, 1),  # A live peer must not enter this generation.
        }

    def generation(self, path):
        return {
            "endpoint": (10, 100),
            "released": {(20, 200), (21, 210), (30, 300)},
            "leaders": {(20, 200), (30, 300)},
            "session": path,
            "roles": {"backend_processes": 2, "backend_rss_bytes": 2097152},
        }

    def test_generation_roles_capture_backend_and_guardian_without_endpoint_or_peer(
        self,
    ):
        server = Mock()
        server.capture_owned.return_value = self.records()
        with patch.object(probe.os, "getpgid", return_value=20):
            result = probe.generation_snapshot(server, self.client(Path("/unused")))
        self.assertEqual(result["released"], {(20, 200), (21, 210), (30, 300)})
        self.assertEqual(result["roles"]["backend_rss_bytes"], 2097152)
        self.assertEqual(result["roles"]["guardian_processes"], 1)
        self.assertEqual(result["roles"]["endpoint_processes"], 1)

    def test_missing_reused_exited_or_unowned_generation_process_fails_closed(self):
        for pid, replacement in (
            (20, None),
            (30, None),
            (20, self.record(20, 10, birth=201)),
            (30, self.record(30, 40)),
            (20, self.record(20, 10, state="Z")),
        ):
            records = self.records()
            if replacement is None:
                records.pop(pid)
            else:
                records[pid] = replacement
            server = Mock()
            server.capture_owned.return_value = records
            with (
                self.subTest(pid=pid, replacement=replacement),
                self.assertRaises(probe.ProbeError),
            ):
                probe.generation_snapshot(server, self.client(Path("/unused")))

    def test_backend_group_must_be_its_captured_leader(self):
        server = Mock()
        server.capture_owned.return_value = self.records()
        with patch.object(probe.os, "getpgid", return_value=19):
            with self.assertRaisesRegex(probe.ProbeError, "group is not owned"):
                probe.generation_snapshot(server, self.client(Path("/unused")))

    def test_release_requires_absent_generation_and_retains_only_same_endpoint(self):
        server = Mock(timeout=1)
        records = {10: self.record(10, 1), 40: self.record(40, 1)}
        server.capture_owned.return_value = records
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(probe, "process_snapshot", return_value=records),
        ):
            result = probe.assert_generation_released(
                server, self.generation(Path(directory) / "already-released")
            )
        self.assertTrue(result["session_and_output_removed"])
        self.assertEqual(result["remaining_generation_process_entries"], 0)
        self.assertEqual(result["after"]["backend_rss_bytes"], 0)
        self.assertEqual(result["after"]["endpoint_rss_bytes"], 1048576)
        self.assertFalse(result["native_transport_shutdown"])

    def test_live_or_zombie_generation_entries_cannot_count_as_released(self):
        for state in ("S", "Z"):
            server = Mock(timeout=1)
            server.capture_owned.return_value = {10: self.record(10, 1)}
            with (
                self.subTest(state=state),
                patch.object(
                    probe,
                    "process_snapshot",
                    return_value={20: self.record(20, 1, state=state)},
                ),
                patch.object(probe.time, "monotonic", side_effect=[0, 2]),
            ):
                with self.assertRaisesRegex(probe.ProbeError, "retained generation"):
                    probe.assert_generation_released(
                        server, self.generation(Path("/unused"))
                    )

    def test_missing_or_reused_endpoint_cannot_count_as_close_release(self):
        for records in ({}, {10: self.record(10, 1, birth=101)}):
            server = Mock(timeout=1)
            server.capture_owned.return_value = records
            with self.assertRaisesRegex(probe.ProbeError, "ended or replaced"):
                probe.assert_generation_released(
                    server, self.generation(Path("/unused"))
                )

    def test_retained_session_or_dangling_symlink_cannot_count_as_released(self):
        server = Mock(timeout=1)
        records = {10: self.record(10, 1)}
        server.capture_owned.return_value = records
        with tempfile.TemporaryDirectory() as directory:
            for symlink in (False, True):
                path = Path(directory) / str(symlink)
                if symlink:
                    path.symlink_to(Path(directory) / "missing")
                else:
                    path.mkdir()
                with (
                    self.subTest(symlink=symlink),
                    patch.object(probe, "process_snapshot", return_value=records),
                    patch.object(probe.time, "monotonic", side_effect=[0, 2]),
                ):
                    with self.assertRaisesRegex(
                        probe.ProbeError, "retained generation"
                    ):
                        probe.assert_generation_released(server, self.generation(path))

    def test_unexpected_new_backend_under_dormant_endpoint_is_rejected(self):
        server = Mock(timeout=1)
        records = {10: self.record(10, 1), 22: self.record(22, 10)}
        server.capture_owned.return_value = records
        with patch.object(probe, "process_snapshot", return_value=records):
            with self.assertRaisesRegex(probe.ProbeError, "recreated backend"):
                probe.assert_generation_released(
                    server, self.generation(Path("/unused"))
                )

    def test_reopen_tracks_new_session_and_retains_all_generation_paths(self):
        client = self.client(Path("/unused-old-generation"))
        metadata = {**client["metadata"], "child_pid": 22, "guardian_pid": 32}
        sessions = {client["session"]}
        with patch.object(
            probe,
            "wait_session",
            return_value=(Path("/unused-new-generation"), metadata),
        ):
            probe.track_generation(Mock(), client, Path("/root"), set(), sessions)
        self.assertEqual(client["thread"], "same-thread")
        self.assertEqual(client["session"], Path("/unused-new-generation"))
        self.assertEqual(
            sessions, {Path("/unused-old-generation"), Path("/unused-new-generation")}
        )

    def test_reopen_rejects_endpoint_or_generation_identity_reuse(self):
        for change in ({"birth_tick": 101}, {"child_pid": 20}, {"guardian_pid": 30}):
            client = self.client(Path("/unused-old-generation"))
            metadata = {
                **client["metadata"],
                "child_pid": 22,
                "guardian_pid": 32,
                **change,
            }
            with (
                self.subTest(change=change),
                patch.object(
                    probe,
                    "wait_session",
                    return_value=(Path("/unused-new-generation"), metadata),
                ),
            ):
                with self.assertRaises(probe.ProbeError):
                    probe.track_generation(Mock(), client, Path("/root"), set(), set())

    def test_reopen_rejects_a_retained_previous_session(self):
        with tempfile.TemporaryDirectory() as directory:
            client = self.client(Path(directory))
            metadata = {**client["metadata"], "child_pid": 22, "guardian_pid": 32}
            with patch.object(
                probe, "wait_session", return_value=(Path(directory) / "new", metadata)
            ):
                with self.assertRaisesRegex(
                    probe.ProbeError, "previous generation directory"
                ):
                    probe.track_generation(
                        Mock(), client, Path(directory), set(), set()
                    )

    def test_final_cleanup_does_not_hide_owned_zombie_entries(self):
        server = Mock(timeout=1, known_processes={(20, 200)})
        server.process.poll.return_value = 0
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(probe, "owned_sessions", return_value={}),
            patch.object(
                probe,
                "process_snapshot",
                return_value={20: self.record(20, 1, state="Z")},
            ),
            patch.object(probe.time, "monotonic", side_effect=[0, 16]),
        ):
            result = probe.cleanup(server, [], set(), Path(directory), set())
        self.assertEqual(result["live_owned_processes"], 0)
        self.assertEqual(result["remaining_owned_process_entries"], 1)


class CloseFollowupControls(unittest.TestCase):
    def state(self, **changes):
        return {
            "cookies": 0,
            "marker": None,
            "pages": 1,
            "title": "Native cleanup probe",
            **changes,
        }

    def response(self, state):
        return {"content": [{"type": "text", "text": json.dumps(state)}]}

    def test_used_and_never_used_close_reopen_track_generation_after_navigation(self):
        for used in (False, True):
            connection = Mock()
            connection.call.return_value = self.response(self.state())
            client = {"thread": "same-thread"}
            events = []
            connection.call.side_effect = lambda _client, tool, *args: (
                events.append(tool) or self.response(self.state())
            )
            connection.track.side_effect = lambda _client: events.append("track")
            with (
                self.subTest(used=used),
                patch.object(probe, "generation_snapshot", return_value={"roles": {}}),
                patch.object(
                    probe, "assert_generation_released", return_value={}
                ) as release,
                patch.object(probe, "inventory"),
            ):
                result = cleanup_probe.check_close_reopen(connection, client, used=used)
            self.assertEqual(release.call_count, 2)
            self.assertEqual(events.count("browser_close"), 2)
            self.assertLess(events.index("browser_navigate"), events.index("track"))
            self.assertTrue(result["fresh_context"])
            self.assertFalse(result["native_transport_shutdown"])

    def test_reopened_context_cookie_dom_or_tab_leaks_fail(self):
        for changes in (
            {"cookies": 1},
            {"marker": "owned"},
            {"pages": 2},
            {"title": "foreign"},
        ):
            connection = Mock()
            connection.call.return_value = self.response(self.state(**changes))
            with (
                self.subTest(changes=changes),
                patch.object(probe, "generation_snapshot", return_value={"roles": {}}),
                patch.object(probe, "assert_generation_released", return_value={}),
                patch.object(probe, "inventory"),
            ):
                with self.assertRaisesRegex(probe.ProbeError, "prior context state"):
                    cleanup_probe.check_close_reopen(
                        connection, {"thread": "same-thread"}, used=False
                    )

    def test_failed_call_is_sent_once_and_active_generation_is_retained(self):
        generation = {"endpoint": (1, 2), "leaders": {(3, 4)}, "session": Mock()}
        generation["session"].exists.return_value = True
        connection = Mock()
        connection.call.side_effect = probe.ProbeError(
            "Browser primitive failed: browser_evaluate"
        )
        with patch.object(probe, "generation_snapshot", return_value=generation):
            result = cleanup_probe.check_failed_call(connection, {})
        self.assertTrue(result["explicit_failure_preserved"])
        connection.call.assert_called_once()

    def test_failed_call_cannot_be_swallowed_or_replace_generation(self):
        generation = {"endpoint": (1, 2), "leaders": {(3, 4)}, "session": Mock()}
        for succeeds in (False, True):
            connection = Mock()
            if not succeeds:
                connection.call.side_effect = probe.ProbeError(
                    "Browser primitive failed: browser_evaluate"
                )
            with (
                self.subTest(succeeds=succeeds),
                patch.object(
                    probe,
                    "generation_snapshot",
                    side_effect=[generation, {**generation, "leaders": {(3, 5)}}],
                ),
            ):
                with self.assertRaises(probe.ProbeError):
                    cleanup_probe.check_failed_call(connection, {})
            connection.call.assert_called_once()


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
