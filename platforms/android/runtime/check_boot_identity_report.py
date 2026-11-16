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
    if n["status"] == 0:
        require(
            n["knownness"] == 1
            and n["errno"]
            == n["cleanup_errno"]
            == n["failed_sample"]
            == n["failed_node"]
            == 0,
            "Mislabelled known observation",
        )
        require(
            all(
                s["origin"] == 1
                and all(
                    s[k] == 5
                    for k in (
                        "opened_nodes",
                        "procfs_checks",
                        "type_checks",
                        "mount_checks",
                    )
                )
                and s["bytes_read"] in (36, 37)
                for s in n["samples"]
            ),
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
        gate = "NO-GO-native-unknown"
    require(r["runtime_gate"] == gate, "Runtime gate drift")
    return {
        "observation_complete": True,
        "runtime_gate": gate,
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
    result = check_report(
        loads(args.report.read_bytes()),
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
