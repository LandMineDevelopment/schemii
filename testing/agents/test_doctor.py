import copy
import importlib.util
from pathlib import Path
import unittest
import tomllib


spec = importlib.util.spec_from_file_location(
    "agent_doctor", Path(__file__).with_name("doctor.py")
)
doctor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(doctor)


class CapacityReportTests(unittest.TestCase):
    def report(self, config=None, slots=None, hooks=None):
        return doctor.summarize({"config": config or {}, "hooks": hooks or []}, slots)

    def test_configured_limit_does_not_claim_available_workers(self):
        result = self.report({"agents": {"max_concurrent_threads_per_session": 12}})
        self.assertEqual(result["configured_child_limit"], 12)
        self.assertIsNone(result["maximum_workers_with_reviewer_reserved"])
        self.assertEqual(result["status"], "runtime_capacity_unverified")

    def test_current_four_slots_reserve_coordinator_and_reviewer(self):
        result = self.report(
            {"agents": {"max_concurrent_threads_per_session": 12}}, slots=4
        )
        self.assertEqual(result["maximum_workers_with_reviewer_reserved"], 2)

    def test_configured_child_limit_also_bounds_larger_host(self):
        result = self.report(
            {"agents": {"max_concurrent_threads_per_session": 3}}, slots=20
        )
        self.assertEqual(result["maximum_workers_with_reviewer_reserved"], 2)

    def test_enabled_v2_does_not_report_v1_capacity_as_effective(self):
        result = self.report(
            {
                "agents": {"max_concurrent_threads_per_session": 12},
                "features": {"multi_agent_v2": True},
            }
        )
        self.assertEqual(result["engine"], "v2")
        self.assertIsNone(result["configured_child_limit"])

    def test_v2_table_uses_its_own_limit(self):
        result = self.report(
            {
                "agents": {"max_concurrent_threads_per_session": 12},
                "features": {
                    "multi_agent_v2": {
                        "enabled": True,
                        "max_concurrent_threads_per_session": 6,
                    }
                },
            },
            slots=13,
        )
        self.assertEqual(result["configured_child_limit"], 6)
        self.assertEqual(result["maximum_workers_with_reviewer_reserved"], 5)

    def test_untrusted_modified_or_disabled_hook_is_not_active(self):
        for trust, enabled in [
            ("untrusted", True),
            ("modified", True),
            ("trusted", False),
        ]:
            hooks = [
                {
                    "hooks": [
                        {
                            "eventName": "PreToolUse",
                            "enabled": enabled,
                            "trustStatus": trust,
                            "command": "python3 testing/agents/guard.py",
                        }
                    ]
                }
            ]
            self.assertFalse(self.report(hooks=hooks)["spawn_guard_trusted"])

    def test_session_context_hook_does_not_prove_spawn_guard(self):
        hooks = [
            {
                "hooks": [
                    {
                        "eventName": "SessionStart",
                        "enabled": True,
                        "trustStatus": "trusted",
                        "command": "python3 testing/agents/guard.py",
                    }
                ]
            }
        ]
        self.assertFalse(self.report(hooks=hooks)["spawn_guard_trusted"])

    def test_trusted_enabled_spawn_hook_is_reported(self):
        hooks = [
            {
                "hooks": [
                    {
                        "eventName": "preToolUse",
                        "enabled": True,
                        "trustStatus": "trusted",
                        "command": "python3 testing/agents/guard.py",
                    }
                ]
            }
        ]
        self.assertTrue(self.report(hooks=hooks)["spawn_guard_trusted"])

    def ready_report(self, config, matcher="spawn_agent|Agent"):
        return self.report(
            config,
            slots=13,
            hooks=[
                {
                    "hooks": [
                        {
                            "eventName": "preToolUse",
                            "enabled": True,
                            "trustStatus": "trusted",
                            "command": "python3 testing/agents/guard.py",
                            "matcher": matcher,
                        }
                    ]
                }
            ],
        )

    def test_bash_only_or_invalid_matcher_cannot_claim_dispatch_guard(self):
        config = {"agents": {"enabled": True, "max_concurrent_threads_per_session": 12}}
        for matcher in ("^Bash$", "^spawn_agent$", "["):
            report = self.ready_report(config, matcher)
            self.assertFalse(report["spawn_guard_trusted"])
            self.assertFalse(report["dispatch_ready"])

    def test_unknown_enable_or_v2_limit_cannot_claim_dispatch_readiness(self):
        for config in (
            {},
            {
                "features": {"multi_agent_v2": True},
                "agents": {"max_concurrent_threads_per_session": 12},
            },
        ):
            report = self.ready_report(config)
            self.assertFalse(report["dispatch_ready"])

    def test_disabled_hooks_cannot_claim_enforcement_despite_trust_metadata(self):
        config = {
            "agents": {"enabled": True, "max_concurrent_threads_per_session": 12},
            "features": {"hooks": False},
        }
        self.assertFalse(self.ready_report(config)["spawn_guard_trusted"])

    def test_known_capacity_and_covered_trusted_guard_allow_readiness(self):
        config = {"agents": {"enabled": True, "max_concurrent_threads_per_session": 12}}
        self.assertTrue(self.ready_report(config)["dispatch_ready"])


class NativeBrowserPolicyTests(unittest.TestCase):
    def setUp(self):
        self.config = tomllib.loads(
            (Path(__file__).resolve().parents[2] / ".codex/config.toml").read_text()
        )

    def test_project_policy_is_configured_without_claiming_runtime_proof(self):
        result = doctor.browser_policy(self.config)
        self.assertTrue(result["isolated_stdio_policy_valid"])
        self.assertEqual(result["runtime_browser_isolation"], "unverified")
        self.assertEqual(
            result["artifact_cleanup"],
            "successful_browser_close; terminal_browser_release; owned_connection_exit; orphan_sweep_on_start",
        )

    def test_missing_and_remote_shared_servers_do_not_claim_isolation(self):
        self.assertFalse(doctor.browser_policy({})["isolated_stdio_policy_valid"])
        self.config["mcp_servers"]["schemii_browser"]["url"] = (
            "http://localhost:9999/mcp"
        )
        self.assertFalse(
            doctor.browser_policy(self.config)["isolated_stdio_policy_valid"]
        )

    def test_terminal_release_must_be_allowed_without_claiming_runtime_activation(self):
        self.config["mcp_servers"]["schemii_browser"]["enabled_tools"].remove(
            "browser_release"
        )
        result = doctor.browser_policy(self.config)
        self.assertFalse(result["isolated_stdio_policy_valid"])
        self.assertEqual(result["artifact_cleanup"], "unverified")

    def test_arbitrary_javascript_or_no_allowlist_prevents_readiness(self):
        for tools in (
            None,
            ["browser_evaluate"],
            self.config["mcp_servers"]["schemii_browser"]["enabled_tools"]
            + ["browser_run_code_unsafe"],
        ):
            config = copy.deepcopy(self.config)
            config["mcp_servers"]["schemii_browser"]["enabled_tools"] = tools
            self.assertFalse(
                doctor.browser_policy(config)["isolated_stdio_policy_valid"]
            )

    def test_native_capacity_reserves_independent_review(self):
        result = doctor.summarize({"config": self.config, "hooks": []}, 13)
        self.assertEqual(result["native_browser_workers_with_reviewer_reserved"], 11)
        self.assertFalse(result["dispatch_ready"])

    def test_output_readiness_requires_all_filename_surfaces(self):
        hook = {
            "eventName": "PreToolUse",
            "enabled": True,
            "trustStatus": "trusted",
            "command": "python3 testing/agents/guard.py",
            "matcher": "schemii_browser[._]+browser_take_screenshot$",
        }
        hooks = [{"hooks": [hook]}]
        result = doctor.summarize({"config": self.config, "hooks": hooks}, 13)
        self.assertFalse(result["native_browser"]["output_guard_trusted"])
        hook["matcher"] = (
            "schemii_browser[._]+browser_(take_screenshot|snapshot|find|console_messages)$"
        )
        result = doctor.summarize({"config": self.config, "hooks": hooks}, 13)
        self.assertTrue(result["native_browser"]["output_guard_trusted"])


if __name__ == "__main__":
    unittest.main()
