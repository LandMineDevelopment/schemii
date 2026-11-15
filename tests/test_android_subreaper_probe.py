"""Dedicated real subreaper ownership controls, separate from immutable6242."""

from __future__ import annotations

import errno
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "platforms/android/native/process"
CASES = (
    "fast-orphan",
    "nested-orphan",
    "new-session",
    "new-group",
    "sigint",
    "deny-prctl",
    "unsupported-prctl",
    "deny-proc",
    "unsupported-proc",
    "deny-pidfd",
    "unsupported-pidfd",
    "timeout",
    "owner-loss",
    "stale-owner",
    "stale-group",
    "stale-session",
)
POSITIVE = {"fast-orphan", "nested-orphan", "new-session", "new-group", "sigint"}


class SubreaperProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which("cc")
        if compiler is None:
            raise RuntimeError("C11 compiler required for real subreaper controls")
        evidence = ROOT / "artifacts/android-apk/host/subreaper"
        evidence.mkdir(parents=True, exist_ok=True)
        cls.temporary = tempfile.TemporaryDirectory(prefix="tests-", dir=evidence)
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.binary = Path(cls.temporary.name) / "subreaper-probe"
        cls.source_files = [
            SOURCE / "subreaper_probe.c",
            SOURCE / "proc_observer.c",
            SOURCE / "proc_observer.h",
        ]
        cls.source_id = hashlib.sha256(
            b"".join(p.name.encode() + b"\0" + p.read_bytes() for p in cls.source_files)
        ).hexdigest()
        subprocess.run(
            [
                compiler,
                "-std=c11",
                "-O2",
                "-g",
                "-Wall",
                "-Wextra",
                "-Werror",
                "-pthread",
                f'-DSUBREAPER_PROBE_SOURCE_ID="{cls.source_id}"',
                str(SOURCE / "proc_observer.c"),
                str(SOURCE / "subreaper_probe.c"),
                "-o",
                str(cls.binary),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )

    def invoke(self, case=None):
        argv = [str(self.binary), "subreaper-orphan-control"]
        if case is not None:
            argv += ["--case", case]
        run = subprocess.run(argv, capture_output=True, text=True, timeout=35)
        self.assertEqual(run.stderr, "")
        self.assertLess(len(run.stdout.encode()), 65536)
        report = json.loads(run.stdout)
        self.assertEqual(report["source_id"], self.source_id)
        self.assertTrue(report["oracle_subreaper"])
        self.assertEqual(report["harness_errno"], 0)
        self.assertTrue(report["kernel_uid_known"])
        self.assertEqual(report["kernel_uid"], os.getuid())
        self.assertEqual(report["reported_uid"], os.getuid())
        self.assertFalse(report["owner_death_solution"])
        self.assertFalse(report["production_ownership"])
        self.assertFalse(report["runtime_acceptance"])
        self.assertEqual(report["android_acceptance"], "pending")
        return run.returncode, report

    @staticmethod
    def birth(identity):
        return (
            identity["pid"],
            identity["start_ticks"],
            identity["boot_id"],
            identity["kernel_uid"],
        )

    def check_case(self, case):
        code, report = self.invoke(case)
        self.assertEqual(code, 0 if case in POSITIVE else 1, report)
        self.assertTrue(report["controls_pass"], report)
        self.assertEqual(len(report["cases"]), 1)
        item = report["cases"][0]
        self.assertEqual(item["name"], case)
        self.assertTrue(item["control_pass"], item)
        self.assertEqual(item["candidate_complete"], case in POSITIVE)
        self.assertEqual(report["selected_candidate_cases_complete"], case in POSITIVE)
        self.assertTrue(item["candidate_waited"])
        self.assertTrue(item["peer_survived"])
        self.assertEqual(item["cleanup_errno"], 0)
        self.assertEqual(item["subreaper_before"], 0)
        expected = {w["role"]: w["identity"] for w in item["expected"]}
        discovered = {self.birth(i) for i in item["discovered"]}
        self.assertIn(5, expected)
        self.assertNotIn(self.birth(expected[5]), discovered)
        if case in {"deny-prctl", "unsupported-prctl", "stale-owner"}:
            self.assertFalse(item["launched"])
            self.assertEqual(discovered, set())
            self.assertFalse(item["oracle_intervention"])
        else:
            self.assertTrue(item["owner_verified_outside"])
            self.assertTrue(item["owner_verified_inside"])
            self.assertEqual(item["subreaper_after"], 1)
            self.assertTrue(item["intermediates_reaped_before_first_scan"])
            count = 3 if case == "nested-orphan" else 2
            self.assertTrue(all(role in expected for role in range(count)))
            if case not in {"deny-proc", "unsupported-proc"}:
                self.assertTrue(item["scan_complete"])
                self.assertTrue(item["waitability_verified"])
                self.assertEqual(
                    discovered, {self.birth(expected[i]) for i in range(count)}
                )
                for node in item["discovered"]:
                    # Kernel reparenting, not a UID/group inference or the oracle.
                    self.assertEqual(node["ppid"], item["owner"]["pid"])
                    before = next(
                        n for n in expected.values() if n["pid"] == node["pid"]
                    )
                    self.assertNotEqual(before["ppid"], item["owner"]["pid"])
            self.assertEqual(item["oracle_intervention"], case not in POSITIVE)
        if case in POSITIVE:
            count = 3 if case == "nested-orphan" else 2
            self.assertEqual(item["candidate_errno"], 0)
            self.assertEqual(item["sent"], count)
            self.assertEqual(item["reaped"], count)
            self.assertTrue(item["no_children"])
            sig = signal.SIGINT if case == "sigint" else signal.SIGTERM
            self.assertEqual(item["acknowledged_signals"][:count], [sig] * count)
            self.assertEqual(item["candidate_exit_code"], 0)
            if case == "new-session":
                self.assertNotEqual(expected[0]["sid"], item["owner"]["sid"])
            if case == "new-group":
                self.assertNotEqual(expected[0]["pgid"], item["owner"]["pgid"])
            if case == "nested-orphan":
                self.assertIn(3, expected)
                self.assertEqual(expected[4]["ppid"], expected[3]["pid"])
        elif case == "owner-loss":
            self.assertTrue(item["owner_lost"])
            self.assertTrue(item["orphan_survived_owner_loss"])
            self.assertEqual(item["candidate_exit_signal"], signal.SIGTERM)
            self.assertEqual(item["candidate_exit_code"], -1)
            self.assertNotEqual(
                item["orphan_after_owner_loss"]["ppid"], item["owner"]["pid"]
            )
        else:
            self.assertEqual(item["candidate_exit_code"], 1)
            error = {
                "deny-prctl": errno.EPERM,
                "unsupported-prctl": errno.ENOSYS,
                "deny-proc": errno.EACCES,
                "unsupported-proc": errno.ENOSYS,
                "deny-pidfd": errno.EPERM,
                "unsupported-pidfd": errno.ENOSYS,
                "timeout": errno.ETIMEDOUT,
                "stale-owner": errno.ESTALE,
                "stale-group": errno.ESTALE,
                "stale-session": errno.ESTALE,
            }[case]
            self.assertEqual(item["candidate_errno"], error)
            if case in {"stale-group", "stale-session"}:
                changed = next(w["identity"] for w in item["changed"] if w["role"] == 0)
                field = "pgid" if case == "stale-group" else "sid"
                self.assertNotEqual(changed[field], expected[0][field])
                self.assertEqual(changed["ppid"], item["owner"]["pid"])

            if case.startswith(("deny-", "unsupported-")):
                self.assertTrue(item["fault_installed"])
                self.assertEqual(item["fault_install_errno"], 0)
            if case == "timeout":
                self.assertEqual(item["sent"], 1)
                self.assertTrue(item["live_after_refusal"])
            else:
                self.assertEqual(item["sent"], 0)
            if case in {
                "deny-pidfd",
                "unsupported-pidfd",
                "stale-group",
                "stale-session",
            }:
                self.assertTrue(item["live_after_refusal"])

    def test_full_report_never_calls_negative_cases_complete(self):
        code, report = self.invoke()
        self.assertEqual(code, 1)
        self.assertTrue(report["controls_pass"], report)
        self.assertFalse(report["selected_candidate_cases_complete"])
        self.assertEqual([r["name"] for r in report["cases"]], list(CASES))
        self.assertEqual(
            {r["name"] for r in report["cases"] if r["candidate_complete"]}, POSITIVE
        )
        self.assertTrue(
            all(r["peer_survived"] and r["cleanup_errno"] == 0 for r in report["cases"])
        )

    def test_invalid_argv_never_launches_a_fixture(self):
        for argv in [
            [],
            ["proc-discovery-control"],
            ["subreaper-orphan-control", "--pid", "1"],
            ["subreaper-orphan-control", "--case", "unknown"],
        ]:
            run = subprocess.run(
                [str(self.binary), *argv], capture_output=True, timeout=3
            )
            self.assertEqual(run.returncode, 2)
            self.assertEqual(run.stdout, b"")


def _case_test(case):
    def run(self):
        self.check_case(case)

    return run


for _case in CASES:
    setattr(SubreaperProbeTests, "test_" + _case.replace("-", "_"), _case_test(_case))
