"""Raw finite-witness consistency controls; no Android/runtime acceptance."""

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import tempfile
import unittest
import zipfile

HERE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "wrapper_report", HERE / "runtime/check_wrapper_report.py"
)
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)
HEAD, SIGNER, SOURCE = "a" * 40, "b" * 64, "c" * 64


def fixture_package(root):
    elf = bytearray(64)
    elf[:6] = b"\x7fELF\x02\x01"
    struct.pack_into("<H", elf, 18, 62)
    elf += SOURCE.encode() + b"\0"
    sha = hashlib.sha256(elf).hexdigest()
    native = {
        "source_commit": HEAD,
        "adapter_dirty": False,
        "wrapper_controls_only": True,
        "peer_components": {},
        "debuggable": False,
        "abis": ["x86_64"],
        "adapter_sources": {
            "platforms/android/native/runtime/collector_fixture.c": SOURCE
        },
        "parity_helper": {"kind": "checkout", "source_head": HEAD},
        "wrapper_fixture": {"source_sha256": SOURCE},
        "native_files": {"lib/x86_64/libcollector_fixture.so": sha},
    }
    encoded = json.dumps(native).encode()
    apk = root / "fixture.apk"
    with zipfile.ZipFile(apk, "w") as z:
        z.writestr("assets/runtime/native-provenance.json", encoded)
        z.writestr("lib/x86_64/libcollector_fixture.so", elf)
    apk_sha = hashlib.sha256(apk.read_bytes()).hexdigest()
    provenance_sha = hashlib.sha256(encoded).hexdigest()
    report = {
        "source_commit": HEAD,
        "native_provenance_sha256": provenance_sha,
        "installed_apk_sha256": apk_sha,
        "package_signer_sha256": [SIGNER],
        "device_api": 36,
        "target_sdk": 36,
        "debuggable": False,
        "abis": ["x86_64"],
        "stack_acceptance": False,
        "production_ownership": False,
        "owner_death_solution": False,
        "wrapper_fixture_controls_pass": True,
        "retained_invocations": [],
        "retained_guest_owners": 0,
        "run_id": "fixture",
        "attempt": "/private/attempt",
        "fixture_executable_sha256": sha,
        "checks": [],
    }
    names = [
        "cwd-one",
        "cwd-two",
        "proot-cwd",
        "exact-64k",
        "overflow",
        "partial",
        "trailing",
        "deadline",
        "cancel",
    ]
    for i, name in enumerate(names):
        c = {
            "name": "wrapper-" + name,
            "invocation_id": str(i),
            "mode": "proot" if i == 2 else "native",
            "run_identity": {
                "run_id": "fixture",
                "source_commit": HEAD,
                "native_provenance_sha256": provenance_sha,
                "installed_apk_sha256": apk_sha,
                "package_signer_sha256": [SIGNER],
                "java_owner_start_ticks": 1,
                "boot_id": "boot",
                "fixture_source_sha256": SOURCE,
            },
            "command": ["/package/lib/x86_64/libcollector_fixture.so", "cwd"],
            "cwd_fresh_empty": True,
            "cwd": "/private/attempt/work/" + name,
            "cwd_device": 1,
            "cwd_inode": i + 1,
            "output_file": "/private/attempt/reports/" + name,
            "elapsed_ms": 251,
            "deadline_ms": 250,
            "termination_attempted": False,
            "fixture_control_pass": True,
            "exit": 0,
            "direct_child_exit_observed": True,
            "process_alive_after_wait": False,
            "cancelled": False,
            "timeout": False,
            "retained_ownership": False,
            "output_accepted": True,
            "output_overflow": False,
            "output_truncated": False,
        }
        if i < 3:
            output = json.dumps(
                {
                    "fixture": "cwd",
                    "source_id": SOURCE,
                    "abi": "x86_64",
                    "pid": 5,
                    "start_ticks": 10,
                    "empty_before_launch": True,
                    "cwd_device": 1,
                    "cwd_inode": i + 1,
                }
            )
        elif i == 3:
            output = '{"padding":"' + "a" * (65536 - len('{"padding":""}')) + '"}'
        else:
            output = None
            c.update(
                output_accepted=False,
                output_error="refused",
                output_bytes_observed=65537 if i == 4 else 10,
                output_overflow=i == 4,
                output_truncated=i == 4,
            )
        if output is not None:
            c.update(
                output=output,
                output_bytes_observed=len(output.encode()),
                output_sha256=hashlib.sha256(output.encode()).hexdigest(),
            )
        if i >= 7:
            c.update(
                retained_ownership=True,
                direct_child_exit_observed=False,
                process_alive_after_wait=True,
                exit=-1,
                timeout=i == 7,
                cancelled=i == 8,
                new_launch_refused_while_retained=True,
                fixture_reference_released_after_oracle=True,
                fixture_oracle={
                    "exact_finite_fixture_cleanup": True,
                    "general_descendant_completeness": False,
                    "signals_sent": False,
                    "direct_owner_exit_observed": True,
                    "cancel_flag_observed": i == 8,
                    "parent": {
                        "fixture": "family",
                        "source_id": SOURCE,
                        "owner_pid": 5,
                        "owner_start_ticks": 10,
                        "child_pid": 6,
                        "child_start_ticks": 11,
                        "specific_child_reaped": True,
                        "child_status": 0,
                        "owned_children": 1,
                    },
                    "child": {"pid": 6, "start_ticks": 11, "finite_child_done": True},
                },
            )
        base = "/package/lib/x86_64/"
        suffix = (
            ["cwd"]
            if i < 3
            else (
                ["bytes", "65536"]
                if i == 3
                else (
                    ["bytes", "65537"] if i == 4 else ([name] if i < 7 else ["family"])
                )
            )
        )
        c["command"] = [base + "libcollector_fixture.so"] + suffix
        if i == 2:
            c["command"] = [
                base + "libproot.so",
                "-v",
                "0",
                "-r",
                "/",
                "-w",
                c["cwd"],
            ] + c["command"]
        report["checks"].append(c)
    return apk, report


class ReportControls(unittest.TestCase):
    def test_raw_contradictions_and_output_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            apk, original = fixture_package(Path(directory))
            self.assertEqual(
                checker.validate(original, apk, HEAD, SIGNER)["status"], "passed"
            )
            mutations = {
                "reaping": lambda r: r["checks"][7]["fixture_oracle"]["parent"].update(
                    specific_child_reaped=False, child_status=9, owned_children=0
                ),
                "child-done": lambda r: r["checks"][7]["fixture_oracle"][
                    "child"
                ].update(finite_child_done=False),
                "owner-exit": lambda r: r["checks"][7]["fixture_oracle"].update(
                    direct_owner_exit_observed=False
                ),
                "parent-source": lambda r: r["checks"][7]["fixture_oracle"][
                    "parent"
                ].update(source_id="0" * 64),
                "executable": lambda r: r.update(fixture_executable_sha256="0" * 64),
                "accepted-exit": lambda r: r["checks"][0].update(
                    exit=3,
                    cancelled=True,
                    timeout=True,
                    direct_child_exit_observed=False,
                ),
                "child-birth": lambda r: r["checks"][7]["fixture_oracle"][
                    "child"
                ].update(start_ticks=12),
                "owner-status": lambda r: r["checks"][7]["fixture_oracle"].update(
                    direct_owner_exit=3
                ),
                "abi": lambda r: r.update(abis=["arm64-v8a"]),
                "command-abi": lambda r: r["checks"][0].update(
                    command=["/package/lib/arm64-v8a/libcollector_fixture.so"]
                ),
                "accepted-hash": lambda r: r["checks"][0].update(
                    output_sha256="0" * 64
                ),
                "refused-output": lambda r: r["checks"][7].update(output="{}"),
                "cancel-timeout": lambda r: r["checks"][8].update(timeout=True),
            }
            for key, wrong in {
                "exit": 3,
                "cancelled": True,
                "timeout": True,
                "direct_child_exit_observed": False,
                "process_alive_after_wait": True,
                "retained_ownership": True,
            }.items():
                mutations["accepted-" + key] = lambda r, k=key, v=wrong: r["checks"][
                    0
                ].update({k: v})
            for key, wrong in {
                "specific_child_reaped": False,
                "child_status": 9,
                "owned_children": 0,
                "owner_start_ticks": 0,
                "owner_pid": 6,
            }.items():
                mutations["parent-" + key] = lambda r, k=key, v=wrong: r["checks"][7][
                    "fixture_oracle"
                ]["parent"].update({k: v})
            for name, mutate in mutations.items():
                with self.subTest(name=name):
                    report = copy.deepcopy(original)
                    mutate(report)
                    with self.assertRaises((ValueError, KeyError, TypeError)):
                        checker.validate(report, apk, HEAD, SIGNER)


if __name__ == "__main__":
    unittest.main()
