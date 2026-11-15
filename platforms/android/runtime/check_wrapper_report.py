#!/usr/bin/env python3
"""Validate an actual wrapper-only Android report against independent APK/source/cert inputs.

Finite collector fixtures do not establish production descendant or owner-loss cleanup.
"""

import argparse
import hashlib
import json
from pathlib import Path
import re
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
    checks = report["checks"]
    require(
        [c["name"] for c in checks] == expected, "Incomplete/extra collector inventory"
    )
    require(
        len({c["invocation_id"] for c in checks}) == len(checks),
        "Reused invocation identity",
    )
    require(len({c["cwd"] for c in checks}) == len(checks), "Reused cwd")
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
            output["source_id"] == native["wrapper_fixture"]["source_sha256"],
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
            c["output_accepted"] is False and "output" not in c and c["output_error"],
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
        require(
            c["retained_ownership"] is True
            and c["direct_child_exit_observed"] is False
            and c["process_alive_after_wait"] is True,
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
    require(
        checks[7]["timeout"] is True
        and checks[7]["elapsed_ms"] >= checks[7]["deadline_ms"],
        "Deadline not exercised",
    )
    require(
        checks[8]["cancelled"] is True
        and checks[8]["fixture_oracle"]["cancel_flag_observed"] is True,
        "Interruption not exercised",
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
