#!/usr/bin/env python3
"""Bind finite13e/networkcde observations to an independently selected actual APK.

A valid measurement may be blocked or show refused/incomplete candidate cases.
Neither finite oracle cleanup nor callback TLS establishes production ownership.
"""

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import zipfile

ANDROID = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ANDROID))
from runtime.peer_inputs import (  # noqa: E402
    INTEGRATION_PROFILE,
    SUBREAPER_HEAD,
    SUBREAPER_CASES,
    NETWORK_HEAD,
    integration_components,
)
from verify_apk import verify_package  # noqa: E402


def require(ok, message):
    if not ok:
        raise ValueError(message)


def positive_int(value):
    return type(value) is int and value > 0


def finite_case(raw, which, exit_code, abi, uid, boot):
    require(
        raw["source_id"] == SUBREAPER_HEAD
        and raw["probe"] == "subreaper-orphan-control",
        "Process source/probe mismatch",
    )
    require(
        raw["compiled_platform"] == "android"
        and raw["compiled_api"] == 29
        and raw["compiled_arch"] == {"x86_64": "x86_64", "arm64-v8a": "aarch64"}[abi],
        "Process native ABI/API mismatch",
    )
    require(
        raw["runtime_acceptance"] is False
        and raw["production_ownership"] is False
        and raw["owner_death_solution"] is False,
        "Finite probe promoted to production",
    )
    require(
        raw["kernel_uid_known"] is True
        and raw["kernel_uid"] == uid
        and raw["oracle_subreaper"] is True
        and raw["harness_errno"] == 0,
        "Process prerequisites unknown/refused",
    )
    require(
        len(raw["cases"]) == 1 and raw["cases"][0]["name"] == which,
        "Process case inventory mismatch",
    )
    c = raw["cases"][0]
    require(
        type(c["cleanup_errno"]) is int and c["cleanup_errno"] == 0,
        "Finite oracle cleanup unknown/failed",
    )
    complete, passed = c["candidate_complete"], c["control_pass"]
    require(
        type(complete) is bool
        and type(passed) is bool
        and raw["controls_pass"] is passed
        and raw["selected_candidate_cases_complete"] is complete
        and type(exit_code) is int
        and exit_code == (0 if complete and passed else 1),
        "Process exit/summary contradiction",
    )
    require(
        not complete or which in SUBREAPER_CASES[:5],
        "Refusal/owner-loss case claims completeness",
    )
    require(
        not passed or which not in SUBREAPER_CASES[:5] or complete,
        "Positive control hides incomplete candidate",
    )
    if complete:
        for key in [
            "owner_verified_outside",
            "owner_verified_inside",
            "intermediates_reaped_before_first_scan",
            "waitability_verified",
            "scan_complete",
            "candidate_waited",
            "no_children",
            "peer_survived",
        ]:
            require(c[key] is True, "Candidate completion lacks " + key)
        for key in [
            "error",
            "candidate_errno",
            "prctl_errno",
            "scan_errno",
            "scan_unknown",
            "candidate_exit_code",
            "candidate_exit_signal",
        ]:
            require(
                type(c[key]) is int and c[key] == 0,
                "Candidate completion contradicts " + key,
            )
        require(
            passed
            and c["oracle_intervention"] is False
            and c["subreaper_before"] == 0
            and c["subreaper_after"] == 1
            and c["owner_lost"] is False,
            "Candidate completeness/owner contradiction",
        )
        count = 3 if which == "nested-orphan" else 2
        owner = c["owner"]
        require(
            positive_int(owner["pid"])
            and positive_int(owner["start_ticks"])
            and owner["kernel_uid"] == uid
            and owner["boot_id"] == boot,
            "Owner birth mismatch",
        )
        expected = {w["role"]: w["identity"] for w in c["expected"]}
        require(
            len(expected) == len(c["expected"]) and 5 in expected,
            "Independent peer/witness inventory missing/duplicated",
        )
        found = c["discovered"]
        require(
            len(found) == count and c["sent"] == c["reaped"] == count,
            "Finite inventory/signal/reap count mismatch",
        )
        births = set()
        for role in range(count):
            witness = expected[role]
            birth = (
                witness["pid"],
                witness["start_ticks"],
                witness["kernel_uid"],
                witness["boot_id"],
            )
            require(
                positive_int(birth[0])
                and positive_int(birth[1])
                and birth[2:] == (uid, boot),
                "Independent birth invalid",
            )
            require(
                witness["ppid"] != owner["pid"],
                "Original reparenting witness overwritten",
            )
            matches = [
                p
                for p in found
                if (p["pid"], p["start_ticks"], p["kernel_uid"], p["boot_id"]) == birth
                and p["ppid"] == owner["pid"]
            ]
            require(
                len(matches) == 1 and birth not in births,
                "Reparented exact birth set mismatch",
            )
            births.add(birth)
            require(
                c["acknowledged_signals"][role] == (2 if which == "sigint" else 15),
                "Signal acknowledgement mismatch",
            )
        peer = expected[5]
        require(
            (peer["pid"], peer["start_ticks"], peer["kernel_uid"], peer["boot_id"])
            not in births,
            "Peer admitted as child",
        )
    if which == "owner-loss" and passed:
        require(
            not complete
            and c["owner_lost"] is True
            and c["orphan_survived_owner_loss"] is True
            and c["oracle_intervention"] is True
            and c["candidate_waited"] is True
            and c["candidate_exit_code"] == -1
            and c["candidate_exit_signal"] == 15,
            "Owner-loss intervention missing/hidden",
        )
        expected = {w["role"]: w["identity"] for w in c["expected"]}
        orphan, witness = c["orphan_after_owner_loss"], expected[0]
        require(
            all(
                orphan[k] == witness[k]
                for k in ["pid", "start_ticks", "kernel_uid", "boot_id"]
            )
            and orphan["ppid"] != c["owner"]["pid"],
            "Owner-loss surviving birth/reparenting mismatch",
        )
    return {
        "case": which,
        "control_pass": passed,
        "candidate_complete": complete,
        "candidate_errno": c["candidate_errno"],
        "oracle_intervention": c["oracle_intervention"],
        "finite_oracle_cleanup": True,
    }


def released_births(raw, observations, uid, boot):
    c = raw["cases"][0]
    identities = [w["identity"] for field in ["expected", "changed"] for w in c[field]]
    identities += c["discovered"] + [c["owner"], c["orphan_after_owner_loss"]]
    expected = set()
    for identity in identities:
        pid, ticks = identity["pid"], identity["start_ticks"]
        if pid == ticks == 0:
            continue
        require(
            positive_int(pid)
            and positive_int(ticks)
            and identity["kernel_uid"] == uid
            and identity["boot_id"] == boot,
            "Post-cleanup identity unknown",
        )
        expected.add((pid, ticks, boot))
    found = set()
    for observation in observations:
        birth = (observation["pid"], observation["start_ticks"], observation["boot_id"])
        require(
            birth in expected and birth not in found,
            "Unexpected/duplicate post-cleanup birth",
        )
        state = observation["observation"]
        require(
            state == "absent"
            or (
                state == "different-birth"
                and positive_int(observation["current_start_ticks"])
                and observation["current_start_ticks"] != observation["start_ticks"]
            ),
            "Finite birth live/unknown after cleanup",
        )
        found.add(birth)
    require(found == expected and found, "Post-cleanup finite inventory incomplete")


def network_case(call, component):
    require(
        call["source_head"] == NETWORK_HEAD
        and call["node_started"] is False
        and call["enrollment_attempted"] is False,
        "Network source/mode drift",
    )
    if call["retained_ownership"] is True:
        require(
            call.get("failed") is True and call["control_pass"] is False,
            "Unknown network operation claimed success",
        )
        return False
    require(
        call["api_returned_after_close"] is True and call.get("failed", False) is False,
        "Network close/return evidence missing",
    )
    raw = call["report"]
    require(raw["stack_acceptance"] is False, "Callbacks promoted to stack readiness")
    ready = raw.get("ready") is True
    require(
        call["control_pass"] is (ready and call["bridge_identity_verified"]),
        "Network control/readiness mismatch",
    )
    if ready:
        require(
            raw["native_bridge_sha256"] == component["bridge_sha256"]
            and call["bridge_identity_verified"] is True,
            "Runtime JNI bridge mismatch",
        )
        require(
            raw["probe"] == "android-network-v1" and raw["schema"] == 1,
            "Network probe/schema mismatch",
        )
        for field in [
            "sdk_interfaces_ok",
            "socket_cookie_required",
            "dns_ok",
            "udp_socket_bound",
            "tcp_socket_bound",
            "tls_ok",
        ]:
            require(raw[field] is True, "Network readiness lacks " + field)
        for field in [
            "generation",
            "network_handle",
            "public_root_count",
            "android_dns_queries",
            "android_socket_binds",
            "peer_certificate_expires_at",
        ]:
            require(
                positive_int(raw[field]), "Missing actual network witness: " + field
            )
        require(
            raw["android_socket_binds"] >= 2
            and raw["interfaces"]
            and re.fullmatch(r"[0-9a-f]{64}", raw["public_roots_sha256"]),
            "Network interface/root/bind witness invalid",
        )
    return ready


def validate(report, apk, expected_commit, signer_sha256, debuggable, root, aapt2):
    require(
        re.fullmatch(r"[0-9a-f]{40}", expected_commit) is not None,
        "Independent full adapter SHA required",
    )
    require(
        re.fullmatch(r"[0-9a-f]{64}", signer_sha256) is not None,
        "Independent signer SHA256 required",
    )
    packaged = verify_package(
        apk,
        root,
        root / "scripts/check_runtime_parity.py",
        expected_commit,
        aapt2=aapt2,
    )
    with zipfile.ZipFile(apk) as z:
        encoded = z.read("assets/runtime/native-provenance.json")
        native = json.loads(encoded)
    components = integration_components(native)
    apk_sha = hashlib.sha256(apk.read_bytes()).hexdigest()
    require(
        native["adapter_dirty"] is False
        and native["debuggable"] is debuggable
        and native["parity_helper"]["kind"] == "checkout"
        and native["parity_helper"]["source_head"] == expected_commit,
        "Uncommitted/wrong-profile package",
    )
    require(
        report["source_commit"] == expected_commit
        and report["diagnostic_profile"] == INTEGRATION_PROFILE
        and report["installed_apk_sha256"] == apk_sha
        and report["native_provenance_sha256"] == hashlib.sha256(encoded).hexdigest()
        and report["package_signer_sha256"] == [signer_sha256],
        "Report adapter/APK/signature binding mismatch",
    )
    require(
        report["device_api"] == report["target_sdk"] == 36
        and report["debuggable"] is debuggable
        and report["stack_acceptance"] is False
        and report["production_ownership"] is False
        and report["owner_death_solution"] is False,
        "Actual profile/API/debug/acceptance mismatch",
    )
    abi = report["executed_abi"]
    require(abi in native["abis"] and abi in report["abis"], "Unpackaged execution ABI")
    for filename in ["libsubreaper_probe.so", "libschemii_network.so"]:
        require(
            report["integration_library_sha256"][filename]
            == native["native_files"]["lib/" + abi + "/" + filename],
            "Installed component/ABI mismatch",
        )
    checks = report["checks"]
    require(
        checks and checks[0]["name"] == "network-callbacks-cde",
        "Network callback record missing",
    )
    require(
        checks[0]["library_sha256"]
        == report["integration_library_sha256"]["libschemii_network.so"],
        "Callback library binding drift",
    )
    ni = checks[0]["run_identity"]
    require(
        ni["run_id"] == report["run_id"]
        and ni["source_commit"] == expected_commit
        and ni["installed_apk_sha256"] == apk_sha
        and ni["native_provenance_sha256"] == report["native_provenance_sha256"]
        and ni["package_signer_sha256"] == [signer_sha256]
        and ni["debuggable"] is debuggable
        and ni["diagnostic_profile"] == INTEGRATION_PROFILE,
        "Stale callback invocation identity",
    )
    require(
        type(checks[0]["elapsed_ms"]) is int and checks[0]["elapsed_ms"] >= 0,
        "Network elapsed observation missing",
    )
    network_ready = network_case(checks[0], components["network"])
    expected = [
        (mode, which) for mode in ["native", "proot"] for which in SUBREAPER_CASES
    ]
    require(len(checks) <= 33, "Extra integration cases")
    results, invocations, cwds = [], set(), set()
    for c, (mode, which) in zip(checks[1:], expected):
        require(
            c["name"] == "subreaper-orphan-v1-" + mode + "-" + which
            and c["mode"] == mode,
            "Case/mode order mismatch",
        )
        identity = c["run_identity"]
        require(
            identity["run_id"] == report["run_id"]
            and identity["source_commit"] == expected_commit
            and identity["installed_apk_sha256"] == apk_sha
            and identity["native_provenance_sha256"]
            == report["native_provenance_sha256"]
            and identity["package_signer_sha256"] == [signer_sha256]
            and identity["debuggable"] is debuggable
            and identity["diagnostic_profile"] == INTEGRATION_PROFILE
            and positive_int(identity["java_owner_start_ticks"])
            and identity["boot_id"],
            "Stale invocation identity",
        )
        require(
            c["invocation_id"] not in invocations
            and c["cwd"] not in cwds
            and c["cwd_fresh_empty"] is True
            and c["cwd"] == report["attempt"] + "/work/" + c["name"]
            and c["output_file"]
            == report["attempt"] + "/reports/" + c["name"] + ".txt",
            "Cwd/report isolation mismatch",
        )
        invocations.add(c["invocation_id"])
        cwds.add(c["cwd"])
        require(
            c["deadline_ms"] == 45000
            and type(c["elapsed_ms"]) is int
            and c["elapsed_ms"] >= 0
            and c["termination_attempted"] is False
            and c["timeout"] is False
            and c["cancelled"] is False
            and c["direct_child_exit_observed"] is True
            and c["process_alive_after_wait"] is False
            and c["retained_ownership"] is False
            and c["completion_state"] == "committed"
            and c["journal_outcome"] == "provisional-no-output-acceptance",
            "Incomplete/cancelled invocation accepted",
        )
        data = c["output"].encode()
        require(
            c["output_accepted"] is True
            and c["output_overflow"] is False
            and c["output_truncated"] is False
            and len(data) == c["output_bytes_observed"] <= 65536
            and hashlib.sha256(data).hexdigest() == c["output_sha256"],
            "Incomplete process output",
        )
        argv = c["command"]
        executable = argv[0] if mode == "native" else argv[7]
        require(
            executable.startswith("/data/app/")
            and executable.endswith("/libsubreaper_probe.so"),
            "Not an absolute packaged component",
        )
        tail = [executable, "subreaper-orphan-control", "--case", which]
        expected_argv = (
            tail
            if mode == "native"
            else [
                str(Path(executable).parent / "libproot.so"),
                "-v",
                "0",
                "-r",
                "/",
                "-w",
                c["cwd"],
            ]
            + tail
        )
        require(
            argv == expected_argv
            and c["component_source_head"] == SUBREAPER_HEAD
            and c["component_library_sha256"]
            == report["integration_library_sha256"]["libsubreaper_probe.so"],
            "Packaged command/component mismatch",
        )
        result = finite_case(
            json.loads(c["output"]),
            which,
            c["exit"],
            abi,
            identity["java_owner_uid"],
            identity["boot_id"],
        )
        released_births(
            json.loads(c["output"]),
            c["finite_post_birth_observations"],
            identity["java_owner_uid"],
            identity["boot_id"],
        )
        require(
            c["finite_oracle_cleanup_verified"] is True,
            "Finite wrapper cleanup claim missing",
        )
        result["mode"] = mode
        results.append(result)
    retained = bool(
        report["retained_invocations"]
        or report["retained_native_operations"]
        or report["retained_guest_owners"]
    )
    complete_inventory = (
        len(results) == 32 and report.get("integration_case_inventory_complete") is True
    )
    if not complete_inventory:
        require(
            report.get("error") or retained,
            "Incomplete inventory claimed clean success",
        )
    safe = (
        not retained
        and not report.get("error")
        and not report.get("report_write_error")
    )
    process_ready = complete_inventory and all(c["control_pass"] for c in results)
    return {
        "status": "measured" if safe and complete_inventory else "blocked",
        "source_commit": expected_commit,
        "run_id": report["run_id"],
        "apk_sha256": apk_sha,
        "abi": abi,
        "debuggable": debuggable,
        "network_callbacks_ready": network_ready,
        "subreaper_finite_controls_ready": process_ready,
        "candidate_complete_cases": sum(c["candidate_complete"] for c in results),
        "oracle_intervention_cases": sum(c["oracle_intervention"] for c in results),
        "measured_process_cases": len(results),
        "process_cases": results,
        "safe_to_replace_disposable_apk": safe,
        "retained_ownership_or_uncertainty": retained,
        "report_error": report.get("error"),
        "canonical_source": packaged["source"],
        "runtime_acceptance": "pending",
        "production_ownership": False,
        "owner_death_solution": False,
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("report", type=Path)
    p.add_argument("--apk", type=Path, required=True)
    p.add_argument("--expected-commit", required=True)
    p.add_argument("--signer-sha256", required=True)
    p.add_argument("--root", type=Path, default=ANDROID.parents[1])
    p.add_argument("--aapt2", type=Path, required=True)
    debug = p.add_mutually_exclusive_group(required=True)
    debug.add_argument("--debuggable", action="store_true")
    debug.add_argument("--non-debuggable", action="store_true")
    a = p.parse_args()
    result = validate(
        json.loads(a.report.read_text()),
        a.apk,
        a.expected_commit,
        a.signer_sha256,
        a.debuggable,
        a.root,
        a.aapt2,
    )
    print(json.dumps(result, indent=2))
    if result["status"] == "blocked":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
