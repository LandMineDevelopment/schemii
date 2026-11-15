#!/usr/bin/env python3
"""Build only the shared network library from exact Go/JNI inputs; never fetch SDKs."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[4]
MODULE = ROOT / "dev/android/tailnet-proxy"
NATIVE = Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(args):
    go = args.go.resolve(strict=True)
    ndk = args.ndk.resolve(strict=True)
    if (
        subprocess.check_output([go, "version"], text=True).strip()
        != "go version go1.27.4 linux/amd64"
    ):
        raise ValueError("Expected retained Linux/amd64 Go1.27.4 build SDK")
    properties = (ndk / "source.properties").read_text()
    if "Pkg.Revision = 30.0.16248370" not in properties.splitlines():
        raise ValueError("Expected runtime264 pinned NDK30.0.16248370")
    paths = sorted(
        p
        for base in [MODULE, NATIVE]
        for p in base.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    )
    source = {str(p.relative_to(ROOT)): sha(p) for p in paths}
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()
    dirty = []
    for path, digest in source.items():
        original = subprocess.run(
            ["git", "show", f"{head}:{path}"], cwd=ROOT, capture_output=True
        )
        if original.returncode or hashlib.sha256(original.stdout).hexdigest() != digest:
            dirty.append(path)
    if dirty and not args.allow_dirty:
        raise ValueError(
            "Uncommitted network inputs; --allow-dirty is development evidence only"
        )
    out = args.output_dir.resolve()
    out.mkdir(
        parents=True, exist_ok=False
    )  # Never overwrite an earlier failed/successful build.
    triple = {"arm64-v8a": ("arm64", "aarch64"), "x86_64": ("amd64", "x86_64")}[
        args.abi
    ]
    compiler = (
        ndk
        / f"toolchains/llvm/prebuilt/linux-x86_64/bin/{triple[1]}-linux-android29-clang"
    )
    # cgo does not track included files outside its module directory in cache
    # keys. Put the actual external bridge digest in CFLAGS and runtime evidence.
    bridge_digest = hashlib.sha256(
        (NATIVE / "bridge.c").read_bytes() + (NATIVE / "bridge.h").read_bytes()
    ).hexdigest()
    environment = os.environ.copy()
    environment.update(
        GOOS="android",
        GOARCH=triple[0],
        CGO_ENABLED="1",
        GOTOOLCHAIN="local",
        GOPROXY="off",
        CC=str(compiler),
        GOMAXPROCS="3",
        CGO_CFLAGS=f"-DSCHEMII_NETWORK_BRIDGE_SHA256={bridge_digest}",
        CGO_LDFLAGS="-Wl,-z,max-page-size=16384 -Wl,-soname,libschemii_network.so",
        GOCACHE=str(args.go_cache.resolve() if args.go_cache else out / "go-cache"),
    )
    library = out / "libschemii_network.so"
    command = [
        str(go),
        "build",
        "-mod=readonly",
        "-p=3",
        "-buildmode=c-shared",
        "-buildvcs=false",
        "-trimpath",
        "-ldflags=-buildid=",
        "-o",
        str(library),
        ".",
    ]
    with (out / "build.log").open("x") as log:
        result = subprocess.run(
            command, cwd=MODULE, env=environment, stdout=log, stderr=subprocess.STDOUT
        )
    record = {
        "schema": 1,
        "source_head": head,
        "source_files": source,
        "dirty_inputs": dirty,
        "abi": args.abi,
        "min_sdk": 29,
        "ndk": "30.0.16248370",
        "go": "1.27.4",
        "go_compiler_sha256": sha(go),
        "ndk_compiler_sha256": sha(compiler),
        "bridge_sha256": bridge_digest,
        "command": command,
        "cflags": environment["CGO_CFLAGS"],
        "ldflags": environment["CGO_LDFLAGS"],
        "source_unchanged": source == {str(p.relative_to(ROOT)): sha(p) for p in paths},
        "exit": result.returncode,
        "stack_acceptance": False,
    }
    if result.returncode == 0:
        record["library_sha256"] = sha(library)
        record["go_build_metadata"] = subprocess.check_output(
            [go, "version", "-m", library], text=True
        )
    (out / "network-build.json").write_text(json.dumps(record, indent=2) + "\n")
    if result.returncode or not record["source_unchanged"]:
        raise RuntimeError(f"Network build failed or inputs changed; inspect {out}")
    print(
        json.dumps(
            {
                "library": str(library),
                "sha256": record["library_sha256"],
                "receipt": str(out / "network-build.json"),
                "stack_acceptance": False,
            }
        )
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--go", type=Path, required=True)
    parser.add_argument("--ndk", type=Path, required=True)
    parser.add_argument("--abi", choices=["arm64-v8a", "x86_64"], required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--go-cache", type=Path)
    parser.add_argument("--allow-dirty", action="store_true")
    build(parser.parse_args())


if __name__ == "__main__":
    main()
