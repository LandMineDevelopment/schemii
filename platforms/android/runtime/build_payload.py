#!/usr/bin/env python3
"""Resolve explicitly, then assemble pinned offline Debian/Python/Node APK guest data.

Does not run Schemii or PostgreSQL on the host. No system package installation.
The owned cache's build-only apt sandbox is used solely by the explicit resolve step.
"""

from __future__ import annotations
import argparse
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import fcntl
import subprocess
import tarfile
import urllib.parse
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
BASES = {
    "arm64-v8a": {
        "arch": "arm64",
        "manifest": "24a01011d10eba3a023bc8585b9650b8b29021240d4217c05b905e09ef4a5232",
        "layer": "acd43e9bebc37bc4ba7a5f47bd7598913337803e53d468f37d940f11f5d5f1ef",
        "reason": "canonical Android runtime-contract image",
    },
    "x86_64": {
        "arch": "amd64",
        "manifest": "7378260c0a098d27ac206225db5e7d92b8c7c84ea7afccc86eeb513756bbd392",
        "layer": "37fb86c2a767b88d500c6872195f88078f64765fe0d2f9cc375cc2efbd4c0a41",
        "reason": "same Debian image build, diagnostic emulator architecture only",
    },
}


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def fetch(url, expected, path):
    if path.exists():
        if sha(path) != expected:
            raise ValueError("Cached payload checksum drift: " + str(path))
        return path
    temporary = path.with_suffix(path.suffix + ".partial")
    with urllib.request.urlopen(url, timeout=120) as src, temporary.open("xb") as dst:
        shutil.copyfileobj(src, dst)
    if sha(temporary) != expected:
        raise ValueError("Payload download checksum drift: " + url)
    temporary.rename(path)
    return path


def apt_command(cache, *args):
    root = cache / "guest-build/x86_64"
    env = os.environ.copy()
    env.update(
        PROOT_LOADER=str(cache / "host-proot-lld/src/loader/loader"),
        PROOT_NO_SECCOMP="1",
    )
    return subprocess.check_output(
        [
            str(cache / "host-proot-lld/src/proot"),
            "-0",
            "-r",
            str(root),
            "-b",
            "/dev",
            "-b",
            "/proc",
            "-w",
            "/",
            *args,
        ],
        env=env,
        text=True,
    )


def paragraphs(text):
    for paragraph in text.split("\n\n"):
        fields = {}
        for line in paragraph.splitlines():
            if line and not line.startswith(" ") and ": " in line:
                key, value = line.split(": ", 1)
                fields[key] = value
        if fields:
            yield fields


def prepare_resolver(cache):
    """Build-only x86 apt sandbox; no host installation and no database startup."""
    source_pin = json.loads(
        (ROOT / "platforms/android/toolchain.lock.json").read_text()
    )["sources"]["proot"]["commit"]
    actual = subprocess.check_output(
        ["git", "-C", str(cache / "proot-src"), "rev-parse", "HEAD"], text=True
    ).strip()
    if actual != source_pin or subprocess.check_output(
        ["git", "-C", str(cache / "proot-src"), "status", "--porcelain"]
    ):
        raise ValueError("Host resolver requires clean pinned PRoot source")
    host = cache / "host-proot-lld"
    if host.exists():
        raise ValueError(
            "Host resolver already exists; preserving original inputs/evidence"
        )
    host.mkdir()
    shutil.copytree(cache / "proot-src/src", host / "src")
    subprocess.run(
        [
            "patch",
            "-p1",
            "-i",
            str(ROOT / "platforms/android/native/proot-string-header.patch"),
        ],
        cwd=host / "src",
        check=True,
    )
    env = os.environ.copy()
    env["CPPFLAGS"] = "-DARG_MAX=131072"
    linker = (
        cache / "sdk/android-ndk-r30/toolchains/llvm/prebuilt/linux-x86_64/bin/ld.lld"
    )
    subprocess.run(
        [
            "make",
            "-j2",
            "proot",
            "CC=clang -fuse-ld=" + str(linker),
            "PROOT_UNBUNDLE_LOADER=/nonexistent",
        ],
        cwd=host / "src",
        env=env,
        check=True,
    )
    root = cache / "guest-build/x86_64"
    root.mkdir(parents=True)
    subprocess.run(
        [
            "bsdtar",
            "--no-same-owner",
            "-xpf",
            str(base_layer(cache, BASES["x86_64"])),
            "-C",
            str(root),
        ],
        check=True,
    )
    shutil.copyfile("/etc/resolv.conf", root / "etc/resolv.conf")
    # Defense in depth: this step only downloads, never installs or starts packages.
    policy = root / "usr/sbin/policy-rc.d"
    policy.write_text("#!/bin/sh\nexit 101\n")
    policy.chmod(0o755)
    (root / "tmp/apk264-empty-status").touch()
    apt_command(cache, "/usr/bin/dpkg", "--add-architecture", "arm64")
    print(apt_command(cache, "/usr/bin/apt-get", "update"), flush=True)
    for arch in ["amd64", "arm64"]:
        (root / ("var/cache/apk264-" + arch) / "partial").mkdir(parents=True)
        print(
            apt_command(
                cache,
                "/usr/bin/apt-get",
                "-o",
                "APT::Architecture=" + arch,
                "-o",
                "Dir::State::status=/tmp/apk264-empty-status",
                "-o",
                "Dir::Cache::archives=/var/cache/apk264-" + arch,
                "--download-only",
                "--yes",
                "--no-install-recommends",
                "install",
                "postgresql-17:" + arch,
                "postgresql-client-17:" + arch,
                "python3:" + arch,
                "libffi8:" + arch,
                "ca-certificates",
            ),
            flush=True,
        )


def resolve(cache):
    """Freeze one signed apt solution plus canonical Python/Node inputs; never called by build."""
    contract_path = ROOT / "dev/runtime-contract.json"
    contract = json.loads(contract_path.read_text())
    lock = {"schema": 1, "contract_sha256": sha(contract_path), "abi": {}}
    aptroot = cache / "guest-build/x86_64"
    # apt itself verified these indexes/signatures. Independently verify retained releases.
    keyring = aptroot / "usr/share/keyrings/debian-archive-keyring.gpg"
    releases = sorted((aptroot / "var/lib/apt/lists").glob("*InRelease"))
    if not releases:
        raise ValueError("Signed apt resolver must be prepared first")
    lock["apt_releases"] = []
    for release in releases:
        subprocess.run(["gpgv", "--keyring", str(keyring), str(release)], check=True)
        lock["apt_releases"].append({"name": release.name, "sha256": sha(release)})
    lock["debian_keyring_sha256"] = sha(keyring)
    for abi, base in BASES.items():
        packages = []
        archives = sorted(
            (aptroot / ("var/cache/apk264-" + base["arch"])).glob("*.deb")
        )
        if not archives:
            raise ValueError("Missing signed apt download closure for " + abi)
        wanted = []
        for archive in archives:
            package, version, arch = urllib.parse.unquote(archive.name[:-4]).split("_")
            wanted.append(
                package + (":" + arch if arch != "all" else "") + "=" + version
            )
        records = list(
            paragraphs(apt_command(cache, "/usr/bin/apt-cache", "show", *wanted))
        )
        for archive in archives:
            package, version, arch = urllib.parse.unquote(archive.name[:-4]).split("_")
            record = next(
                r
                for r in records
                if r.get("Package") == package
                and r.get("Version") == version
                and r.get("Architecture") == arch
                and r.get("SHA256") == sha(archive)
            )
            # Probe exact authenticated-metadata pool path on official repositories; pin bytes.
            filename = record["Filename"]
            origins = [
                "https://deb.debian.org/debian/",
                "https://deb.debian.org/debian-security/",
            ]
            found = None
            for origin in origins:
                try:
                    request = urllib.request.Request(origin + filename, method="HEAD")
                    with urllib.request.urlopen(request, timeout=30):
                        found = origin + filename
                    break
                except urllib.error.HTTPError as error:
                    if error.code != 404:
                        raise
            if found is None:
                raise ValueError("No official archive URL for " + archive.name)
            dst = cache / "downloads" / archive.name
            if not dst.exists():
                shutil.copyfile(archive, dst)
            packages.append(
                {
                    "package": package,
                    "version": version,
                    "arch": arch,
                    "url": found,
                    "filename": archive.name,
                    "sha256": sha(archive),
                    "source": record.get("Source", package),
                }
            )
        wheels = []
        platform = "aarch64" if abi == "arm64-v8a" else "x86_64"
        for name, version in contract["dependencies"]["python"].items():
            data = json.load(
                urllib.request.urlopen(
                    f"https://pypi.org/pypi/{name}/{version}/json", timeout=60
                )
            )

            def suitable(item):
                filename = item["filename"]
                if filename.endswith("-py3-none-any.whl"):
                    return True
                python = "-cp313-cp313-" in filename or bool(
                    re.search(r"-cp3(?:[89]|1[0123])-abi3-", filename)
                )
                return (
                    python
                    and "manylinux" in filename
                    and platform in filename
                    and item["packagetype"] == "bdist_wheel"
                )

            candidates = sorted(
                (x for x in data["urls"] if suitable(x)), key=lambda x: x["filename"]
            )
            if not candidates:
                raise ValueError(
                    "No canonical Python3.13 wheel for " + name + " on " + abi
                )
            selected = candidates[0]
            wheels.append(
                {
                    "package": name,
                    "version": version,
                    "filename": selected["filename"],
                    "url": selected["url"],
                    "sha256": selected["digests"]["sha256"],
                }
            )
        node_arch = "arm64" if abi == "arm64-v8a" else "x64"
        node_version = contract["dependencies"]["node"]
        sums_url = f"https://nodejs.org/dist/v{node_version}/SHASUMS256.txt"
        sums = urllib.request.urlopen(sums_url, timeout=60).read()
        filename = f"node-v{node_version}-linux-{node_arch}.tar.xz"
        node_hash = next(
            line.split()[0]
            for line in sums.decode().splitlines()
            if line.split()[-1] == filename
        )
        if abi == "arm64-v8a" and node_hash != contract["node_arm64_sha256"]:
            raise ValueError("Canonical Node ARM64 digest drift")
        lock["abi"][abi] = {
            "base": base,
            "packages": packages,
            "wheels": wheels,
            "node": {
                "url": f"https://nodejs.org/dist/v{node_version}/" + filename,
                "filename": filename,
                "sha256": node_hash,
                "version": node_version,
                "checksums_source": sums_url,
                "checksums_sha256": hashlib.sha256(sums).hexdigest(),
            },
        }
    return lock


def base_layer(cache, base):
    path = cache / "downloads" / (base["layer"] + ".tar.gz")
    if path.exists():
        if sha(path) != base["layer"]:
            raise ValueError("OCI base layer drift")
        return path
    token = json.load(
        urllib.request.urlopen(
            "https://auth.docker.io/token?service=registry.docker.io&scope=repository:library/debian:pull"
        )
    )["token"]
    request = urllib.request.Request(
        "https://registry-1.docker.io/v2/library/debian/blobs/sha256:" + base["layer"],
        headers={"Authorization": "Bearer " + token},
    )
    with (
        urllib.request.urlopen(request, timeout=120) as source,
        path.open("xb") as target,
    ):
        shutil.copyfileobj(source, target)
    if sha(path) != base["layer"]:
        raise ValueError("OCI downloaded layer drift")
    return path


def host_node(cache, lock):
    """Use the canonical Node22 distribution's npm, independent of ambient npm."""
    node = lock["abi"]["x86_64"]["node"]
    archive = fetch(node["url"], node["sha256"], cache / "downloads" / node["filename"])
    destination = cache / ("host-node-" + node["version"])
    prefix = "node-v" + node["version"] + "-linux-x64/"
    if not destination.exists():
        destination.mkdir()
        with tarfile.open(archive) as source:
            for member in source:
                if not member.name.startswith(prefix):
                    continue
                member.name = member.name[len(prefix) :]
                if member.name:
                    source.extract(member, destination, filter="tar")
    # Bind the actual executable/installer tree to the verified distribution.
    with tarfile.open(archive) as source:
        for member in source:
            if not member.isfile() or not member.name.startswith(prefix):
                continue
            name = member.name[len(prefix) :]
            if name == "bin/node" or name.startswith("lib/node_modules/npm/"):
                with (
                    source.extractfile(member) as expected,
                    (destination / name).open("rb") as actual,
                ):
                    if (
                        hashlib.file_digest(expected, "sha256").digest()
                        != hashlib.file_digest(actual, "sha256").digest()
                    ):
                        raise ValueError("Pinned Node/npm tool byte drift")
    return destination


def producer_inputs():
    names = [
        "platforms/android/runtime/build_payload.py",
        "platforms/android/runtime/payload.lock.json",
        "platforms/android/native/runtime/guest_compat.c",
        "dev/runtime-contract.json",
        "ai/prototype/package.json",
        "ai/prototype/package-lock.json",
        "scripts/check_runtime_parity.py",
        "scripts/build-android-release.py",
    ]
    return {name: sha(ROOT / name) for name in names}


def payload_record(abi):
    lock = json.loads((HERE / "payload.lock.json").read_text())
    contract = json.loads((ROOT / "dev/runtime-contract.json").read_text())
    if lock["contract_sha256"] != sha(ROOT / "dev/runtime-contract.json"):
        raise ValueError(
            "Runtime contract changed; explicitly resolve/review new payload pins"
        )
    record = lock["abi"][abi]
    if record["base"] != BASES[abi]:
        raise ValueError("Guest base provenance/ABI drift")
    if {p["package"]: p["version"] for p in record["wheels"]} != contract[
        "dependencies"
    ]["python"]:
        raise ValueError("Guest Python dependency drift")
    if record["node"]["version"] != contract["dependencies"]["node"]:
        raise ValueError("Guest Node dependency drift")
    for item in record["packages"]:
        if item["arch"] not in (record["base"]["arch"], "all"):
            raise ValueError("Guest Debian ABI drift")
    pg = next(p for p in record["packages"] if p["package"] == "postgresql-17")
    if pg["version"].split(".")[0] != contract["dependencies"]["postgresql_major"]:
        raise ValueError("Guest PostgreSQL dependency drift")
    return lock, record


def safe_name(name):
    if (
        not name
        or name.startswith("/")
        or any(p in ("", ".", "..") for p in name.split("/"))
    ):
        raise ValueError("Unsafe guest path")
    return name


def guest_path(name, files):
    """Resolve guest absolute/relative links without following any host filesystem link."""
    pending = name.split("/")
    resolved = []
    links = 0
    while pending:
        part = pending.pop(0)
        if part in ("", "."):
            continue
        if part == "..":
            if not resolved:
                raise ValueError("Guest link escapes root")
            resolved.pop()
            continue
        resolved.append(part)
        entry = files.get("/".join(resolved), {})
        if entry.get("kind") == "symlink":
            links += 1
            if links > 40:
                raise ValueError("Guest link cycle")
            target = entry["target"]
            if target.startswith("/"):
                resolved = []
            else:
                resolved.pop()
            pending = target.split("/") + pending
    return "/".join(resolved)


def guest_elf(data, abi):
    """Read ELF program/dynamic tables without loading guest code on the host."""
    import struct

    if len(data) < 64 or data[:6] != b"\x7fELF\x02\x01":
        raise ValueError("Guest expected little-endian ELF64")
    kind, machine = struct.unpack_from("<HH", data, 16)
    if kind not in (2, 3) or machine != {"x86_64": 62, "arm64-v8a": 183}[abi]:
        raise ValueError("Guest ELF ABI/type mismatch")
    offset = struct.unpack_from("<Q", data, 32)[0]
    size, count = struct.unpack_from("<HH", data, 54)
    if size < 56 or not count or offset + count * size > len(data):
        raise ValueError("Guest ELF program headers invalid")
    headers = [
        struct.unpack_from("<IIQQQQQQ", data, offset + i * size) for i in range(count)
    ]
    loads = [h for h in headers if h[0] == 1]
    if not loads:
        raise ValueError("Guest ELF has no LOAD")
    for h in headers:
        if h[2] + h[5] > len(data):
            raise ValueError("Guest ELF segment truncated")
        if h[0] == 1 and (
            h[5] > h[6] or h[1] & 3 == 3 or h[7] < 4096 or h[2] % 4096 != h[3] % 4096
        ):
            raise ValueError("Guest ELF unsafe LOAD")
        if (
            abi == "arm64-v8a"
            and h[0] == 1
            and (h[7] < 16384 or h[2] % 16384 != h[3] % 16384)
        ):
            raise ValueError("Guest ARM64 ELF lacks 16KiB LOAD layout")
    interpreter = None
    entries = []
    for h in headers:
        if h[0] == 3:
            interpreter = data[h[2] : h[2] + h[5]].rstrip(b"\0").decode()
        if h[0] == 2:
            if h[5] % 16:
                raise ValueError("Guest ELF dynamic table invalid")
            for i in range(h[2], h[2] + h[5], 16):
                tag, value = struct.unpack_from("<qQ", data, i)
                if tag == 0:
                    break
                entries.append((tag, value))
    strings = next((v for t, v in entries if t == 5), None)
    string_size = next((v for t, v in entries if t == 10), 0)
    table = b""
    if strings is not None:
        segment = next(
            (
                h
                for h in loads
                if h[3] <= strings and strings + string_size <= h[3] + h[5]
            ),
            None,
        )
        if segment is None:
            raise ValueError("Guest ELF string table invalid")
        start = segment[2] + strings - segment[3]
        table = data[start : start + string_size]

    def string(index):
        end = table.find(b"\0", index)
        if index >= len(table) or end < 0:
            raise ValueError("Guest ELF dynamic string invalid")
        return table[index:end].decode()

    return {
        "machine": machine,
        "type": kind,
        "interpreter": interpreter,
        "needed": [string(v) for t, v in entries if t == 1],
        "rpath": [string(v) for t, v in entries if t == 15],
        "runpath": [string(v) for t, v in entries if t == 29],
        "load_alignment": min(h[7] for h in loads),
    }


def inspect_guest(archive, files, abi):
    """Check the complete ZIP inventory, native ABI and loader closure, never execute it."""
    for name, entry in files.items():
        safe_name(name)
        if entry["kind"] not in ("file", "symlink", "directory"):
            raise ValueError("Unknown guest file kind")
        # Ordinary files cannot have link/file ancestors (ZIP extraction writes files first).
        for parent in Path(name).parents:
            if (
                str(parent) != "."
                and files.get(parent.as_posix(), {}).get("kind") != "directory"
            ):
                raise ValueError("Guest parent is missing or not a directory")
        if entry["kind"] == "symlink":
            guest_path(name, files)
    inventory = {}
    with zipfile.ZipFile(archive) as source:
        names = source.namelist()
        if len(names) != len(set(names)) or set(names) != {
            n for n, r in files.items() if r["kind"] == "file"
        }:
            raise ValueError("Guest archive inventory drift")
        for name in names:
            entry = files[name]
            info = source.getinfo(name)
            if (
                info.file_size != entry["size"]
                or info.external_attr >> 16 != stat.S_IFREG | entry["mode"]
            ):
                raise ValueError("Guest file size/mode drift")
            with source.open(name) as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != entry["sha256"]:
                    raise ValueError("Guest file byte drift")
            with source.open(name) as stream:
                magic = stream.read(4)
            if magic == b"\x7fELF":
                inventory[name] = guest_elf(source.read(name), abi)
    triple = "x86_64-linux-gnu" if abi == "x86_64" else "aarch64-linux-gnu"
    defaults = ["/lib/" + triple, "/usr/lib/" + triple, "/lib", "/usr/lib"]
    contexts = {}

    def visit(name, inherited=()):
        context = (name, inherited)
        if context in contexts:
            return contexts[context] is not False  # A pending edge is an ELF cycle.
        contexts[context] = None
        elf = inventory[name]
        if elf["interpreter"]:
            interpreter = guest_path(elf["interpreter"], files)
            if interpreter not in inventory:
                raise ValueError("Missing guest ELF interpreter: " + name)
        origin = "/" + str(Path(name).parent)

        def expand(values):
            paths = [
                part.replace("${ORIGIN}", origin).replace("$ORIGIN", origin)
                for value in values
                for part in value.split(":")
            ]
            if any(not p.startswith("/") or "$" in p for p in paths):
                raise ValueError("Unsupported guest ELF search path: " + name)
            return paths

        # glibc DT_RPATH propagates to descendants; DT_RUNPATH applies only to
        # direct children. Bundled wheel libraries rely on the former. A global
        # basename/sibling search would falsely admit a genuinely missing loader path.
        rpath = expand(elf["rpath"]) if not elf["runpath"] else []
        inherited_next = tuple(dict.fromkeys(rpath + list(inherited)))
        search = list(inherited_next) + expand(elf["runpath"]) + defaults
        dependencies = {}
        for needed in elf["needed"]:
            if "/" in needed:
                raise ValueError("Unsupported guest ELF dependency path")
            found = next(
                (
                    resolved
                    for directory in search
                    if (resolved := guest_path(directory + "/" + needed, files))
                    in inventory
                ),
                None,
            )
            if found is None:
                contexts[context] = False
                return False  # A library may need an ancestor's DT_RPATH context.
            dependencies[needed] = found
        if not all(visit(found, inherited_next) for found in dependencies.values()):
            contexts[context] = False
            return False
        contexts[context] = True
        if "resolved_needed" not in elf:
            elf["resolved_needed"] = dependencies
            elf["inherited_rpath"] = list(inherited)
        return True

    for name, elf in inventory.items():
        complete = visit(name)
        # Each actual executable/extension must load in its own context. An
        # unrelated executable's RPATH cannot repair a broken launch root.
        launch_root = (
            elf["interpreter"]
            or "/bin/" in name
            or (name.startswith("opt/python-packages/") and ".libs/" not in name)
        )
        if launch_root and not complete:
            raise ValueError("Missing guest native dependency for launch root: " + name)
    for name, elf in inventory.items():
        if "resolved_needed" not in elf:
            raise ValueError(
                "Missing guest native dependency: "
                + name
                + " -> "
                + ",".join(elf["needed"])
            )
    for name in (
        "usr/lib/postgresql/17/bin/initdb",
        "usr/lib/postgresql/17/bin/postgres",
        "usr/lib/postgresql/17/bin/pg_ctl",
        "usr/lib/postgresql/17/bin/psql",
        "usr/bin/python3.13",
        "opt/node/bin/node",
    ):
        if guest_path(name, files) not in inventory:
            raise ValueError("Missing actual guest executable: " + name)
    return inventory


def inspect_dependencies(archive, files, record):
    from email.parser import BytesParser

    distributions = {}
    npm = {}
    with zipfile.ZipFile(archive) as source:
        for name in files:
            if name.startswith("opt/python-packages/") and name.endswith(
                ".dist-info/METADATA"
            ):
                metadata = BytesParser().parsebytes(source.read(name))
                normalized = re.sub(r"[-_.]+", "-", metadata["Name"]).lower()
                if normalized in distributions:
                    raise ValueError("Duplicate guest Python distribution")
                distributions[normalized] = metadata["Version"]
        expected = {
            re.sub(r"[-_.]+", "-", p["package"]).lower(): p["version"]
            for p in record["wheels"]
        }
        if distributions != expected:
            raise ValueError("Guest installed Python dependency inventory drift")
        node_lock = json.loads((ROOT / "ai/prototype/package-lock.json").read_text())
        for name, pin in node_lock["packages"].items():
            if not name or pin.get("dev"):
                continue
            package_path = name + "/package.json"
            if package_path not in files and pin.get("optional"):
                continue
            if package_path not in files:
                raise ValueError("Missing guest Node dependency: " + name)
            package = json.loads(source.read(package_path))
            if package["version"] != pin["version"]:
                raise ValueError("Guest installed Node dependency drift: " + name)
            npm[name] = {
                "name": package["name"],
                "version": package["version"],
                "integrity": pin.get("integrity"),
            }
        pi = npm["node_modules/@earendil-works/pi-ai"]
        if pi["name"] != "@earendil-works/pi-ai":
            raise ValueError("Guest Pi identity drift")
        for name in (
            "opt/schemii-venv/bin/python",
            "opt/schemii-venv/lib/python3.13/site-packages",
        ):
            if guest_path(name, files) not in files:
                raise ValueError("Missing shared Runtime Python path")
    return {"python": distributions, "npm": npm}


def verify_payload(payload, abi, shared_record, expected_manifest_sha256):
    manifest_path = payload / "guest-manifest.json"
    if (
        not re.fullmatch(r"[0-9a-f]{64}", expected_manifest_sha256 or "")
        or sha(manifest_path) != expected_manifest_sha256
    ):
        raise ValueError("Independent guest manifest anchor mismatch")
    manifest = json.loads(manifest_path.read_text())
    lock, record = payload_record(abi)
    expected_dependencies = {
        "base": record["base"],
        "packages": record["packages"],
        "wheels": record["wheels"],
        "node": record["node"],
    }
    if (
        manifest["abi"] != abi
        or manifest["producer_inputs"] != producer_inputs()
        or manifest["producer_sha256"] != sha(Path(__file__))
        or manifest["payload_lock_sha256"] != sha(HERE / "payload.lock.json")
        or manifest["contract_sha256"] != lock["contract_sha256"]
        or manifest.get("dependencies") != expected_dependencies
    ):
        raise ValueError("Guest source/ABI/dependency provenance drift")
    if manifest.get("shared_source") != shared_record:
        raise ValueError("Guest shared source drift")
    archive = payload / "guest.zip"
    if sha(archive) != manifest["archive_sha256"]:
        raise ValueError("Guest archive drift")
    inventory = inspect_guest(archive, manifest["files"], abi)
    if inventory != manifest.get("elf_inventory"):
        raise ValueError("Guest native inventory drift")
    if inspect_dependencies(archive, manifest["files"], record) != manifest.get(
        "installed_dependencies"
    ):
        raise ValueError("Guest installed dependency record drift")
    return manifest


def assemble(cache, abi, build_id):
    producer_before = sha(Path(__file__))
    inputs_before = producer_inputs()
    sys.path.insert(0, str(ROOT / "scripts"))
    from check_runtime_parity import apk_source_manifest

    commit = subprocess.check_output(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True
    ).strip()
    shared_record = apk_source_manifest(ROOT, commit)
    compiler = Path(shutil.which("clang")).resolve() if abi == "x86_64" else None
    compiler_identity = (
        None
        if compiler is None
        else {
            "binary_sha256": sha(compiler),
            "version": subprocess.check_output(
                [str(compiler), "--version"], text=True
            ).strip(),
            "scope": "x86 diagnostic glibc rename adapter only; not ARM64 toolchain",
        }
    )
    lock_path = HERE / "payload.lock.json"
    lock, record = payload_record(abi)
    destination = cache / "payloads" / build_id
    destination.mkdir(parents=True)
    root = destination / "rootfs"
    root.mkdir()
    # Verified OCI/.deb data only, never user-supplied archives.
    subprocess.run(
        [
            "bsdtar",
            "--no-same-owner",
            "-xpf",
            str(base_layer(cache, record["base"])),
            "-C",
            str(root),
        ],
        check=True,
    )
    inputs = record["packages"] + record["wheels"] + [record["node"]]
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        list(
            pool.map(
                lambda item: fetch(
                    item["url"], item["sha256"], cache / "downloads" / item["filename"]
                ),
                inputs,
            )
        )
    for item in record["packages"]:
        archive = cache / "downloads" / item["filename"]
        members = subprocess.check_output(
            ["ar", "t", str(archive)], text=True
        ).splitlines()
        data_name = next(x for x in members if x.startswith("data.tar."))
        reader = subprocess.Popen(
            ["ar", "p", str(archive), data_name], stdout=subprocess.PIPE
        )
        try:
            subprocess.run(
                ["bsdtar", "--no-same-owner", "-xpf", "-", "-C", str(root)],
                stdin=reader.stdout,
                check=True,
            )
        finally:
            reader.stdout.close()
            if reader.wait() != 0:
                raise ValueError("Cannot read Debian package")
    packages = root / "opt/python-packages"
    packages.mkdir(parents=True)
    for item in record["wheels"]:
        with zipfile.ZipFile(cache / "downloads" / item["filename"]) as wheel:
            for name in wheel.namelist():
                path = Path(name)
                if path.is_absolute() or ".." in path.parts:
                    raise ValueError("Unsafe wheel path")
            wheel.extractall(packages)
    node = root / "opt/node"
    node.mkdir()
    with tarfile.open(cache / "downloads" / record["node"]["filename"]) as source:
        prefix = source.getmembers()[0].name.split("/")[0] + "/"
        for member in source:
            if not member.name.startswith(prefix):
                continue
            member.name = member.name[len(prefix) :]
            if member.name:
                source.extract(member, node, filter="tar")
    if abi == "x86_64":
        # glibc x86 uses legacy rename, which Android app seccomp traps. This
        # explicit ABI adapter uses the same atomic renameat operation as bionic.
        subprocess.run(
            [
                str(compiler),
                "-shared",
                "-fPIC",
                "-O2",
                "-Wl,-z,max-page-size=16384",
                str(ROOT / "platforms/android/native/runtime/guest_compat.c"),
                "-o",
                str(root / "opt/libschemii_guest_compat.so"),
            ],
            check=True,
        )
    # Construct the shared Runtime's stable Python path without executing a guest
    # interpreter or installing from ambient pip. Wheels remain exact verified bytes.
    venv = root / "opt/schemii-venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin/python").symlink_to("/usr/bin/python3.13")
    (venv / "pyvenv.cfg").write_text(
        "home = /usr/bin\ninclude-system-site-packages = false\nversion = 3.13\n"
    )
    (venv / "lib/python3.13").mkdir(parents=True)
    (venv / "lib/python3.13/site-packages").symlink_to("/opt/python-packages")
    (packages / "schemii-shared.pth").write_text("/shared/src\n")
    # npm uses the canonical lock/integrities, but never installs executable lifecycle scripts.
    npm = destination / "npm"
    npm.mkdir()
    for name in ["package.json", "package-lock.json"]:
        shutil.copyfile(ROOT / "ai/prototype" / name, npm / name)
    installer = host_node(cache, lock)
    env = os.environ.copy()
    env.pop("NODE_OPTIONS", None)
    env.pop("NODE_PATH", None)
    env["PATH"] = str(installer / "bin") + os.pathsep + os.defpath
    subprocess.run(
        [
            str(installer / "bin/node"),
            str(installer / "lib/node_modules/npm/bin/npm-cli.js"),
            "ci",
            "--omit=dev",
            "--ignore-scripts",
            "--no-audit",
            "--no-fund",
            "--userconfig=/dev/null",
            "--cache=" + str(cache / "npm-cache"),
            "--registry=https://registry.npmjs.org",
        ],
        cwd=npm,
        env=env,
        check=True,
    )
    shutil.move(npm / "node_modules", root / "node_modules")
    (root / "etc/passwd").write_text(
        "root:x:0:0:root:/root:/bin/sh\nschemii:x:1000:1000:Disposable APK:/state:/bin/sh\n"
    )
    (root / "etc/group").write_text("root:x:0:\nschemii:x:1000:\n")
    # Data/logs/host DNS and generated machine identities are not runtime code.
    for name in ["var/log", "var/cache", "var/lib/apt/lists"]:
        path = root / name
        if path.exists():
            shutil.rmtree(path)
        path.mkdir(parents=True, exist_ok=True)
    for name in ["etc/resolv.conf", "etc/machine-id", "var/lib/dbus/machine-id"]:
        (root / name).unlink(missing_ok=True)
    (root / "etc/resolv.conf").write_text(
        "# No host DNS copied; Android NetworkBridge owns resolution.\n"
    )
    for name in ["state", "shared", "dev/shm", "proc", "tmp", "run"]:
        (root / name).mkdir(parents=True, exist_ok=True)
    files = {}
    archive = destination / "guest.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as output:
        for directory, directories, names in os.walk(root, followlinks=False):
            directories.sort()  # npm creates directories concurrently; ZIP traversal must not depend on that order.
            for name in sorted(directories + names):
                path = Path(directory) / name
                relative = path.relative_to(root).as_posix()
                mode = path.lstat().st_mode
                if stat.S_ISLNK(mode):
                    files[relative] = {"kind": "symlink", "target": os.readlink(path)}
                elif stat.S_ISDIR(mode):
                    files[relative] = {"kind": "directory", "mode": 0o755}
                elif stat.S_ISREG(mode):
                    files[relative] = {
                        "kind": "file",
                        "sha256": sha(path),
                        "size": path.stat().st_size,
                        "mode": 0o555 if mode & 0o111 else 0o444,
                    }
                    info = zipfile.ZipInfo(relative, date_time=(1980, 1, 1, 0, 0, 0))
                    info.compress_type = zipfile.ZIP_DEFLATED
                    info.external_attr = (stat.S_IFREG | files[relative]["mode"]) << 16
                    with path.open("rb") as data, output.open(info, "w") as target:
                        shutil.copyfileobj(data, target)
                else:
                    raise ValueError(
                        "Special file cannot enter APK runtime: " + relative
                    )
    manifest = {
        "schema": 1,
        "abi": abi,
        "kind": "disposable-runtime-payload",
        "producer_sha256": producer_before,
        "producer_inputs": inputs_before,
        "diagnostic_host_compiler": compiler_identity,
        "host_node_archive_sha256": lock["abi"]["x86_64"]["node"]["sha256"],
        "payload_lock_sha256": sha(lock_path),
        "contract_sha256": lock["contract_sha256"],
        "archive_sha256": sha(archive),
        "files": files,
        "dependencies": {
            "base": record["base"],
            "packages": record["packages"],
            "wheels": record["wheels"],
            "node": record["node"],
        },
        "shared_source": shared_record,
        "elf_inventory": inspect_guest(archive, files, abi),
        "installed_dependencies": inspect_dependencies(archive, files, record),
        "execution": "NOT_RUN",
        "readiness": "PENDING",
        "page_scope": "x86_64-4KiB-diagnostic"
        if abi == "x86_64"
        else "ARM64-16KiB-static-only",
        "node": record["node"]["version"],
        "postgresql_major": 17,
        "python_minor": "3.13",
        "pi_lock_sha256": sha(ROOT / "ai/prototype/package-lock.json"),
        "runtime_acceptance": "pending",
    }
    if sha(Path(__file__)) != producer_before:
        raise ValueError("Payload producer changed during assembly; manifest refused")
    if inputs_before != producer_inputs():
        raise ValueError("Payload input changed during assembly; manifest refused")
    if compiler is not None and sha(compiler) != compiler_identity["binary_sha256"]:
        raise ValueError(
            "Diagnostic compiler changed during assembly; manifest refused"
        )
    (destination / "guest-manifest.json").write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n"
    )
    print(
        json.dumps(
            {
                "payload": str(destination),
                "abi": abi,
                "files": len(files),
                "zip_sha256": sha(archive),
                "runtime_acceptance": "pending",
            },
            indent=2,
        )
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cache", type=Path, default=Path.home() / ".cache/schemii-apk264"
    )
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("prepare-resolver")
    sub.add_parser("resolve")
    b = sub.add_parser("assemble")
    b.add_argument("--abi", choices=BASES, required=True)
    b.add_argument("--build-id", required=True)
    args = parser.parse_args()
    os.umask(0o077)
    sys.path.insert(0, str(HERE.parent))
    from build import validate_cache_owner

    args.cache = args.cache.resolve()
    args.cache.mkdir(parents=True, exist_ok=True)
    (args.cache / "downloads").mkdir(exist_ok=True)
    validate_cache_owner(args.cache)
    with (args.cache / ".payload-lock").open("w") as lease:
        fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.action == "prepare-resolver":
            prepare_resolver(args.cache)
        elif args.action == "resolve":
            lock = resolve(args.cache)
            path = HERE / "payload.lock.json"
            with path.open("x") as output:
                output.write(json.dumps(lock, indent=2) + "\n")
        else:
            if not re.fullmatch(r"[A-Za-z0-9_-]+", args.build_id):
                raise ValueError("Unsafe build ID")
            assemble(args.cache, args.abi, args.build_id)


if __name__ == "__main__":
    main()
