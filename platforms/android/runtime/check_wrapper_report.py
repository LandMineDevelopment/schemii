#!/usr/bin/env python3
"""Validate an actual wrapper-only Android report against independent APK/source/cert inputs.

Finite collector fixtures do not establish production descendant or owner-loss cleanup.
"""

import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import zipfile


def require(value, message):
    if not value:
        raise ValueError(message)


def validate(report, apk, expected_commit, signer_sha256):
    require(
        bool(re.fullmatch(r"[0-9a-f]{40}", expected_commit)),
        "Independent full source SHA required",
    )
    require(
        bool(re.fullmatch(r"[0-9a-f]{64}", signer_sha256)),
        "Independent signing-certificate SHA256 required",
    )
    with apk.open("rb") as stream:
        apk_sha = hashlib.file_digest(stream, "sha256").hexdigest()
    with zipfile.ZipFile(apk) as package:
        encoded = package.read("assets/runtime/native-provenance.json")
        native = json.loads(encoded)
        require(
            native["adapter_sources"][
                "platforms/android/native/runtime/collector_fixture.c"
            ]
            == native["wrapper_fixture"]["source_sha256"],
            "Fixture source/provenance mismatch",
        )
        fixtures = {}
        for abi in native["abis"]:
            path = "lib/" + abi + "/libcollector_fixture.so"
            data = package.read(path)
            require(
                data[:6] == b"\x7fELF\x02\x01", "Fixture is not ELF64 little endian"
            )
            require(
                struct.unpack_from("<H", data, 18)[0]
                == {"x86_64": 62, "arm64-v8a": 183}[abi],
                "Fixture ELF ABI mismatch",
            )
            sha = hashlib.sha256(data).hexdigest()
            require(
                sha == native["native_files"][path], "Packaged fixture digest mismatch"
            )
            require(
                native["wrapper_fixture"]["source_sha256"].encode() + b"\0" in data,
                "Packaged fixture source identity missing",
            )
            fixtures[abi] = sha
    require(
        native["source_commit"] == expected_commit and native["adapter_dirty"] is False,
        "APK source is not the selected committed head",
    )
    require(
        native["wrapper_controls_only"] is True
        and not native["peer_components"]
        and not native.get("guest_payloads"),
        "Not a wrapper-only package",
    )
    require(
        native["parity_helper"]["kind"] == "checkout"
        and native["parity_helper"]["source_head"] == expected_commit,
        "Default in-checkout helper identity required",
    )
    require(
        native["debuggable"] is False,
        "This focused receipt requires the selected non-debuggable package",
    )
    require(
        report.get("error") is None and report["source_commit"] == expected_commit,
        "Report launch/source failure",
    )
    require(
        report["native_provenance_sha256"] == hashlib.sha256(encoded).hexdigest(),
        "Report provenance mismatch",
    )
    require(report["installed_apk_sha256"] == apk_sha, "Installed APK byte mismatch")
    require(
        report["package_signer_sha256"] == [signer_sha256], "Installed signer mismatch"
    )
    require(
        report["device_api"] == report["target_sdk"] == 36
        and report["debuggable"] is False,
        "Actual Android API/debug label mismatch",
    )
    require(
        report["stack_acceptance"] is False
        and report["production_ownership"] is False
        and report["owner_death_solution"] is False,
        "Fixture promoted to production acceptance",
    )
    require(report["wrapper_fixture_controls_pass"] is True, "Wrapper fixture failure")
    require(
        report["retained_invocations"] == [] and report["retained_guest_owners"] == 0,
        "Unresolved retained process/uncertainty",
    )
    expected = [
        "wrapper-cwd-one",
        "wrapper-cwd-two",
        "wrapper-proot-cwd",
        "wrapper-exact-64k",
        "wrapper-overflow",
        "wrapper-partial",
        "wrapper-trailing",
        "wrapper-deadline",
        "wrapper-cancel",
    ]
    current_completion = (
        "platforms/android/app/src/main/java/development/landmine/schemii/runtimeprobe/CollectorCompletion.java"
        in native["adapter_sources"]
    )
    if current_completion:
        require(
            report["completion_protocol"] == "provisional-journal-v1",
            "Completion protocol missing",
        )
        expected += ["wrapper-late-read", "wrapper-late-journal"]
    checks = report["checks"]
    require(
        [c["name"] for c in checks] == expected, "Incomplete/extra collector inventory"
    )
    require(
        len({c["invocation_id"] for c in checks}) == len(checks),
        "Reused invocation identity",
    )
    require(len({c["cwd"] for c in checks}) == len(checks), "Reused cwd")
    fixture_abi = json.loads(checks[0]["output"])["abi"]
    require(
        fixture_abi in fixtures and fixture_abi in report["abis"],
        "Observed fixture ABI not packaged/supported",
    )
    require(
        report["fixture_executable_sha256"] == fixtures[fixture_abi],
        "Observed fixture executable mismatch",
    )
    fixture_path = checks[0]["command"][0]
    for c in checks:
        identity = c["run_identity"]
        require(
            identity["run_id"] == report["run_id"]
            and identity["source_commit"] == expected_commit,
            "Stale invocation identity",
        )
        require(
            identity["native_provenance_sha256"] == report["native_provenance_sha256"]
            and identity["installed_apk_sha256"] == apk_sha,
            "Invocation APK identity mismatch",
        )
        require(
            identity["package_signer_sha256"] == [signer_sha256]
            and identity["java_owner_start_ticks"] > 0
            and identity["boot_id"],
            "Owner/signature birth evidence missing",
        )
        require(
            c["cwd_fresh_empty"] is True
            and c["cwd"].startswith(report["attempt"] + "/work/"),
            "Cwd freshness/root failure",
        )
        require(
            c["output_file"].startswith(report["attempt"] + "/reports/")
            and not c["output_file"].startswith(c["cwd"] + "/"),
            "Report placed in fixture cwd",
        )
        require(
            type(c["elapsed_ms"]) is int and c["elapsed_ms"] >= 0,
            "Elapsed measurement missing",
        )
        require(
            identity["fixture_source_sha256"]
            == native["wrapper_fixture"]["source_sha256"],
            "Invocation fixture source mismatch",
        )
        command = c["command"]
        fixture = next(
            (Path(v) for v in command if v.endswith("/libcollector_fixture.so")), None
        )
        require(
            fixture is not None
            and fixture.parent.name == fixture_abi
            and str(fixture) == fixture_path,
            "Invocation fixture path/ABI mismatch",
        )
        if c["name"] == "wrapper-proot-cwd":
            require(
                c["mode"] == "proot"
                and command
                == [
                    str(fixture.parent / "libproot.so"),
                    "-v",
                    "0",
                    "-r",
                    "/",
                    "-w",
                    c["cwd"],
                    fixture_path,
                    "cwd",
                ],
                "PRoot fixture argv mismatch",
            )
        else:
            suffix = {
                "wrapper-cwd-one": ["cwd"],
                "wrapper-cwd-two": ["cwd"],
                "wrapper-exact-64k": ["bytes", "65536"],
                "wrapper-overflow": ["bytes", "65537"],
                "wrapper-partial": ["partial"],
                "wrapper-trailing": ["trailing"],
            }.get(c["name"], ["family"])
            require(command == [fixture_path] + suffix, "Native fixture argv mismatch")
        require(
            c["output_accepted"] is False
            or (
                c["exit"] == 0
                and type(c["exit"]) is int
                and c["direct_child_exit_observed"] is True
                and c["process_alive_after_wait"] is False
                and c["cancelled"] is False
                and c["timeout"] is False
                and c["retained_ownership"] is False
                and c["output_overflow"] is False
                and c["output_truncated"] is False
                and not c.get("output_error")
            ),
            "Accepted output contradicts owner/exit/cancellation evidence",
        )
        if c["output_accepted"] is True:
            data = c["output"].encode("utf-8")
            require(
                len(data) == c["output_bytes_observed"] <= 65536
                and hashlib.sha256(data).hexdigest() == c["output_sha256"],
                "Accepted output bytes/hash mismatch",
            )
        else:
            require(
                "output" not in c and "output_sha256" not in c,
                "Refused output contains accepted bytes/hash",
            )
        require(
            c["termination_attempted"] is False and c["fixture_control_pass"] is True,
            "Forced/intervened or failed collector control",
        )
    for c in checks[:3]:
        output = json.loads(c["output"])
        require(
            c["output_accepted"] is True
            and output["empty_before_launch"] is True
            and output["cwd_device"] == c["cwd_device"]
            and output["cwd_inode"] == c["cwd_inode"],
            "Fixture cwd witness mismatch",
        )
        require(
            output["source_id"] == native["wrapper_fixture"]["source_sha256"]
            and output["fixture"] == "cwd"
            and output["abi"] == fixture_abi
            and type(output["pid"]) is int
            and output["pid"] > 0
            and type(output["start_ticks"]) is int
            and output["start_ticks"] > 0,
            "Fixture source mismatch",
        )
    require(checks[2]["mode"] == "proot", "Packaged PRoot cwd not exercised")
    exact = checks[3]
    require(
        exact["output_accepted"] is True
        and exact["output_bytes_observed"] == 65536
        and len(exact["output"].encode()) == 65536,
        "Incomplete 64KiB capture",
    )
    require(
        hashlib.sha256(exact["output"].encode()).hexdigest() == exact["output_sha256"],
        "64KiB output digest mismatch",
    )
    json.loads(exact["output"])
    for c in checks[4:7]:
        require(
            c["output_accepted"] is False
            and "output" not in c
            and c["output_error"]
            and type(c["exit"]) is int
            and c["exit"] == 0
            and c["direct_child_exit_observed"] is True
            and c["process_alive_after_wait"] is False
            and c["cancelled"] is False
            and c["timeout"] is False
            and c["retained_ownership"] is False,
            "Partial/overflow JSON accepted",
        )
        overflow = c["name"] == "wrapper-overflow"
        require(
            c["output_overflow"] == overflow and c["output_truncated"] == overflow,
            "Overflow label mismatch",
        )
        if overflow:
            require(
                c["output_bytes_observed"] == 65537, "Overflow boundary not exercised"
            )
    for c in checks[7:]:
        late = c["name"].startswith("wrapper-late-")
        require(
            c["retained_ownership"] is True
            and c["direct_child_exit_observed"] is late
            and c["process_alive_after_wait"] is (not late),
            "Live uncertain owner was discarded",
        )
        require(
            c["output_accepted"] is False
            and "output" not in c
            and c["new_launch_refused_while_retained"] is True,
            "Retained owner did not fence later launch",
        )
        oracle = c["fixture_oracle"]
        require(
            c["fixture_reference_released_after_oracle"] is True
            and oracle["exact_finite_fixture_cleanup"] is True
            and oracle["general_descendant_completeness"] is False
            and oracle["signals_sent"] is False,
            "Finite fixture cleanup overstated/failed",
        )
        require(
            oracle["parent"]["child_pid"] == oracle["child"]["pid"]
            and oracle["parent"]["child_start_ticks"] == oracle["child"]["start_ticks"],
            "Finite child birth mismatch",
        )
        parent, child = oracle["parent"], oracle["child"]
        if current_completion:
            require(
                type(oracle["direct_owner_exit"]) is int
                and oracle["direct_owner_exit"] == 0,
                "Observed finite owner exit failed",
            )
        require(
            oracle["direct_owner_exit_observed"] is True
            and (
                "direct_owner_exit" not in oracle
                or (
                    type(oracle["direct_owner_exit"]) is int
                    and oracle["direct_owner_exit"] == 0
                )
            )
            and parent["fixture"] == "family"
            and parent["source_id"] == native["wrapper_fixture"]["source_sha256"]
            and parent["specific_child_reaped"] is True
            and type(parent["child_status"]) is int
            and parent["child_status"] == 0
            and type(parent["owned_children"]) is int
            and parent["owned_children"] == 1
            and child["finite_child_done"] is True,
            "Finite cleanup raw witnesses contradict success",
        )
        for value in [
            parent["owner_pid"],
            parent["owner_start_ticks"],
            parent["child_pid"],
            parent["child_start_ticks"],
            child["pid"],
            child["start_ticks"],
        ]:
            require(type(value) is int and value > 0, "Finite birth witness invalid")
        require(parent["owner_pid"] != child["pid"], "Owner and child identity overlap")
        require(
            c["exit"] == (0 if late else -1)
            and type(c["exit"]) is int
            and c["output_overflow"] is False
            and c["output_truncated"] is False
            and c["output_error"],
            "Unfinished collector exit/output contradiction",
        )
    require(
        checks[7]["timeout"] is True
        and checks[7]["cancelled"] is False
        and checks[7]["fixture_oracle"]["cancel_flag_observed"] is False
        and checks[7]["elapsed_ms"] >= checks[7]["deadline_ms"],
        "Deadline not exercised",
    )
    require(
        checks[8]["cancelled"] is True
        and checks[8]["timeout"] is False
        and checks[8]["fixture_oracle"]["cancel_flag_observed"] is True,
        "Interruption not exercised",
    )
    if current_completion:
        for c in checks[:7]:
            require(
                c["completion_state"] == "committed"
                and c["journal_outcome"] == "provisional-no-output-acceptance",
                "Completion/journal claim mismatch",
            )
        for c in checks[7:]:
            require(
                c["completion_state"] == "retained",
                "Cancelled/unfinished completion released",
            )
        for c, stage in zip(checks[9:], ["read", "journal"], strict=True):
            require(
                c["cancelled"] is True
                and c["timeout"] is False
                and c["cancellation_boundary"] == stage
                and c["fixture_oracle"]["cancel_flag_observed"] is True,
                "Late cancellation boundary not exercised",
            )
    return {
        "status": "passed",
        "scope": "finite collector fixtures only",
        "source_commit": expected_commit,
        "apk_sha256": apk_sha,
        "run_id": report["run_id"],
        "checks": len(checks),
        "runtime_acceptance": "pending",
        "production_ownership": False,
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("report", type=Path)
    p.add_argument("--apk", type=Path, required=True)
    p.add_argument("--expected-commit", required=True)
    p.add_argument("--signer-sha256", required=True)
    args = p.parse_args()
    print(
        json.dumps(
            validate(
                json.loads(args.report.read_text()),
                args.apk,
                args.expected_commit,
                args.signer_sha256,
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
