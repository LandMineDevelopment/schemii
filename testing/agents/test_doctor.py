import importlib.util
from pathlib import Path
import unittest


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


if __name__ == "__main__":
    unittest.main()
