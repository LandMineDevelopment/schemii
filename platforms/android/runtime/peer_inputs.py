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
    record = json.loads(handoff.read_text())
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
    for abi in abis:
        item = record["elf"][abi]
        library = Path(item["library"])
        receipt = json.loads(Path(item["build_receipt"]).read_text())
        if (
            digest(library) != item["sha256"]
            or receipt["library_sha256"] != item["sha256"]
            or receipt["source_head"] != record["source_head"]
            or receipt["exit"] != 0
            or not receipt["source_unchanged"]
            or receipt["dirty_inputs"]
        ):
            raise ValueError("Network artifact/source receipt mismatch")
        for name, sha in receipt["source_files"].items():
            if hashes.get(name) != sha:
                raise ValueError("Network build input outside verified handoff")
        target = out / "lib" / abi / "libschemii_network.so"
        shutil.copyfile(library, target)
        libraries[str(target.relative_to(out))] = item["sha256"]
    return (
        {
            "source_head": record["source_head"],
            "source_files": hashes,
            "handoff_sha256": digest(handoff),
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
