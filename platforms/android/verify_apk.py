#!/usr/bin/env python3
"""Check the shared/native bytes of a real APK against an independent source head."""

from __future__ import annotations
import argparse
import importlib.util
import json
from pathlib import Path
import struct
import re
import subprocess
import tempfile
import zipfile
import hashlib

MACHINES = {"arm64-v8a": 183, "x86_64": 62}


def elf_identity(data: bytes, abi: str) -> dict:
    if data[:6] != b"\x7fELF\x02\x01" or len(data) < 64:
        raise ValueError("Expected little-endian ELF64")
    kind, machine = struct.unpack_from("<HH", data, 16)
    if kind not in (2, 3) or machine != MACHINES[abi]:
        raise ValueError("Native ABI/type mismatch")
    offset = struct.unpack_from("<Q", data, 32)[0]
    size, count = struct.unpack_from("<HH", data, 54)
    if size < 56 or count == 0 or offset + size * count > len(data):
        raise ValueError("Malformed ELF program headers")
    loads = []
    for i in range(count):
        values = struct.unpack_from("<IIQQQQQQ", data, offset + i * size)
        if values[0] != 1:
            continue
        _, flags, file_offset, virtual, _, file_size, memory_size, align = values
        if align < 16384 or file_offset % 16384 != virtual % 16384:
            raise ValueError("ELF lacks 16KiB-compatible LOAD alignment")
        if flags & 3 == 3:
            raise ValueError("Writable/executable ELF LOAD segment")
        if file_offset + file_size > len(data) or file_size > memory_size:
            raise ValueError("Malformed ELF LOAD segment")
        loads.append(align)
    if not loads:
        raise ValueError("ELF has no LOAD segments")
    return {"machine": machine, "type": kind, "load_alignment": min(loads)}


def parity_module(helper):
    spec = importlib.util.spec_from_file_location("apk_parity", helper)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def integration_manifest(apk, aapt2, debuggable):
    if aapt2 is None:
        raise ValueError(
            "Pinned aapt2 required for actual integration manifest readback"
        )
    badging = subprocess.check_output(
        [str(aapt2), "dump", "badging", str(apk)], text=True
    )
    if (
        "package: name='development.landmine.schemii.runtimeprobe'" not in badging
        or "sdkVersion:'29'" not in badging
        or "targetSdkVersion:'36'" not in badging
        or ("application-debuggable" in badging) != debuggable
    ):
        raise ValueError("Actual integration APK manifest SDK/debug/package drift")
    permissions = set(re.findall(r"^uses-permission: name='([^']+)'", badging, re.M))
    if permissions != {
        "android.permission.INTERNET",
        "android.permission.ACCESS_NETWORK_STATE",
    }:
        raise ValueError("Integration manifest permission drift")
    tree = subprocess.check_output(
        [str(aapt2), "dump", "xmltree", str(apk), "--file", "AndroidManifest.xml"],
        text=True,
    )
    if re.search(r"E: (?:service|receiver|provider)\b", tree):
        raise ValueError("Diagnostic manifest contains production component")
    return {
        "package": "development.landmine.schemii.runtimeprobe",
        "min_sdk": 29,
        "target_sdk": 36,
        "debuggable": debuggable,
        "permissions": sorted(permissions),
    }


def verify_package(
    apk: Path, root: Path, helper: Path, expected_commit: str, *, aapt2=None
) -> dict:
    module = parity_module(helper)
    with tempfile.TemporaryDirectory(prefix="apk-shared-") as temp:
        extracted = Path(temp)
        with zipfile.ZipFile(apk) as package:
            names = package.namelist()
            if len(set(names)) != len(names):
                raise ValueError("Duplicate APK entries")
            for name in names:
                if name.startswith("assets/shared/") and not name.endswith("/"):
                    target = extracted / name
                    if not target.resolve().is_relative_to(extracted):
                        raise ValueError("Unsafe APK shared path")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(package.read(name))
            record = json.loads(package.read("assets/runtime/shared-source.json"))
            source = module.check_apk_sources(
                root, extracted / "assets/shared", record, expected_commit
            )
            native = json.loads(package.read("assets/runtime/native-provenance.json"))
            if (
                native["source_commit"] != expected_commit
                or native["target_sdk"] != 36
                or native["compile_sdk"] != 36
                or native["min_sdk"] != 29
            ):
                raise ValueError("Native build identity/SDK drift")
            expected_native = {
                f"lib/{abi}/{name}"
                for abi in native["abis"]
                for name in ["libproot.so", "libproot_loader.so", "libruntime_probe.so"]
            }
            if native.get("wrapper_controls_only"):
                if (
                    native.get("guest_payloads")
                    or native.get("peer_components")
                    or not native.get("wrapper_fixture")
                ):
                    raise ValueError(
                        "Wrapper-only package contains unrelated runtime components"
                    )
                expected_native.update(
                    "lib/" + abi + "/libcollector_fixture.so" for abi in native["abis"]
                )
            components = native.get("peer_components", {})
            manifest = None
            if native.get("diagnostic_profile"):
                from runtime.peer_inputs import integration_components

                integration_components(native)
                manifest = integration_manifest(apk, aapt2, native["debuggable"])
            if set(components) - {"network", "process", "subreaper"}:
                raise ValueError("Unknown native peer component")
            for component, filename in [
                ("network", "libschemii_network.so"),
                ("process", "libschemii_proc_discovery.so"),
                ("subreaper", "libsubreaper_probe.so"),
            ]:
                if component in components:
                    expected_native.update(
                        "lib/" + abi + "/" + filename for abi in native["abis"]
                    )
            actual_native = {
                n for n in names if n.startswith("lib/") and not n.endswith("/")
            }
            if (
                set(native["native_files"]) != expected_native
                or actual_native != expected_native
            ):
                raise ValueError("Native file inventory drift")
            identities = {}
            for name, sha in native["native_files"].items():
                data = package.read(name)
                if hashlib.sha256(data).hexdigest() != sha:
                    raise ValueError("Native byte drift")
                identities[name] = elf_identity(data, name.split("/")[1])
                if native.get("diagnostic_profile") and name.endswith(
                    "/libsubreaper_probe.so"
                ):
                    from runtime.peer_inputs import SUBREAPER_HEAD

                    if SUBREAPER_HEAD.encode() + b"\0" not in data:
                        raise ValueError("Packaged subreaper source ID drift")
            guest_payloads = {}
            for abi, identity in native.get("guest_payloads", {}).items():
                if abi not in native["abis"]:
                    raise ValueError("Guest ABI drift")
                manifest_bytes = package.read(
                    "assets/runtime/guest-" + abi + "-manifest.json"
                )
                if (
                    hashlib.sha256(manifest_bytes).hexdigest()
                    != identity["manifest_sha256"]
                ):
                    raise ValueError("Guest manifest drift")
                manifest = json.loads(manifest_bytes)
                if (
                    manifest["abi"] != abi
                    or manifest["archive_sha256"] != identity["archive_sha256"]
                ):
                    raise ValueError("Guest manifest identity drift")
                with package.open("assets/runtime/guest-" + abi + ".zip") as stream:
                    if (
                        hashlib.file_digest(stream, "sha256").hexdigest()
                        != identity["archive_sha256"]
                    ):
                        raise ValueError("Guest archive byte drift")
                if manifest["contract_sha256"] != source["contract_sha256"]:
                    raise ValueError("Guest/shared contract mismatch")
                guest_payloads[abi] = {
                    "archive_sha256": identity["archive_sha256"],
                    "runtime_acceptance": "pending",
                }
            if guest_payloads:
                if (
                    hashlib.sha256(
                        package.read("assets/runtime/runtime-interface.json")
                    ).hexdigest()
                    != native["runtime_interface_sha256"]
                ):
                    raise ValueError("Runtime endpoint/interface byte drift")
                if (
                    hashlib.sha256(
                        package.read("assets/runtime/guest_probe.py")
                    ).hexdigest()
                    != native["guest_probe_sha256"]
                ):
                    raise ValueError("Guest probe byte drift")
        return {
            "source": source,
            "native_elf": identities,
            "guest_payloads": guest_payloads,
            "manifest": manifest,
            "apk_runtime_acceptance": "pending",
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("apk", type=Path)
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[2]
    )
    parser.add_argument("--parity-helper", type=Path)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--aapt2", type=Path)
    args = parser.parse_args()
    print(
        json.dumps(
            verify_package(
                args.apk,
                args.root,
                args.parity_helper or args.root / "scripts/check_runtime_parity.py",
                args.expected_commit,
                aapt2=args.aapt2,
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
