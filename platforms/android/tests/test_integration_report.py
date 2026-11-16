"""Raw finite/network evidence refusals through the production checker."""

import copy
import importlib.util
from pathlib import Path
import unittest

ANDROID = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "integration_report", ANDROID / "runtime/check_integration_report.py"
)
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


def positive_case():
    def identity(pid, parent):
        return dict(
            pid=pid,
            ppid=parent,
            pgid=20,
            sid=20,
            kernel_uid=10001,
            start_ticks=100 + pid,
            boot_id="boot",
        )

    c = dict(
        name="fast-orphan",
        candidate_complete=True,
        control_pass=True,
        cleanup_errno=0,
        owner=identity(20, 10),
        expected=[{"role": i, "identity": identity(30 + i, 25)} for i in [0, 1, 5]],
        discovered=[identity(30 + i, 20) for i in [0, 1]],
        acknowledged_signals=[15, 15, 0, 0, 0, 0],
        error=0,
        candidate_errno=0,
        prctl_errno=0,
        scan_errno=0,
        scan_unknown=0,
        candidate_exit_code=0,
        candidate_exit_signal=0,
        subreaper_before=0,
        subreaper_after=1,
        sent=2,
        reaped=2,
        owner_verified_outside=True,
        owner_verified_inside=True,
        intermediates_reaped_before_first_scan=True,
        waitability_verified=True,
        scan_complete=True,
        candidate_waited=True,
        no_children=True,
        peer_survived=True,
        oracle_intervention=False,
        owner_lost=False,
    )
    return dict(
        source_id=checker.SUBREAPER_HEAD,
        probe="subreaper-orphan-control",
        compiled_platform="android",
        compiled_api=29,
        compiled_arch="x86_64",
        runtime_acceptance=False,
        production_ownership=False,
        owner_death_solution=False,
        kernel_uid_known=True,
        kernel_uid=10001,
        oracle_subreaper=True,
        harness_errno=0,
        cases=[c],
        controls_pass=True,
        selected_candidate_cases_complete=True,
    )


class IntegrationReportControls(unittest.TestCase):
    def validate_case(self, raw, exit_code=0):
        return checker.finite_case(
            raw, "fast-orphan", exit_code, "x86_64", 10001, "boot"
        )

    def test_finite_completeness_never_comes_from_summary_or_oracle_alone(self):
        original = positive_case()
        self.assertTrue(self.validate_case(original)["candidate_complete"])
        mutations = {
            "wrong-source": lambda r: r.update(source_id="0" * 40),
            "wrong-abi": lambda r: r.update(compiled_arch="aarch64"),
            "wrong-native-api": lambda r: r.update(compiled_api=36),
            "unknown-uid": lambda r: r.update(kernel_uid_known=False),
            "cleanup-error": lambda r: r["cases"][0].update(cleanup_errno=110),
            "no-wait": lambda r: r["cases"][0].update(candidate_waited=False),
            "owner-exit": lambda r: r["cases"][0].update(candidate_exit_code=1),
            "no-reaping": lambda r: r["cases"][0].update(reaped=0),
            "no-child-waitability": lambda r: r["cases"][0].update(
                waitability_verified=False
            ),
            "unknown-scan": lambda r: r["cases"][0].update(scan_unknown=1),
            "peer-lost": lambda r: r["cases"][0].update(peer_survived=False),
            "oracle-substitution": lambda r: r["cases"][0].update(
                oracle_intervention=True
            ),
            "wrong-birth": lambda r: r["cases"][0]["discovered"][0].update(
                start_ticks=999
            ),
            "wrong-parent": lambda r: r["cases"][0]["discovered"][0].update(ppid=10),
            "wrong-ack": lambda r: r["cases"][0].update(acknowledged_signals=[0] * 6),
            "overclaimed-production": lambda r: r.update(production_ownership=True),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                changed = copy.deepcopy(original)
                mutate(changed)
                with self.assertRaises((ValueError, KeyError, TypeError)):
                    self.validate_case(changed)
        with self.assertRaises(ValueError):
            self.validate_case(original, 1)

    def test_safe_failed_control_is_measured_not_promoted(self):
        raw = positive_case()
        raw.update(controls_pass=False, selected_candidate_cases_complete=False)
        raw["cases"][0].update(
            candidate_complete=False,
            control_pass=False,
            error=13,
            candidate_errno=13,
            reaped=0,
            sent=0,
            oracle_intervention=True,
        )
        result = self.validate_case(raw, 1)
        self.assertFalse(result["candidate_complete"])
        self.assertFalse(result["control_pass"])
        self.assertTrue(result["finite_oracle_cleanup"])

    def test_known_post_cleanup_births_must_be_absent_or_reused_not_unknown(self):
        raw = positive_case()
        c = raw["cases"][0]
        c["changed"] = []
        c["orphan_after_owner_loss"] = dict(pid=0, start_ticks=0)
        ids = [w["identity"] for w in c["expected"]] + c["discovered"] + [c["owner"]]
        births = {(p["pid"], p["start_ticks"], p["boot_id"]) for p in ids}
        observations = [
            dict(pid=pid, start_ticks=ticks, boot_id=boot, observation="absent")
            for pid, ticks, boot in sorted(births)
        ]
        checker.released_births(raw, observations, 10001, "boot")
        for wrong in [
            observations[:-1],
            observations + [observations[0]],
            [{**o, "observation": "live"} for o in observations],
            [
                {
                    **o,
                    "observation": "different-birth",
                    "current_start_ticks": o["start_ticks"],
                }
                for o in observations
            ],
        ]:
            with self.subTest(wrong=wrong), self.assertRaises(ValueError):
                checker.released_births(raw, wrong, 10001, "boot")

    def test_network_readiness_requires_callback_binding_and_public_tls_witnesses(self):
        raw = dict(
            ready=True,
            stack_acceptance=False,
            native_bridge_sha256="bridge",
            probe="android-network-v1",
            schema=1,
            sdk_interfaces_ok=True,
            socket_cookie_required=True,
            dns_ok=True,
            udp_socket_bound=True,
            tcp_socket_bound=True,
            tls_ok=True,
            generation=1,
            network_handle=100,
            public_root_count=100,
            android_dns_queries=2,
            android_socket_binds=2,
            peer_certificate_expires_at=1234,
            interfaces=["192.0.2.1/24"],
            public_roots_sha256="a" * 64,
        )
        call = dict(
            source_head=checker.NETWORK_HEAD,
            node_started=False,
            enrollment_attempted=False,
            retained_ownership=False,
            api_returned_after_close=True,
            control_pass=True,
            bridge_identity_verified=True,
            report=raw,
        )
        self.assertTrue(checker.network_case(call, {"bridge_sha256": "bridge"}))
        for field in [
            "socket_cookie_required",
            "dns_ok",
            "udp_socket_bound",
            "tcp_socket_bound",
            "tls_ok",
        ]:
            with self.subTest(field=field):
                changed = copy.deepcopy(call)
                changed["report"][field] = False
                with self.assertRaises(ValueError):
                    checker.network_case(changed, {"bridge_sha256": "bridge"})
        changed = copy.deepcopy(call)
        changed.update(retained_ownership=True, failed=True, control_pass=False)
        self.assertFalse(checker.network_case(changed, {"bridge_sha256": "bridge"}))


if __name__ == "__main__":
    unittest.main()
