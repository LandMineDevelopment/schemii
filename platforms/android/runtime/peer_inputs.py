"""Read exact peer Git inputs without changing another worktree or our branch."""

import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def committed(checkout, head, names):
    if not re.fullmatch(r"[0-9a-f]{40}", head):
        raise ValueError("Full immutable peer head required")
    result = {}
    for name in names:
        path = Path(name)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("Unsafe peer source name")
        result[name] = subprocess.check_output(
            ["git", "-C", str(checkout), "show", f"{head}:{name}"]
        )
    return result


def network(handoff, checkout, out, abis, contract):
    handoff_bytes = handoff.read_bytes()
    record = json.loads(handoff_bytes)
    sources = committed(checkout, record["source_head"], record["source_files"])
    hashes = {name: hashlib.sha256(data).hexdigest() for name, data in sources.items()}
    if hashes != record["source_files"]:
        raise ValueError("Network handoff/Git input drift")
    for name in [
        "dev/android/tailnet-proxy/go.mod",
        "dev/android/tailnet-proxy/go.sum",
    ]:
        if hashes[name] != contract["dependency_inputs"][name]:
            raise ValueError("Network canonical Go dependency input drift")
    java = []
    for name, data in sources.items():
        if name.startswith("platforms/android/app/src/main/java/") and name.endswith(
            ".java"
        ):
            target = out / "peer-sources/network" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            java.append(target)
    libraries = {}
    receipts = {}
    bridge = hashlib.sha256(
        sources["platforms/android/native/network/bridge.c"]
        + sources["platforms/android/native/network/bridge.h"]
    ).hexdigest()
    for abi in abis:
        item = record["elf"][abi]
        library = Path(item["library"])
        receipt_path = Path(item["build_receipt"])
        receipt_bytes = receipt_path.read_bytes()
        receipt_sha = hashlib.sha256(receipt_bytes).hexdigest()
        if receipt_sha != item["build_receipt_sha256"]:
            raise ValueError("Network build receipt byte drift")
        receipt = json.loads(receipt_bytes)
        if (
            digest(library) != item["sha256"]
            or receipt["library_sha256"] != item["sha256"]
            or receipt["source_head"] != record["source_head"]
            or receipt["exit"] != 0
            or not receipt["source_unchanged"]
            or receipt["dirty_inputs"]
            or receipt["abi"] != abi
        ):
            raise ValueError("Network artifact/source receipt mismatch")
        if item["bridge_sha256"] != bridge or receipt["bridge_sha256"] != bridge:
            raise ValueError("Network compiled bridge/source identity mismatch")
        for name, sha in receipt["source_files"].items():
            if hashes.get(name) != sha:
                raise ValueError("Network build input outside verified handoff")
        target = out / "lib" / abi / "libschemii_network.so"
        shutil.copyfile(library, target)
        libraries[str(target.relative_to(out))] = item["sha256"]
        receipts[abi] = receipt_sha
    return (
        {
            "source_head": record["source_head"],
            "source_files": hashes,
            "handoff_sha256": hashlib.sha256(handoff_bytes).hexdigest(),
            "bridge_sha256": bridge,
            "build_receipts_sha256": receipts,
            "runtime_acceptance": "pending",
        },
        java,
        libraries,
    )


def process(checkout, head, out, abis, compiler_directory):
    prefix = "platforms/android/native/process/"
    names = [
        prefix + name
        for name in ["proc_discovery_control.c", "proc_observer.c", "proc_observer.h"]
    ]
    sources = committed(checkout, head, names)
    hashes = {name: hashlib.sha256(data).hexdigest() for name, data in sources.items()}
    match = re.search(
        rb"cases\[\]\s*=\s*\{(.*?)\};",
        sources[prefix + "proc_discovery_control.c"],
        re.S,
    )
    if not match:
        raise ValueError("Process case inventory missing")
    cases = re.findall(r'"([a-z-]+)"', match.group(1).decode())
    if not cases or len(set(cases)) != len(cases):
        raise ValueError("Invalid process case inventory")
    source_id = hashlib.sha256(
        b"".join(
            Path(name).name.encode() + b"\0" + sources[name] for name in sorted(sources)
        )
    ).hexdigest()
    source = out / "peer-sources/process"
    source.mkdir(parents=True)
    for name, data in sources.items():
        (source / Path(name).name).write_bytes(data)
    libraries = {}
    for abi in abis:
        triple = {"arm64-v8a": "aarch64", "x86_64": "x86_64"}[abi]
        compiler = compiler_directory / (triple + "-linux-android29-clang")
        library = out / "lib" / abi / "libschemii_proc_discovery.so"
        subprocess.run(
            [
                str(compiler),
                "-std=c11",
                "-O2",
                "-g",
                "-Wall",
                "-Wextra",
                "-Werror",
                "-pthread",
                "-fPIE",
                "-pie",
                "-Wl,-z,max-page-size=16384",
                "-Wl,-z,noexecstack",
                '-DPROCESS_PROBE_SOURCE_ID="' + source_id + '"',
                str(source / "proc_observer.c"),
                str(source / "proc_discovery_control.c"),
                "-o",
                str(library),
            ],
            check=True,
        )
        libraries[str(library.relative_to(out))] = digest(library)
    return {
        "source_head": head,
        "source_files": hashes,
        "source_id": source_id,
        "min_api": 29,
        "cases": cases,
        "runtime_acceptance": "pending",
    }, libraries


# This bounded diagnostic profile admits exactly the coordinator-reviewed inputs.
# These anchors are selected outside the supplied handoffs/receipts.
INTEGRATION_PROFILE = "subreaper13e-networkcde-v1"
SUBREAPER_HEAD = "13e04d8fd9c65d206967cfc50b60f4571bba5f08"
SUBREAPER_HANDOFF = "14393f72047d90c1bcf868ff9d21eead17f20921d970bddf9ed663a2cb3a4bae"
NETWORK_HEAD = "cde504d128d2a1ba8e3f50b02028db7d93395529"
NETWORK_HANDOFF = "62d3747254b1c481045957c4e8fe4c0c8ca53493a6a216872d91eecaf881af34"
NETWORK_INVENTORY = "526e96c5f3b851eae9e04890dcf045d666387177b7fe6a9b2e029bc56e162e04"
NETWORK_LIBRARIES = {
    "arm64-v8a": "656414cd2b96e339d91aa40af50647e2a222f29bcb0ef0d3aa03b4a8ac134603",
    "x86_64": "cd62d92dc03a3869c44b0bfa9a75331ba65d296de8f7abfa2e439aa04580f5af",
}
SUBREAPER_SOURCES = {
    "platforms/android/native/process/subreaper_probe.c": "a5465265d4e54397d7f29a98ab5718025f6fe42330314e23f74db18358b2586d",
    "platforms/android/native/process/proc_observer.c": "cb1b874d9946c37c6832ca3385a1b10da1220571f1f7041ab8cbd4313eca56f0",
    "platforms/android/native/process/proc_observer.h": "ab02461b07ad862b5397b527cdbf8abbae91a53ce4a883a91716bbf94928ede7",
}
SUBREAPER_CASES = [
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
]


def anchored_handoff(path, expected):
    raw = Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError("Peer handoff does not match independently selected digest")
    return json.loads(raw)


def inventory_sha256(files):
    return hashlib.sha256(
        json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def subreaper(handoff, checkout, out, abis, compiler_directory):
    record = anchored_handoff(handoff, SUBREAPER_HANDOFF)
    if (
        record["commit"] != SUBREAPER_HEAD
        or record["cases"] != SUBREAPER_CASES
        or record["collector_deadline_ms"] != 45000
        or record["report_limit_bytes"] != 65536
    ):
        raise ValueError("Subreaper handoff profile drift")
    sources = committed(checkout, SUBREAPER_HEAD, SUBREAPER_SOURCES)
    hashes = {name: hashlib.sha256(data).hexdigest() for name, data in sources.items()}
    if hashes != SUBREAPER_SOURCES:
        raise ValueError("Subreaper committed source drift")
    source = out / "peer-sources/subreaper"
    source.mkdir(parents=True)
    for name, data in sources.items():
        (source / Path(name).name).write_bytes(data)
    libraries, builds = {}, {}
    for abi in abis:
        compiler = compiler_directory / (
            {"arm64-v8a": "aarch64", "x86_64": "x86_64"}[abi] + "-linux-android29-clang"
        )
        library = out / "lib" / abi / "libsubreaper_probe.so"
        library.parent.mkdir(parents=True, exist_ok=True)
        argv = [
            str(compiler),
            *record["compile"]["flags"],
            str(source / "proc_observer.c"),
            str(source / "subreaper_probe.c"),
            "-o",
            str(library),
        ]
        subprocess.run(argv, check=True)
        sha = digest(library)
        libraries[str(library.relative_to(out))] = sha
        builds[abi] = {
            "argv": argv,
            "compiler_sha256": digest(compiler.resolve()),
            "clang_binary_sha256": digest(compiler_directory / "clang"),
            "compiler_version": subprocess.check_output(
                [str(compiler), "--version"], text=True
            ),
            "library_sha256": sha,
            "native_api": 29,
        }
    return {
        "source_head": SUBREAPER_HEAD,
        "source_id": SUBREAPER_HEAD,
        "source_files": hashes,
        "handoff_sha256": SUBREAPER_HANDOFF,
        "cases": SUBREAPER_CASES,
        "min_api": 29,
        "builds": builds,
        "runtime_acceptance": "pending",
    }, libraries


def integration_components(native):
    """Fail closed on a stale/mixed diagnostic profile, independent of its claims."""
    if (
        native.get("diagnostic_profile") != INTEGRATION_PROFILE
        or native.get("wrapper_controls_only")
        or native.get("guest_payloads")
        or set(native["peer_components"]) != {"network", "subreaper"}
    ):
        raise ValueError("Integration diagnostic profile/inventory mismatch")
    components = native["peer_components"]
    s, n = components["subreaper"], components["network"]
    if (
        s["source_head"] != SUBREAPER_HEAD
        or s["source_id"] != SUBREAPER_HEAD
        or s["handoff_sha256"] != SUBREAPER_HANDOFF
        or s["source_files"] != SUBREAPER_SOURCES
        or s["cases"] != SUBREAPER_CASES
        or s["min_api"] != 29
    ):
        raise ValueError("Subreaper component identity drift")
    if (
        n["source_head"] != NETWORK_HEAD
        or n["handoff_sha256"] != NETWORK_HANDOFF
        or inventory_sha256(n["source_files"]) != NETWORK_INVENTORY
    ):
        raise ValueError("Network component identity drift")
    for abi in native["abis"]:
        if (
            native["native_files"]["lib/" + abi + "/libschemii_network.so"]
            != NETWORK_LIBRARIES[abi]
        ):
            raise ValueError("Network admitted library identity drift")
        if (
            s["builds"][abi]["native_api"] != 29
            or s["builds"][abi]["library_sha256"]
            != native["native_files"]["lib/" + abi + "/libsubreaper_probe.so"]
        ):
            raise ValueError("Subreaper build/library identity drift")
    return components
