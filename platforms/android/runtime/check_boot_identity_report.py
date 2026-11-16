#!/usr/bin/env python3
"""Closed, identity-bound P1b observation checker; never accepts recovery authority."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
import zipfile

PROFILE = "boot-identity-p1b-v1"
PACKAGE = "development.landmine.schemii.bootidentityprobe"
PREFIX = "platforms/android/"
CORE = {
    PREFIX
    + "native/host/boot_identity.c": "854f90067b0838bb9d97108461b6d110426067534550d886a1f926900c95def3",
    PREFIX
    + "native/host/boot_identity.h": "b4621a7e4d66a4f2156f5b66943189a7d268f41f667bf4bf41c9f94cddab40a3",
}
SOURCES = tuple(CORE) + tuple(
    PREFIX + name
    for name in (
        "native/host/boot_identity_jni.c",
        "app/src/main/java/development/landmine/schemii/runtimeprobe/BootIdentityProbe.java",
        "app/src/main/java/development/landmine/schemii/runtimeprobe/ProbeActivity.java",
        "runtime/check_boot_identity_report.py",
    )
)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def loads(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "Duplicate JSON key")
            result[key] = value
        return result

    return json.loads(raw, object_pairs_hook=pairs)


def closed(value, keys):
    require(
        type(value) is dict and set(value) == set(keys.split()), "Closed schema drift"
    )


def integer(value, low, high):
    require(type(value) is int and low <= value <= high, "Integer type/range drift")


def source_identity(root, commit):
    require(
        bool(re.fullmatch("[0-9a-f]{40}", commit or "")), "Immutable commit required"
    )
    files = {
        name: sha(
            subprocess.check_output(
                ["git", "-C", str(root), "show", commit + ":" + name]
            )
        )
        for name in SOURCES
    }
    require(all(files[k] == v for k, v in CORE.items()), "Accepted core drift")
    return {
        "source_commit": commit,
        "files": files,
        "source_sha256": sha(
            json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
        ),
    }


def check_provenance(p, identity):
    closed(
        p,
        "schema kind stack_acceptance source_commit adapter_dirty target_sdk min_sdk compile_sdk application_id abis peer_components debuggable diagnostic_log_export wrapper_controls_only wrapper_fixture diagnostic_profile native_files native_donor toolchain_lock_sha256 adapter_sources parity_helper host_tools pending boot_identity",
    )
    require(p["schema"] == 1 and type(p["schema"]) is int, "Schema drift")
    require(p["kind"] == "disposable-feasibility", "Kind drift")
    require(
        p["source_commit"] == identity["source_commit"]
        and p["boot_identity"] == identity,
        "Component source drift",
    )
    for k in (
        "adapter_dirty",
        "debuggable",
        "stack_acceptance",
        "wrapper_controls_only",
    ):
        require(p[k] is False, "Unsafe profile flag")
    require(p["diagnostic_log_export"] is True, "Bounded export required")
    require(
        p["peer_components"] == {} and p["wrapper_fixture"] is None,
        "Unrelated campaign",
    )
    require(
        p["application_id"] == PACKAGE and p["diagnostic_profile"] == PROFILE,
        "Profile/package drift",
    )
    require(
        all(
            type(p[k]) is int and p[k] == v
            for k, v in (("target_sdk", 36), ("compile_sdk", 36), ("min_sdk", 29))
        ),
        "SDK drift",
    )
    require(
        type(p["abis"]) is list
        and 0 < len(p["abis"]) <= 2
        and len(set(p["abis"])) == len(p["abis"])
        and set(p["abis"]) <= {"x86_64", "arm64-v8a"},
        "ABI drift",
    )
    require(
        all(p["adapter_sources"].get(k) == v for k, v in identity["files"].items()),
        "Adapter component drift",
    )
    require(type(p["native_donor"]) is dict, "Anchored base donor required")
    donor = p["native_donor"]
    closed(
        donor,
        "source_commit apk_sha256 native_provenance_sha256 receipt_sha256 expected_receipt_sha256 recipe_sha256 scope",
    )
    require(
        donor["receipt_sha256"]
        == donor["expected_receipt_sha256"]
        == "481d8a7b7230bbad8229517d75f96d0232b5f89f27edf7f8980c853ff8844546",
        "Base donor anchor drift",
    )
    expected = {
        f"lib/{abi}/{name}"
        for abi in p["abis"]
        for name in (
            "libproot.so",
            "libproot_loader.so",
            "libruntime_probe.so",
            "libboot_identity_jni.so",
        )
    }
    require(set(p["native_files"]) == expected, "Native inventory drift")
    require(
        all(
            type(v) is str and re.fullmatch("[0-9a-f]{64}", v)
            for v in p["native_files"].values()
        ),
        "Native digest drift",
    )
    return p


def verify_package(apk, root, helper, commit, *, aapt2):
    # Reuse the canonical shared-byte and ELF consumers without expanding the
    # legacy integration-profile validator's ownership or accepted semantics.
    from verify_apk import elf_identity, parity_module

    identity = source_identity(root, commit)
    certificate = subprocess.check_output(
        [
            str(Path(aapt2).parent / "apksigner"),
            "verify",
            "--verbose",
            "--print-certs",
            str(apk),
        ],
        text=True,
    )
    signers = re.findall(
        r"^Signer #1 certificate SHA-256 digest: ([0-9a-f]{64})$", certificate, re.M
    )
    require(
        len(signers) == 1 and "Signer #2" not in certificate, "Diagnostic signer drift"
    )
    with (
        zipfile.ZipFile(apk) as z,
        tempfile.TemporaryDirectory(prefix="boot-readback-") as temp,
    ):
        names = z.namelist()
        require(len(names) == len(set(names)), "Duplicate APK members")
        p = check_provenance(
            loads(z.read("assets/runtime/native-provenance.json")), identity
        )
        require(
            {n for n in names if n.startswith("lib/") and not n.endswith("/")}
            == set(p["native_files"]),
            "Packaged native inventory drift",
        )
        committed_names = subprocess.check_output(
            [
                "git",
                "-C",
                str(root),
                "ls-tree",
                "-r",
                "--name-only",
                commit,
                "--",
                "platforms/android",
            ],
            text=True,
        ).splitlines()
        committed = {
            name: sha(
                subprocess.check_output(
                    ["git", "-C", str(root), "show", commit + ":" + name]
                )
            )
            for name in committed_names
        }
        require(
            p["adapter_sources"] == committed,
            "Complete committed adapter identity drift",
        )
        elf = {}
        for name, digest in p["native_files"].items():
            data = z.read(name)
            require(sha(data) == digest, "Packaged native byte drift")
            elf[name] = elf_identity(data, name.split("/")[1])
            if name.endswith("/libboot_identity_jni.so"):
                require(
                    identity["source_sha256"].encode() + b"\0" in data,
                    "JNI source identity drift",
                )
        extracted = Path(temp)
        for name in names:
            if name.startswith("assets/shared/") and not name.endswith("/"):
                target = extracted / name
                require(
                    target.resolve().is_relative_to(extracted), "Unsafe shared member"
                )
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(z.read(name))
        shared = parity_module(helper).check_apk_sources(
            root,
            extracted / "assets/shared",
            loads(z.read("assets/runtime/shared-source.json")),
            commit,
        )
    badging = subprocess.check_output(
        [str(aapt2), "dump", "badging", str(apk)], text=True
    )
    for token in (
        f"package: name='{PACKAGE}'",
        "minSdkVersion:'29'",
        "targetSdkVersion:'36'",
        "compileSdkVersion='36'",
        "launchable-activity: name='development.landmine.schemii.runtimeprobe.ProbeActivity'",
    ):
        require(token in badging, "Actual manifest identity drift")
    require(
        "application-debuggable" not in badging and "uses-permission:" not in badging,
        "Unsafe manifest flags/permissions",
    )
    tree = subprocess.check_output(
        [str(aapt2), "dump", "xmltree", str(apk), "--file", "AndroidManifest.xml"],
        text=True,
    )
    require(
        not re.search(r"E: (?:service|receiver|provider)\b", tree)
        and len(re.findall(r"E: activity\b", tree)) == 1,
        "Unexpected component",
    )
    return {
        "source": shared,
        "native_elf": elf,
        "profile": PROFILE,
        "signer_sha256": signers[0],
        "runtime_acceptance": "pending",
    }


def check_report(r, p, identity, *, run_id, provenance_sha256, signer_sha256, abi):
    check_provenance(p, identity)
    closed(
        r,
        "schema profile package run_id source_commit source_sha256 native_provenance_sha256 signer_sha256 packaged_abis abi device_api target_sdk min_sdk debuggable java_pid java_uid context native observation_complete runtime_gate stack_acceptance",
    )
    require(
        type(r["schema"]) is int
        and r["schema"] == 1
        and r["profile"] == PROFILE
        and r["package"] == PACKAGE,
        "Report profile drift",
    )
    require(
        bool(re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", run_id)) and r["run_id"] == run_id,
        "Run identity drift",
    )
    require(
        r["source_commit"] == identity["source_commit"]
        and r["source_sha256"] == identity["source_sha256"]
        and r["native_provenance_sha256"] == provenance_sha256,
        "Report source drift",
    )
    require(
        bool(re.fullmatch("[0-9a-f]{64}", signer_sha256))
        and r["signer_sha256"] == signer_sha256,
        "Signer drift",
    )
    require(
        r["packaged_abis"] == p["abis"] and r["abi"] == abi and abi in p["abis"],
        "Runtime ABI drift",
    )
    require(
        r["debuggable"] is False
        and r["stack_acceptance"] is False
        and r["observation_complete"] is True,
        "Observation state drift",
    )
    for k, v in (("device_api", 36), ("target_sdk", 36), ("min_sdk", 29)):
        require(type(r[k]) is int and r[k] == v, "Actual SDK drift")
    integer(r["java_pid"], 1, 2**31 - 1)
    integer(r["java_uid"], 10000, 2**31 - 1)
    closed(r["context"], "birth_ticks tracer_pid selinux_domain")
    for key, fact in r["context"].items():
        closed(fact, "status value")
        require(
            fact["status"] in ("observed", "AccessDenied", "unknown"),
            "Context status drift",
        )
        if fact["status"] != "observed":
            require(fact["value"] is None, "Unknown context has value")
        elif key == "selinux_domain":
            require(
                type(fact["value"]) is str
                and bool(re.fullmatch(r"u:r:[a-zA-Z0-9_:,.-]{1,180}", fact["value"])),
                "Domain drift",
            )
        else:
            integer(fact["value"], 0 if key == "tracer_pid" else 1, 2**63 - 1)
    n = r["native"]
    closed(
        n,
        "source_sha256 pid uid pages kernel knownness status stage method context untraced_owner_verified errno cleanup_errno failed_sample failed_node samples",
    )
    require(
        n["source_sha256"] == identity["source_sha256"]
        and n["pid"] == r["java_pid"]
        and n["uid"] == r["java_uid"],
        "Native JVM identity drift",
    )
    require(
        type(n["pid"]) is int
        and type(n["uid"]) is int
        and type(n["pages"]) is int
        and n["pages"] in (4096, 16384),
        "Native context drift",
    )
    require(
        type(n["kernel"]) is str
        and bool(re.fullmatch(r"[a-zA-Z0-9.+_-]{1,128}", n["kernel"])),
        "Kernel drift",
    )
    require(n["untraced_owner_verified"] is False, "Observation cannot grant authority")
    for key, high in (
        ("knownness", 1),
        ("status", 11),
        ("stage", 8),
        ("errno", 4095),
        ("cleanup_errno", 4095),
        ("failed_sample", 2),
        ("failed_node", 5),
    ):
        integer(n[key], 0, high)
    require(
        type(n["method"]) is int
        and n["method"] == 1
        and type(n["context"]) is int
        and n["context"] == 1,
        "Native method drift",
    )
    require(
        type(n["samples"]) is list and len(n["samples"]) == 2, "Sample inventory drift"
    )

    def verified_read(s):
        return s["origin"] == 1 and all(
            s[k] == 5
            for k in ("opened_nodes", "procfs_checks", "type_checks", "mount_checks")
        )

    def parsed_sample(s):
        # Each data call consumes at least one byte; a short-of-capacity
        # successful read must then make a distinct EOF call.
        return (
            verified_read(s)
            and s["bytes_read"] in (36, 37)
            and 2 <= s["read_calls"] <= s["bytes_read"] + 1
        )

    for s in n["samples"]:
        closed(
            s,
            "origin opened_nodes procfs_checks type_checks mount_checks read_calls bytes_read",
        )
        for key, high in (
            ("origin", 2),
            ("opened_nodes", 5),
            ("procfs_checks", 5),
            ("type_checks", 5),
            ("mount_checks", 5),
            ("read_calls", 38),
            ("bytes_read", 38),
        ):
            integer(s[key], 0, high)
        require(
            s["mount_checks"]
            <= s["type_checks"]
            <= s["procfs_checks"]
            <= s["opened_nodes"],
            "Sample counters drift",
        )
        require(
            s["opened_nodes"] - s["mount_checks"] <= 1,
            "More than one unverified path node",
        )
        require(
            (s["origin"] == 1) == (s["mount_checks"] == 5),
            "Origin/counter drift",
        )
        require(
            (not s["bytes_read"] or s["read_calls"] > 0)
            and (not s["read_calls"] or verified_read(s)),
            "Read before verified origin",
        )
    if n["status"] == 0:
        require(
            n["knownness"] == 1
            and n["stage"] == 0
            and n["errno"]
            == n["cleanup_errno"]
            == n["failed_sample"]
            == n["failed_node"]
            == 0,
            "Mislabelled known observation",
        )
        require(
            all(parsed_sample(s) for s in n["samples"]),
            "Unverified known origin",
        )
        gate = "caller-mount-observed-only"
    else:
        require(
            n["knownness"] == 0 and n["stage"] != 0 and n["failed_sample"] != 0,
            "Mislabelled uncertainty",
        )
        expected_stages = {
            1: {1},
            2: {2, 3, 4},
            3: {4},
            4: {2, 3, 4},
            5: {5},
            6: {6},
            7: {6},
            8: {6},
            9: {6},
            10: {7},
            11: {8},
        }
        require(n["stage"] in expected_stages[n["status"]], "Failure stage drift")
        syscall_failure = n["status"] in (1, 2, 5, 10)
        require((n["errno"] > 0) == syscall_failure, "Failure errno drift")
        require((n["failed_node"] == 0) == (n["status"] == 11), "Failure node drift")
        sample_index = n["failed_sample"] - 1
        if sample_index == 0:
            require(
                all(v == 0 for v in n["samples"][1].values()),
                "Second sample after first-sample refusal",
            )
        else:
            require(parsed_sample(n["samples"][0]), "Unfinished first sample")
        s = n["samples"][sample_index]
        status, node, stage = n["status"], n["failed_node"], n["stage"]
        require((s["origin"] == 2) == (status == 4), "Rejected origin/status drift")
        if status == 1:
            require(
                all(
                    s[k] == node - 1
                    for k in (
                        "opened_nodes",
                        "procfs_checks",
                        "type_checks",
                        "mount_checks",
                    )
                )
                and s["read_calls"] == s["bytes_read"] == 0,
                "Open failure counters",
            )
        elif status in (2, 3, 4):
            require(
                s["opened_nodes"] == node
                and s["procfs_checks"] == node - (stage == 2)
                and s["type_checks"] == node - (stage in (2, 3))
                and s["mount_checks"] == node - 1
                and s["read_calls"] == s["bytes_read"] == 0,
                "Origin failure counters",
            )
        elif status in (5, 6, 7, 8, 9):
            require(node == 5 and verified_read(s), "Read failure origin")
            size, calls = s["bytes_read"], s["read_calls"]
            if status == 5:
                feasible = size < 38 and 1 <= calls <= size + 1
            elif status == 6:
                feasible = size == 0 and calls == 1
            elif status == 7:
                feasible = 0 < size < 36 and 2 <= calls <= size + 1
            elif status == 8:
                feasible = size in (36, 37) and 2 <= calls <= size + 1
            else:
                feasible = size == 38 and 1 <= calls <= 38
            require(feasible, "Read/parse failure counters")
        elif status == 10:
            if node == 5:
                require(parsed_sample(s), "Leaf close before successful parse")
            else:
                require(
                    s["opened_nodes"] == node + 1
                    and all(
                        s[k] == node
                        for k in ("procfs_checks", "type_checks", "mount_checks")
                    )
                    and s["read_calls"] == s["bytes_read"] == 0,
                    "Parent close failure counters",
                )
        else:
            require(
                n["failed_sample"] == 2
                and n["cleanup_errno"] == 0
                and all(parsed_sample(v) for v in n["samples"]),
                "Comparison before two complete reads",
            )
        if (status == 1 and node == 1) or (status == 10 and node == 5):
            require(n["cleanup_errno"] == 0, "Cleanup error without a later close")
        gate = "NO-GO-native-unknown"
    require(r["runtime_gate"] == gate, "Runtime gate drift")
    return {
        "observation_complete": True,
        "runtime_gate": gate,
        "owner_authority": False,
        "recovery_acceptance": False,
    }


def check_diagnostics(bundle, p, identity, **expected):
    """Validate retained attempt evidence; an exported failure is never a read."""
    check_provenance(p, identity)
    closed(
        bundle,
        "schema kind run_id source_commit source_sha256 native_provenance_sha256 clock start terminal export_loss report delivery stack_acceptance",
    )
    require(
        type(bundle["schema"]) is int
        and bundle["schema"] == 1
        and bundle["kind"] == "boot-identity-diagnostics-v1"
        and bundle["clock"] == "java-nanoTime-process"
        and bundle["delivery"] == "unacknowledged"
        and bundle["stack_acceptance"] is False,
        "Diagnostic envelope drift",
    )
    require(
        bool(re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", expected["run_id"]))
        and bundle["run_id"] == expected["run_id"]
        and bundle["source_commit"] == identity["source_commit"]
        and bundle["source_sha256"] == identity["source_sha256"]
        and bundle["native_provenance_sha256"] == expected["provenance_sha256"],
        "Diagnostic identity drift",
    )
    valid_storage = {"file-synced", "readback-valid"}
    storage_states = valid_storage | {"write-failed", "absent", "invalid-or-unreadable"}
    phases = {
        "start",
        "context",
        "native-load",
        "native-call",
        "report-build",
        "report-persist",
        "terminal-persist",
        "export",
    }
    categories = {
        "none",
        "native-linkage",
        "io",
        "interrupted",
        "refused",
        "java-error",
    }
    events = {}
    for key, sequence in (("start", 1), ("terminal", 2), ("export_loss", 3)):
        record = bundle[key]
        closed(record, "storage event")
        require(
            type(record["storage"]) is str and record["storage"] in storage_states,
            "Diagnostic storage state",
        )
        event = record["event"]
        require(
            (event is not None)
            == (record["storage"] in valid_storage | {"write-failed"}),
            "Diagnostic event/storage drift",
        )
        if event is None:
            continue
        closed(
            event,
            "schema run_id source_commit source_sha256 native_provenance_sha256 sequence monotonic_ns phase outcome category",
        )
        require(
            type(event["schema"]) is int
            and event["schema"] == 1
            and type(event["sequence"]) is int
            and event["sequence"] == sequence,
            "Diagnostic sequence drift",
        )
        require(
            all(
                event[k] == bundle[k]
                for k in (
                    "run_id",
                    "source_commit",
                    "source_sha256",
                    "native_provenance_sha256",
                )
            ),
            "Diagnostic event identity drift",
        )
        integer(event["monotonic_ns"], -(2**63), 2**63 - 1)
        require(
            type(event["phase"]) is str
            and event["phase"] in phases
            and type(event["category"]) is str
            and event["category"] in categories,
            "Diagnostic category drift",
        )
        if key == "start":
            require(
                event["phase"] == "start"
                and event["outcome"] == "started"
                and event["category"] == "none",
                "Diagnostic start drift",
            )
        else:
            require(
                event["outcome"] in ("success", "failure")
                and ((event["category"] == "none") == (event["outcome"] == "success")),
                "Diagnostic outcome drift",
            )
            if event["outcome"] == "success":
                require(
                    key == "terminal" and event["phase"] == "terminal-persist",
                    "Diagnostic success phase",
                )
            if key == "export_loss":
                require(
                    event["outcome"] == "failure" and event["phase"] == "export",
                    "Diagnostic export loss drift",
                )
        events[key] = event
    times = [
        events[k]["monotonic_ns"]
        for k in ("start", "terminal", "export_loss")
        if k in events
    ]
    require(times == sorted(times), "Diagnostic monotonic order drift")
    payload = bundle["report"]
    closed(payload, "storage sha256 json")
    require(
        type(payload["storage"]) is str and payload["storage"] in storage_states,
        "Diagnostic report storage drift",
    )
    has_report = payload["json"] is not None
    require(
        has_report == (payload["storage"] in valid_storage | {"write-failed"}),
        "Diagnostic report availability drift",
    )
    measured = None
    if has_report:
        require(type(payload["json"]) is str, "Diagnostic report encoding")
        raw = payload["json"].encode("utf-8")
        require(
            0 < len(raw) <= 49152 and sha(raw) == payload["sha256"],
            "Diagnostic report digest drift",
        )
        measured = check_report(loads(raw), p, identity, **expected)
    else:
        require(payload["sha256"] is None, "Digest without retained report")
    terminal = events.get("terminal")
    if terminal is not None:
        require(
            terminal["phase"] != "export", "Transport failure replaced attempt terminal"
        )
        if terminal["outcome"] == "failure" and has_report:
            require(
                terminal["phase"] in ("report-persist", "terminal-persist"),
                "Report before failed observation",
            )
    succeeded = terminal is not None and terminal["outcome"] == "success"
    complete = (
        all(bundle[k]["storage"] in valid_storage for k in ("start", "terminal"))
        and bundle["export_loss"]["storage"] in valid_storage | {"absent"}
        and payload["storage"] in valid_storage | {"absent"}
    )
    report_required = succeeded or (
        terminal is not None
        and terminal["phase"] in ("report-persist", "terminal-persist")
    )
    if report_required:
        complete = complete and has_report
    observation = complete and succeeded and measured is not None
    return {
        "diagnostic_export_valid": True,
        "diagnostic_complete": complete,
        "observation_complete": observation,
        "runtime_gate": measured["runtime_gate"]
        if observation and measured is not None
        else "NO-GO-attempt-failure-or-evidence-loss",
        "prior_export_failure": bundle["export_loss"]["storage"] != "absent",
        "evidence_loss": not complete,
        "elapsed_ns": events["terminal"]["monotonic_ns"]
        - events["start"]["monotonic_ns"]
        if "start" in events and "terminal" in events
        else None,
        "owner_authority": False,
        "recovery_acceptance": False,
    }


def check_refusal(bundle, p, identity, **expected):
    """Inspect a redacted preflight/request refusal, never an observed boot read.

    Before admission, source fields are caller declarations. Even an exact match
    provides correlation only. The first refusal survives only in the current
    JVM; complete durable attempt evidence is explicitly absent.
    """
    check_provenance(p, identity)
    closed(
        bundle,
        "schema kind run_id source_commit source_sha256 native_provenance_sha256 identity_trust sequence clock monotonic_ns phase category outcome retention retained_first prior_export_failure delivery observation_complete stack_acceptance",
    )
    require(
        type(bundle["schema"]) is int
        and bundle["schema"] == 1
        and type(bundle["sequence"]) is int
        and bundle["sequence"] == 1
        and bundle["kind"] == "boot-identity-refusal-v1"
        and bundle["clock"] == "java-nanoTime-process"
        and bundle["outcome"] == "refused"
        and bundle["retention"] == "volatile-only"
        and bundle["delivery"] == "unacknowledged"
        and bundle["observation_complete"] is False
        and bundle["stack_acceptance"] is False,
        "Refusal envelope drift",
    )
    integer(bundle["monotonic_ns"], -(2**63), 2**63 - 1)
    for key in ("retained_first", "prior_export_failure"):
        require(type(bundle[key]) is bool, "Refusal flag type drift")
    require(
        type(bundle["run_id"]) is str
        and re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", bundle["run_id"]) is not None
        and bundle["run_id"] == expected["run_id"],
        "Refusal correlation drift",
    )
    for key, size in (
        ("source_commit", 40),
        ("source_sha256", 64),
        ("native_provenance_sha256", 64),
    ):
        value = bundle[key]
        require(
            value is None
            or (
                type(value) is str
                and re.fullmatch(r"[0-9a-f]{" + str(size) + "}", value) is not None
            ),
            "Refusal identity token drift",
        )
    phase, category, trust = (
        bundle["phase"],
        bundle["category"],
        bundle["identity_trust"],
    )
    require(
        type(phase) is str
        and phase
        in {
            "request",
            "metadata",
            "profile",
            "source",
            "source-digest",
            "request-identity",
            "libraries",
            "claim",
            "retained-export",
        }
        and type(category) is str
        and category in {"native-linkage", "io", "interrupted", "refused", "java-error"}
        and type(trust) is str
        and trust in {"unbound", "declared-only", "admitted"},
        "Refusal classification drift",
    )
    ids = ("source_commit", "source_sha256", "native_provenance_sha256")
    if trust != "unbound":
        require(
            all(bundle[k] is not None for k in ids), "Bound refusal missing identity"
        )
    else:
        require(
            bundle["run_id"] == "unbound" or any(bundle[k] is None for k in ids),
            "Unbound refusal with complete declaration",
        )
    matches = (
        bundle["source_commit"] == identity["source_commit"]
        and bundle["source_sha256"] == identity["source_sha256"]
        and bundle["native_provenance_sha256"] == expected["provenance_sha256"]
    )
    require(
        (trust == "admitted") == (phase in {"claim", "retained-export"}),
        "Refusal admission phase drift",
    )
    if trust == "admitted":
        require(matches, "Admitted refusal identity drift")
    return {
        "diagnostic_export_valid": True,
        "diagnostic_complete": False,
        "evidence_loss": True,
        "identity_admitted": trust == "admitted",
        "declared_identity_matches_expected": matches,
        "volatile_first_refusal_retained": bundle["retained_first"],
        "prior_export_failure": bundle["prior_export_failure"],
        "observation_complete": False,
        "runtime_gate": "NO-GO-request-refused",
        "owner_authority": False,
        "recovery_acceptance": False,
    }


def decode_export(lines, run_id):
    chunks, expected = {}, None
    for line in lines:
        # Caller supplies only this package PID/tag's message bodies.
        match = re.fullmatch(
            r"([A-Za-z0-9_-]+):(\d+)/(\d+):(\d+):([0-9a-f]{64}):([A-Za-z0-9+/=]+)",
            line.strip(),
        )
        if match is None:
            raise ValueError("Export frame drift")
        require(match[1] == run_id, "Export run drift")
        index, count, size = map(int, (match[2], match[3], match[4]))
        require(
            0 <= index < count <= 64 and 0 < size <= 65536 and index not in chunks,
            "Export chunk inventory drift",
        )
        cohort = (count, size, match[5])
        require(expected is None or cohort == expected, "Mixed export cohort")
        expected = cohort
        chunks[index] = base64.b64decode(match[6], validate=True)
    if expected is None:
        raise ValueError("Empty export")
    require(set(chunks) == set(range(expected[0])), "Truncated export")
    raw = b"".join(chunks[i] for i in range(expected[0]))
    require(
        len(raw) == expected[1] and sha(raw) == expected[2],
        "Export bytecount/digest drift",
    )
    return raw


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("report", "provenance", "root"):
        parser.add_argument("--" + name, type=Path, required=True)
    for name in ("commit", "run-id", "signer-sha256", "abi"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    raw = args.provenance.read_bytes()
    report = loads(args.report.read_bytes())
    validators = {
        "boot-identity-diagnostics-v1": check_diagnostics,
        "boot-identity-refusal-v1": check_refusal,
    }
    validator = (
        validators.get(report.get("kind"), check_report)
        if type(report) is dict
        else check_report
    )
    result = validator(
        report,
        loads(raw),
        source_identity(args.root, args.commit),
        run_id=args.run_id,
        provenance_sha256=sha(raw),
        signer_sha256=args.signer_sha256,
        abi=args.abi,
    )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
