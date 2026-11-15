"""Real disposable proc/pipe/pidfd controls; never Android/runtime acceptance."""

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
    "stable",
    "pthread",
    "witnessed-parent-loss",
    "leader-loss",
    "unwitnessed-orphan",
    "group-change",
    "session-change",
    "stale-birth",
    "stale-boot",
    "scan-limit",
    "scan-deadline",
    "scan-cancel",
    "unknown",
    "sigterm",
    "sigint",
    "cancel-timeout",
    "complete-helper-stop",
)
BLOCKED = {"unwitnessed-orphan", "cancel-timeout"}


class ProcessProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which("cc")
        if compiler is None:
            raise RuntimeError(
                "A C11 Linux compiler is required for real process controls"
            )
        evidence = ROOT / "artifacts/android-apk/host/process-probe"
        evidence.mkdir(parents=True, exist_ok=True)
        cls.temporary = tempfile.TemporaryDirectory(prefix="tests-", dir=evidence)
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.binary = Path(cls.temporary.name) / "proc-discovery-control"
        paths = sorted(SOURCE.glob("*.[ch]"))
        cls.source_id = hashlib.sha256(
            b"".join(p.name.encode() + b"\0" + p.read_bytes() for p in paths)
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
                f'-DPROCESS_PROBE_SOURCE_ID="{cls.source_id}"',
                str(SOURCE / "proc_observer.c"),
                str(SOURCE / "proc_discovery_control.c"),
                "-o",
                str(cls.binary),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )

    def execute(self, case=None):
        args = [str(self.binary), "proc-discovery-control"]
        if case is not None:
            args += ["--case", case]
        result = subprocess.run(args, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.stderr, "")
        self.assertLess(len(result.stdout.encode()), 65536)
        report = json.loads(result.stdout)
        self.assertEqual(report["source_id"], self.source_id)
        self.assertFalse(report["runtime_acceptance"])
        self.assertFalse(report["ownership_complete"])
        self.assertTrue(report["kernel_uid_known"])
        self.assertEqual(report["android_acceptance"], "pending")
        self.assertEqual(report["reported_uid"], os.getuid())
        self.assertEqual(report["kernel_uid"], os.getuid())
        self.assertIn("errno", report["children_node"])
        self.assertTrue(report["harness_subreaper"])
        return result.returncode, report

    @staticmethod
    def birth(identity):
        return (
            identity["pid"],
            identity["start_ticks"],
            identity["boot_id"],
            identity["kernel_uid"],
        )

    def assert_case(self, case):
        code, report = self.execute(case)
        self.assertEqual(code, 1 if case in BLOCKED else 0, report)
        self.assertTrue(report["controls_pass"], report)
        self.assertEqual(len(report["cases"]), 1)
        item = report["cases"][0]
        self.assertEqual(item["name"], case)
        self.assertTrue(item["control_pass"], item)
        self.assertEqual(item["complete"], case not in BLOCKED)
        self.assertEqual(report["selected_cases_complete"], case not in BLOCKED)
        self.assertTrue(item["peer_survived"])
        self.assertEqual(item["cleanup_errno"], 0)
        self.assertFalse(item["emergency_cleanup"])
        expected = {entry["role"]: entry["identity"] for entry in item["expected"]}
        discovered = {self.birth(identity) for identity in item["discovered"]}
        # Exact birth sets from independent child pipe witnesses, not only counts.
        self.assertEqual(discovered, {self.birth(expected[role]) for role in range(4)})
        self.assertNotIn(self.birth(expected[4]), discovered)
        self.assertEqual(expected[2]["ppid"], expected[1]["pid"])
        self.assertEqual(expected[3]["ppid"], expected[0]["pid"])
        threaded = next(w for w in item["expected"] if w["role"] == 3)
        self.assertGreater(threaded["creator_tid"], 1)
        self.assertNotEqual(threaded["creator_tid"], expected[0]["pid"])
        self.assertEqual(expected[4]["pid"], expected[4]["sid"])
        self.assertNotEqual(expected[4]["sid"], expected[0]["sid"])
        if case == "unwitnessed-orphan":
            self.assertIn(6, expected)
            self.assertNotIn(self.birth(expected[6]), discovered)
            self.assertTrue(item["candidate_refused"])
            self.assertTrue(item["oracle_intervention"])
            self.assertEqual(item["error"], errno.ENOLINK)
            self.assertEqual(item["reason"], "unwitnessed_orphan_lineage_unproven")
        elif case in {"group-change", "session-change"}:
            old = next(i for i in item["discovered"] if i["pid"] == expected[3]["pid"])
            self.assertNotEqual(
                old["pgid" if case == "group-change" else "sid"],
                expected[3]["pgid" if case == "group-change" else "sid"],
            )
            self.assertEqual(item["signal_errno"], errno.ESTALE)
        elif case in {"stale-birth", "stale-boot", "unknown"}:
            if case == "unknown":
                self.assertEqual(
                    item["observation"], {"state": "unknown", "errno": errno.EINVAL}
                )
            self.assertEqual(item["signal_errno"], errno.ESTALE)
        elif case.startswith("scan-"):
            wanted = {
                "scan-limit": errno.EOVERFLOW,
                "scan-deadline": errno.ETIMEDOUT,
                "scan-cancel": errno.ECANCELED,
            }[case]
            self.assertFalse(item["scan"]["complete"])
            self.assertEqual(item["scan"]["errno"], wanted)
            self.assertEqual(item["signal_errno"], wanted)
        elif case in {"sigterm", "sigint"}:
            self.assertEqual(item["observation"]["state"], "gone")
            self.assertTrue(item["signal_sent"])
            self.assertEqual(item["signal_errno"], 0)
            self.assertEqual(
                item["acknowledged_signals"][3],
                signal.SIGINT if case == "sigint" else signal.SIGTERM,
            )
        elif case == "complete-helper-stop":
            self.assertEqual(item["acknowledged_signals"][:4], [signal.SIGTERM] * 4)
            self.assertEqual(item["acknowledged_signals"][4], 0)
        elif case in {"witnessed-parent-loss", "leader-loss"}:
            survivor = 2 if case == "witnessed-parent-loss" else 1
            self.assertTrue(item["signal_sent"])
            self.assertEqual(item["signal_errno"], 0)
            self.assertEqual(item["acknowledged_signals"][survivor], signal.SIGTERM)
        elif case == "cancel-timeout":
            self.assertEqual(item["observation"]["state"], "live")
            self.assertTrue(item["signal_sent"])
            self.assertEqual(item["signal_errno"], errno.ETIMEDOUT)
            self.assertTrue(item["oracle_intervention"])
        if item["candidate_refused"] and not item["signal_sent"]:
            self.assertTrue(item["refused_target_survived"])
        if case not in BLOCKED:
            self.assertFalse(item["oracle_intervention"])

    def test_full_command_retains_two_incomplete_cases(self):
        code, report = self.execute()
        self.assertEqual(code, 1)
        self.assertTrue(report["controls_pass"], report)
        self.assertFalse(report["selected_cases_complete"])
        self.assertEqual([c["name"] for c in report["cases"]], list(CASES))
        self.assertEqual(
            {c["name"] for c in report["cases"] if not c["complete"]}, BLOCKED
        )
        self.assertTrue(
            all(
                c["peer_survived"] and not c["emergency_cleanup"]
                for c in report["cases"]
            )
        )

    def test_unknown_arguments_do_not_start_a_fixture(self):
        for args in [
            [],
            ["owner-control"],
            ["proc-discovery-control", "--case", "no-such-case"],
            ["proc-discovery-control", "--pid", "1"],
        ]:
            result = subprocess.run(
                [str(self.binary), *args], capture_output=True, timeout=3
            )
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, b"")


def _case_test(case):
    def run(self):
        self.assert_case(case)

    return run


for _case in CASES:
    setattr(ProcessProbeTests, "test_" + _case.replace("-", "_"), _case_test(_case))
