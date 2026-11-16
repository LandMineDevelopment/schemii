#!/usr/bin/env python3
"""Pinned, disposable Android feasibility build. Does not operate any deployment."""

from __future__ import annotations
import argparse
import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import sys
import stat
import tempfile
import subprocess
import tarfile
import urllib.request
import zipfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
LOCK = json.loads((HERE / "toolchain.lock.json").read_text())
ABI_TRIPLES = {
    "arm64-v8a": "aarch64-linux-android29",
    "x86_64": "x86_64-linux-android29",
}


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def run(*args, **kwargs):
    print("+", *map(str, args), flush=True)
    return subprocess.run(list(map(str, args)), check=True, **kwargs)


def fetch(url, expected, destination):
    if destination.exists():
        if digest(destination) != expected:
            raise ValueError(f"Cached checksum mismatch; preserving {destination}")
        return destination
    temporary = destination.with_suffix(destination.suffix + ".partial")
    with urllib.request.urlopen(url, timeout=120) as src, temporary.open("xb") as dst:
        shutil.copyfileobj(src, dst)
    if digest(temporary) != expected:
        raise ValueError(f"Download checksum mismatch; preserving {temporary}")
    temporary.rename(destination)
    return destination


def unzip(archive, destination):
    # Extract verified SDK archives into a fresh directory, preserving Unix links.
    marker = destination / ("." + archive.name + ".sha256")
    if marker.exists() and marker.read_text().strip() == digest(archive):
        return
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as temporary:
        stage = Path(temporary)
        with zipfile.ZipFile(archive) as source:
            for member in source.infolist():
                path = Path(member.filename)
                if path.is_absolute() or ".." in path.parts:
                    raise ValueError("Unsafe SDK archive path")
                target = stage / path
                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                mode = member.external_attr >> 16
                if stat.S_ISLNK(mode):
                    link = source.read(member).decode("utf-8")
                    if not (target.parent / link).resolve().is_relative_to(stage):
                        raise ValueError("SDK link escapes extraction")
                    target.symlink_to(link)
                else:
                    target.write_bytes(source.read(member))
                    if mode:
                        target.chmod(mode & 0o777)
        for child in stage.iterdir():
            if (destination / child.name).exists():
                raise ValueError(
                    f"Existing unverified SDK directory; preserving {destination / child.name}"
                )
            child.rename(destination / child.name)
    marker.write_text(digest(archive) + "\n")


def verify_extracted_archive(archive, destination):
    """Bind tools actually executed/read to verified archive bytes, including links."""
    with zipfile.ZipFile(archive) as source:
        for member in source.infolist():
            target = destination / member.filename
            if member.is_dir():
                continue
            if stat.S_ISLNK(member.external_attr >> 16):
                if (
                    not target.is_symlink()
                    or os.readlink(target) != source.read(member).decode()
                ):
                    raise ValueError("SDK extracted link drift: " + member.filename)
            else:
                if target.is_symlink() or not target.is_file():
                    raise ValueError(
                        "SDK extracted file missing/linked: " + member.filename
                    )
                with source.open(member) as expected, target.open("rb") as actual:
                    if (
                        hashlib.file_digest(expected, "sha256").digest()
                        != hashlib.file_digest(actual, "sha256").digest()
                    ):
                        raise ValueError("SDK extracted byte drift: " + member.filename)


def bootstrap(cache):
    sdk = cache / "sdk"
    for item in LOCK["archives"]:
        if item["package"] == "emulator":
            continue  # Explicitly provision the optional emulator separately.
        archive = fetch(
            item["url"],
            item["sha256"],
            cache / "downloads" / item["url"].rsplit("/", 1)[-1],
        )
        unzip(archive, sdk)
    for name, item in LOCK["sources"].items():
        if name == "proot":
            src = cache / "proot-src"
            if not src.exists():
                run("git", "clone", "--no-checkout", item["url"], src)
            run("git", "-C", src, "checkout", "--detach", item["commit"])
            if subprocess.check_output(
                ["git", "-C", str(src), "status", "--porcelain"]
            ):
                raise ValueError("Private PRoot source has changes; preserving them")
        else:
            archive = fetch(
                item["url"], item["sha256"], cache / "downloads" / "talloc-2.5.0.tar.gz"
            )
            if not (cache / "talloc-2.5.0").exists():
                with tarfile.open(archive) as source:
                    source.extractall(cache, filter="data")
    if LOCK.get("go"):
        item = LOCK["go"]
        archive = fetch(
            item["url"],
            item["sha256"],
            cache / "downloads" / item["url"].rsplit("/", 1)[-1],
        )
        if not (cache / "go").exists():
            with tarfile.open(archive) as source:
                source.extractall(cache, filter="tar")
    (cache / "licenses.json").write_text(
        json.dumps(
            {
                "sdk_license": LOCK["license_source"],
                "scope": "authorized local APK264 build",
                "third_party": LOCK["sources"],
                "distribution": "not authorized by this build",
            },
            indent=2,
        )
        + "\n"
    )


def native(cache, out, abi):
    tc = cache / "sdk/android-ndk-r30/toolchains/llvm/prebuilt/linux-x86_64/bin"
    cc = tc / (ABI_TRIPLES[abi] + "-clang")
    dst = out / "lib" / abi
    dst.mkdir(parents=True)
    common = [
        "-O2",
        "-fPIE",
        "-pie",
        "-Wl,-z,max-page-size=16384",
        "-Wl,-z,noexecstack",
    ]
    run(cc, *common, HERE / "native/runtime_probe.c", "-o", dst / "libruntime_probe.so")
    # Out-of-tree PRoot objects; no reset/clean of any shared source directory.
    build = out / "native" / abi
    build.mkdir(parents=True)
    talloc = cache / "talloc-2.5.0"
    run(
        cc,
        "-c",
        "-O2",
        "-fPIC",
        "-D_GNU_SOURCE",
        "-I" + str(HERE / "native"),
        "-I" + str(talloc),
        talloc / "talloc.c",
        "-o",
        build / "talloc.o",
    )
    run(tc / "llvm-ar", "rcs", build / "libtalloc.a", build / "talloc.o")
    env = os.environ.copy()
    env["CPPFLAGS"] = "-I" + str(talloc) + " -DARG_MAX=131072"
    env["LDFLAGS"] = "-L" + str(build) + " -Wl,-z,max-page-size=16384"
    proot_build = build / "proot-src"
    shutil.copytree(cache / "proot-src/src", proot_build)
    # Upstream ashmem_memfd.c omits string.h; keep the adaptation source-visible.
    run(
        "patch", "-p1", "-i", HERE / "native/proot-string-header.patch", cwd=proot_build
    )
    run(
        "make",
        "-j4",
        "proot",
        "CC=" + str(cc),
        "STRIP=" + str(tc / "llvm-strip"),
        "OBJCOPY=" + str(tc / "llvm-objcopy"),
        "OBJDUMP=" + str(tc / "llvm-objdump"),
        "PROOT_UNBUNDLE_LOADER=/nonexistent",
        cwd=proot_build,
        env=env,
    )
    shutil.copyfile(proot_build / "proot", dst / "libproot.so")
    shutil.copyfile(proot_build / "loader/loader", dst / "libproot_loader.so")
    return {str(p.relative_to(out)): digest(p) for p in sorted(dst.iterdir())}


def native_recipe(source):
    """The base executable recipe and ABI mapping, independent of unrelated edits."""
    tree = ast.parse(source)
    selected = [
        node
        for node in tree.body
        if (
            isinstance(node, ast.FunctionDef)
            and node.name in {"native", "run", "digest"}
        )
        or (
            isinstance(node, ast.Assign)
            and any(
                isinstance(t, ast.Name) and t.id == "ABI_TRIPLES" for t in node.targets
            )
        )
    ]
    if len(selected) != 4:
        raise ValueError("Native build recipe is missing or ambiguous")
    return hashlib.sha256(
        "\n".join(
            ast.dump(node, include_attributes=False) for node in selected
        ).encode()
    ).hexdigest()


def reuse_native(donor_path, out, abis, expected_receipt_sha256):
    """Reuse only exact base executables; no peer component or acceptance inheritance."""
    from verify_apk import elf_identity

    raw = donor_path.read_bytes()
    # The caller selects this anchor independently of the supplied donor. Local
    # receipt/APK/provenance agreement alone permits a fully rebound substitute.
    if not isinstance(expected_receipt_sha256, str) or not re.fullmatch(
        r"[0-9a-f]{64}", expected_receipt_sha256
    ):
        raise ValueError("Independent expected native donor receipt SHA256 required")
    if hashlib.sha256(raw).hexdigest() != expected_receipt_sha256:
        raise ValueError("Native donor does not match independently selected receipt")
    receipt = json.loads(raw)
    apk = Path(receipt["apk"])
    if digest(apk) != receipt["apk_sha256"]:
        raise ValueError("Native donor APK byte drift")
    with zipfile.ZipFile(apk) as package:
        encoded = package.read("assets/runtime/native-provenance.json")
        donor = json.loads(encoded)
        if (
            hashlib.sha256(encoded).hexdigest() != receipt["native_provenance_sha256"]
            or donor != receipt["native"]
        ):
            raise ValueError("Native donor provenance drift")
        if donor["adapter_dirty"] or (
            donor["compile_sdk"],
            donor["target_sdk"],
            donor["min_sdk"],
        ) != (36, 36, 29):
            raise ValueError("Native donor is not a committed modern-target build")
        if donor["toolchain_lock_sha256"] != digest(HERE / "toolchain.lock.json"):
            raise ValueError("Native donor toolchain input drift")
        head = donor["source_commit"]
        old_builder = subprocess.check_output(
            ["git", "-C", str(ROOT), "show", head + ":platforms/android/build.py"]
        )
        if (
            hashlib.sha256(old_builder).hexdigest()
            != donor["adapter_sources"]["platforms/android/build.py"]
        ):
            raise ValueError("Native donor recipe is not its committed source")
        recipe = native_recipe((HERE / "build.py").read_bytes())
        if native_recipe(old_builder) != recipe:
            raise ValueError("Native donor compiler recipe drift")
        for name in [
            "native/runtime_probe.c",
            "native/replace.h",
            "native/proot-string-header.patch",
        ]:
            if donor["adapter_sources"]["platforms/android/" + name] != digest(
                HERE / name
            ):
                raise ValueError("Native donor source input drift: " + name)
        artifacts = {}
        for abi in abis:
            if abi not in donor["abis"]:
                raise ValueError("Native donor ABI missing")
            for name in ["libproot.so", "libproot_loader.so", "libruntime_probe.so"]:
                relative = "lib/" + abi + "/" + name
                data = package.read(relative)
                sha = hashlib.sha256(data).hexdigest()
                if sha != donor["native_files"][relative]:
                    raise ValueError("Native donor executable byte drift")
                elf_identity(data, abi)
                target = out / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                artifacts[relative] = sha
    return artifacts, {
        "source_commit": head,
        "apk_sha256": receipt["apk_sha256"],
        "native_provenance_sha256": receipt["native_provenance_sha256"],
        "receipt_sha256": hashlib.sha256(raw).hexdigest(),
        "expected_receipt_sha256": expected_receipt_sha256,
        "recipe_sha256": recipe,
        "scope": "base bionic executable bytes only; no shared-source/runtime acceptance reuse",
    }


def helper_identity(helper):
    root = helper.resolve().parents[1]
    hashes = {
        str(p.relative_to(root)): digest(p)
        for p in [helper.resolve(), root / "scripts/build-android-release.py"]
    }
    provenance = root / "provenance.json"
    if provenance.exists():
        record = json.loads(provenance.read_text())
        if record["files"] != hashes:
            raise ValueError("Helper snapshot identity drift")
        return {
            "source_head": record["source_head"],
            "files": hashes,
            "kind": "snapshot",
        }
    head = subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
    ).strip()
    for name in hashes:
        committed = subprocess.check_output(
            ["git", "-C", str(root), "show", f"{head}:{name}"]
        )
        if hashlib.sha256(committed).hexdigest() != hashes[name]:
            raise ValueError(
                "Helper/selector have uncommitted changes; use an immutable snapshot"
            )
    return {"source_head": head, "files": hashes, "kind": "checkout"}


def stage_shared(out, helper, commit):
    """Use #265's real validator, then recheck exact bytes extracted from the APK."""
    spec = importlib.util.spec_from_file_location("apk_parity", helper)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    record = module.apk_source_manifest(ROOT, commit)
    shared = out / "assets/shared"
    shared.mkdir(parents=True)
    for name in record["source_files"]:
        target = shared / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(
            subprocess.check_output(
                ["git", "-C", str(ROOT), "show", f"{commit}:{name}"]
            )
        )
    staging = module.check_apk_sources(ROOT, shared, record, commit)
    (out / "staging-readback.json").write_text(json.dumps(staging, indent=2) + "\n")
    metadata = out / "assets/runtime"
    metadata.mkdir(parents=True)
    (metadata / "shared-source.json").write_text(json.dumps(record, indent=2) + "\n")
    return module, record


def stage_runtime_sources(out, module, commit):
    record = module.apk_runtime_source_manifest(ROOT, commit)
    adapters = out / "assets/runtime-source"
    adapters.mkdir(parents=True)
    for name in record["adapter_files"]:
        target = adapters / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(
            subprocess.check_output(
                ["git", "-C", str(ROOT), "show", f"{commit}:{name}"]
            )
        )
    result = module.check_apk_runtime_sources(
        ROOT, out / "assets/shared", adapters, record, commit
    )
    (out / "assets/runtime/packaged-source.json").write_text(
        json.dumps(record, indent=2) + "\n"
    )
    (out / "runtime-source-staging-readback.json").write_text(
        json.dumps(result, indent=2) + "\n"
    )
    return record


def adapter_sources():
    return {
        str(p.relative_to(ROOT)): digest(p)
        for p in sorted(HERE.rglob("*"))
        if p.is_file() and "__pycache__" not in p.parts
    }


def boot_admission(args):
    """Fail before any cache/native/legacy operation for this closed profile."""
    if not getattr(args, "boot_identity_only", False):
        if getattr(args, "expected_adapter_commit", None) and not getattr(
            args, "packaged_shared_runtime", False
        ):
            raise ValueError(
                "Expected adapter commit requires boot-only or packaged runtime"
            )
        return None
    from runtime.check_boot_identity_report import source_identity

    forbidden = (
        "guest_payload",
        "packaged_shared_runtime",
        "guest_manifest_sha256",
        "network_handoff",
        "network_checkout",
        "process_head",
        "process_checkout",
        "integration_probes",
        "subreaper_handoff",
        "subreaper_checkout",
        "wrapper_controls_only",
    )
    if any(getattr(args, k, None) for k in forbidden):
        raise ValueError(
            "Boot-only excludes guest/network/process/subreaper/wrapper inputs"
        )
    if not args.non_debuggable or not args.log_report or not args.native_donor:
        raise ValueError("Boot-only requires nondebug export and anchored base donor")
    commit = subprocess.check_output(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True
    ).strip()
    if args.expected_adapter_commit != commit:
        raise ValueError("Boot-only expected immutable adapter commit mismatch")
    identity = source_identity(ROOT, commit)
    for name, sha in adapter_sources().items():
        data = subprocess.check_output(
            ["git", "-C", str(ROOT), "show", commit + ":" + name]
        )
        if hashlib.sha256(data).hexdigest() != sha:
            raise ValueError("Boot-only requires exact committed adapter bytes")
    if subprocess.check_output(
        ["git", "-C", str(ROOT), "status", "--porcelain", "--", "platforms/android"]
    ):
        raise ValueError("Boot-only adapter worktree is dirty")
    return identity


def boot_native(cache, out, abis, identity):
    tc = cache / "sdk/android-ndk-r30/toolchains/llvm/prebuilt/linux-x86_64/bin"
    artifacts, compilers = {}, {}
    for abi in abis:
        cc = tc / (ABI_TRIPLES[abi] + "-clang")
        target = out / "lib" / abi / "libboot_identity_jni.so"
        run(
            cc,
            "-std=c11",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-fPIC",
            "-shared",
            "-Wl,-z,max-page-size=16384",
            "-Wl,-z,noexecstack",
            "-Wl,-z,defs",
            '-DBOOT_IDENTITY_SOURCE_SHA256="' + identity["source_sha256"] + '"',
            HERE / "native/host/boot_identity.c",
            HERE / "native/host/boot_identity_jni.c",
            "-o",
            target,
        )
        artifacts[str(target.relative_to(out))] = digest(target)
        compilers[abi] = {
            "binary_sha256": digest(cc.resolve()),
            "version": subprocess.check_output(
                [str(cc), "--version"], text=True
            ).strip(),
            "target": ABI_TRIPLES[abi],
        }
    return artifacts, compilers


def packaged_admission(args):
    """Closed static packaging profile; never imply that a host adapter is ready."""
    enabled = getattr(args, "packaged_shared_runtime", False)
    anchors = getattr(args, "guest_manifest_sha256", None) or []
    if not enabled:
        if anchors:
            raise ValueError(
                "Guest manifest anchors require packaged shared runtime profile"
            )
        return
    payloads = args.guest_payload or []
    abis = args.abi or ["arm64-v8a"]
    if (
        not payloads
        or len(payloads) != len(anchors)
        or len(payloads) != len(abis)
        or len(set(abis)) != len(abis)
        or not args.non_debuggable
        or not args.expected_adapter_commit
        or not args.native_donor
    ):
        raise ValueError(
            "Packaged runtime requires nondebug exact source, anchored native donor and one anchored payload per ABI"
        )
    if any(
        getattr(args, name, None)
        for name in (
            "boot_identity_only",
            "wrapper_controls_only",
            "integration_probes",
            "network_handoff",
            "network_checkout",
            "process_head",
            "process_checkout",
            "subreaper_handoff",
            "subreaper_checkout",
        )
    ):
        raise ValueError(
            "Packaged runtime excludes diagnostic peer/boot/wrapper campaigns"
        )
    actual = subprocess.check_output(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True
    ).strip()
    if args.expected_adapter_commit != actual or subprocess.check_output(
        ["git", "-C", str(ROOT), "status", "--porcelain"]
    ):
        raise ValueError("Packaged runtime requires exact committed clean source")


def packaged_readback(extracted, provenance, shared_record):
    from runtime.build_payload import verify_payload

    actual = json.loads(
        (extracted / "assets/runtime/native-provenance.json").read_text()
    )
    if actual != provenance:
        raise ValueError("Signed packaged native provenance drift")
    sys.path.insert(0, str(ROOT / "scripts"))
    from check_runtime_parity import check_apk_runtime_sources

    runtime_source = json.loads(
        (extracted / "assets/runtime/packaged-source.json").read_text()
    )
    if runtime_source != provenance["packaged_shared_runtime"]["runtime_source"]:
        raise ValueError("Signed packaged Runtime projection drift")
    check_apk_runtime_sources(
        ROOT,
        extracted / "assets/shared",
        extracted / "assets/runtime-source",
        runtime_source,
        shared_record["commit"],
    )
    result = {}
    for abi, anchor in provenance["packaged_shared_runtime"][
        "manifest_anchors"
    ].items():
        # Read the actual signed/extracted payload using the same admission, with
        # its independently selected manifest digest, not a new self-description.
        with tempfile.TemporaryDirectory(
            prefix="guest-readback-", dir=extracted.parent
        ) as directory:
            payload = Path(directory)
            for source, target in [
                ("guest-" + abi + ".zip", "guest.zip"),
                ("guest-" + abi + "-manifest.json", "guest-manifest.json"),
            ]:
                shutil.copyfile(extracted / "assets/runtime" / source, payload / target)
            manifest = verify_payload(payload, abi, shared_record, anchor)
            result[abi] = {
                "archive_sha256": manifest["archive_sha256"],
                "manifest_sha256": anchor,
                "elf_count": len(manifest["elf_inventory"]),
                "execution": "NOT_RUN",
                "readiness": "PENDING",
            }
    return result


def build(args, cache):
    if bool(args.native_donor) != bool(args.native_donor_receipt_sha256):
        raise ValueError(
            "Native donor and independent receipt SHA256 must be supplied together"
        )
    packaged_admission(args)
    boot_identity = boot_admission(args)
    if not args.build_id or any(
        c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
        for c in args.build_id
    ):
        raise ValueError("Build ID must be a single safe name")
    if args.wrapper_controls_only and (
        args.guest_payload
        or args.network_handoff
        or args.process_head
        or args.integration_probes
        or args.subreaper_handoff
    ):
        raise ValueError(
            "Wrapper fixture mode cannot include guest, network or peer process campaigns"
        )
    if args.integration_probes:
        if (
            args.wrapper_controls_only
            or args.guest_payload
            or args.process_head
            or not args.network_handoff
            or not args.network_checkout
            or not args.subreaper_handoff
            or not args.subreaper_checkout
        ):
            raise ValueError(
                "Integration probes require only immutable network+subreaper inputs; no guest/polling/wrapper campaign"
            )
        from runtime.peer_inputs import (
            anchored_handoff,
            NETWORK_HANDOFF,
            NETWORK_HEAD,
            NETWORK_INVENTORY,
            inventory_sha256,
        )

        selected = anchored_handoff(args.network_handoff, NETWORK_HANDOFF)
        if (
            selected["source_head"] != NETWORK_HEAD
            or inventory_sha256(selected["source_files"]) != NETWORK_INVENTORY
        ):
            raise ValueError("Integration network source identity drift")
    elif args.subreaper_handoff or args.subreaper_checkout:
        raise ValueError("Subreaper requires the bounded integration profile")
    adapter_before = adapter_sources()
    out = cache / "builds" / args.build_id
    out.mkdir(parents=True)  # Never overwrite a prior success or failure.
    sdk = cache / "sdk"
    bt = sdk / "android-16"  # Google's build-tools_r36 archive top-level name.
    android = sdk / "android-36/android.jar"
    for item in LOCK["archives"]:
        if item["package"] == "emulator":
            continue
        archive = cache / "downloads" / item["url"].rsplit("/", 1)[-1]
        if digest(archive) != item["sha256"]:
            raise ValueError("SDK archive drift")
        verify_extracted_archive(archive, sdk)
    proot = cache / "proot-src"
    pinned = LOCK["sources"]["proot"]["commit"]
    actual = subprocess.check_output(
        ["git", "-C", str(proot), "rev-parse", "HEAD"], text=True
    ).strip()
    if actual != pinned or subprocess.check_output(
        ["git", "-C", str(proot), "status", "--porcelain"]
    ):
        raise ValueError("PRoot input is not the clean pinned source")
    # Native talloc compilation uses only these exact upstream archive members.
    talloc_archive = cache / "downloads/talloc-2.5.0.tar.gz"
    if digest(talloc_archive) != LOCK["sources"]["talloc"]["sha256"]:
        raise ValueError("Talloc archive drift")
    with tarfile.open(talloc_archive) as source:
        for name in ["talloc.c", "talloc.h"]:
            if (
                source.extractfile("talloc-2.5.0/" + name).read()
                != (cache / "talloc-2.5.0" / name).read_bytes()
            ):
                raise ValueError("Talloc source drift")
    abis = args.abi or ["arm64-v8a"]
    artifacts = {}
    native_donor = None
    if args.native_donor:
        artifacts, native_donor = reuse_native(
            args.native_donor, out, abis, args.native_donor_receipt_sha256
        )
    else:
        for abi in abis:
            artifacts.update(native(cache, out, abi))
    boot_compilers = None
    if boot_identity:
        libraries, boot_compilers = boot_native(cache, out, abis, boot_identity)
        artifacts.update(libraries)
    fixture = None
    if args.wrapper_controls_only:
        source = HERE / "native/runtime/collector_fixture.c"
        fixture = {
            "source_sha256": digest(source),
            "scope": "finite collector controls only; no backend or ownership acceptance",
        }
        tc = cache / "sdk/android-ndk-r30/toolchains/llvm/prebuilt/linux-x86_64/bin"
        for abi in abis:
            target = out / "lib" / abi / "libcollector_fixture.so"
            run(
                tc / (ABI_TRIPLES[abi] + "-clang"),
                "-std=c11",
                "-O2",
                "-g",
                "-Wall",
                "-Wextra",
                "-Werror",
                "-fPIE",
                "-pie",
                "-Wl,-z,max-page-size=16384",
                "-Wl,-z,noexecstack",
                '-DCOLLECTOR_FIXTURE_SOURCE_ID="' + fixture["source_sha256"] + '"',
                source,
                "-o",
                target,
            )
            artifacts[str(target.relative_to(out))] = digest(target)
    peer_components = {}
    peer_java = []
    if args.network_handoff:
        if not args.network_checkout:
            raise ValueError("Network checkout required with immutable handoff")
        from runtime.peer_inputs import network

        component, peer_java, libraries = network(
            args.network_handoff,
            args.network_checkout,
            out,
            abis,
            json.loads((ROOT / "dev/runtime-contract.json").read_text()),
        )
        artifacts.update(libraries)
        peer_components["network"] = component
    if args.process_head:
        if not args.process_checkout:
            raise ValueError("Process checkout required with immutable head")
        from runtime.peer_inputs import process

        tc = cache / "sdk/android-ndk-r30/toolchains/llvm/prebuilt/linux-x86_64/bin"
        component, libraries = process(
            args.process_checkout, args.process_head, out, abis, tc
        )
        artifacts.update(libraries)
        peer_components["process"] = component
    if args.integration_probes:
        from runtime.peer_inputs import subreaper

        tc = cache / "sdk/android-ndk-r30/toolchains/llvm/prebuilt/linux-x86_64/bin"
        component, libraries = subreaper(
            args.subreaper_handoff, args.subreaper_checkout, out, abis, tc
        )
        artifacts.update(libraries)
        peer_components["subreaper"] = component
    commit = subprocess.check_output(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True
    ).strip()
    helper_before = helper_identity(args.parity_helper)
    module, record = stage_shared(out, args.parity_helper.resolve(), commit)
    provenance = {
        "schema": 1,
        "kind": "disposable-feasibility",
        "stack_acceptance": False,
        "source_commit": commit,
        "adapter_dirty": bool(
            subprocess.check_output(["git", "-C", str(ROOT), "status", "--porcelain"])
        ),
        "target_sdk": 36,
        "min_sdk": 29,
        "compile_sdk": 36,
        "application_id": "development.landmine.schemii.bootidentityprobe"
        if boot_identity
        else "development.landmine.schemii.runtimeprobe",
        "abis": abis,
        "peer_components": peer_components,
        "debuggable": not args.non_debuggable,
        "diagnostic_log_export": args.log_report,
        "wrapper_controls_only": args.wrapper_controls_only,
        "wrapper_fixture": fixture,
        "diagnostic_profile": "boot-identity-p1b-v1"
        if boot_identity
        else ("subreaper13e-networkcde-v1" if args.integration_probes else None),
        "native_files": artifacts,
        "native_donor": native_donor,
        "toolchain_lock_sha256": digest(HERE / "toolchain.lock.json"),
        "adapter_sources": adapter_before,
        "parity_helper": helper_identity(args.parity_helper),
        "host_tools": {
            name: {
                "version": subprocess.check_output(
                    [name, "--version"], text=True, stderr=subprocess.STDOUT
                ).strip(),
                "binary_sha256": digest(Path(shutil.which(name)).resolve()),
            }
            for name in ["java", "javac", "python3"]
        },
        "pending": [
            "full runtime payload",
            "ARM64 Android16 execution",
            "PG17 two clusters",
            "Python native extensions",
            "Node22/Pi",
            "Go tailnet",
            "pre-PIN readiness",
        ],
    }
    if boot_identity:
        provenance["boot_identity"] = boot_identity
        provenance["host_tools"]["boot_compilers"] = boot_compilers
        from runtime.check_boot_identity_report import check_provenance

        check_provenance(provenance, boot_identity)
    if getattr(args, "packaged_shared_runtime", False):
        provenance["packaged_shared_runtime"] = {
            "manifest_anchors": {},
            "runtime_source": stage_runtime_sources(out, module, commit),
            "execution": "NOT_RUN",
            "readiness": "PENDING",
            "launch_gate": "shared endpoint/owner-FD-journal adapter and reviewed runtime campaign pending",
        }
    if args.guest_payload:
        provenance["guest_payloads"] = {}
        provenance["guest_probe_sha256"] = digest(HERE / "runtime/guest_probe.py")
        provenance["runtime_interface_sha256"] = digest(HERE / "runtime-interface.json")
        shutil.copyfile(
            HERE / "runtime-interface.json",
            out / "assets/runtime/runtime-interface.json",
        )
        shutil.copyfile(
            HERE / "runtime/guest_probe.py", out / "assets/runtime/guest_probe.py"
        )
        for payload in args.guest_payload:
            manifest_path = payload / "guest-manifest.json"
            manifest = json.loads(manifest_path.read_text())
            abi = manifest["abi"]
            if manifest.get("producer_sha256") != digest(
                HERE / "runtime/build_payload.py"
            ):
                raise ValueError(
                    "Guest producer source drift; preserve old payload and assemble a new one"
                )
            from runtime.build_payload import producer_inputs, verify_payload

            if manifest.get("producer_inputs") != producer_inputs():
                raise ValueError("Guest producer/dependency input drift")
            if getattr(args, "packaged_shared_runtime", False):
                anchor = args.guest_manifest_sha256[len(provenance["guest_payloads"])]
                manifest = verify_payload(payload, abi, record, anchor)
                provenance["packaged_shared_runtime"]["manifest_anchors"][abi] = anchor
            if abi not in abis or abi in provenance["guest_payloads"]:
                raise ValueError("Guest/native ABI mismatch or duplicate")
            if manifest["payload_lock_sha256"] != digest(
                HERE / "runtime/payload.lock.json"
            ):
                raise ValueError("Guest payload lock drift")
            if manifest["contract_sha256"] != digest(
                ROOT / "dev/runtime-contract.json"
            ):
                raise ValueError("Guest contract drift")
            archive = payload / "guest.zip"
            if digest(archive) != manifest["archive_sha256"]:
                raise ValueError("Guest archive drift")
            metadata = out / "assets/runtime"
            shutil.copyfile(archive, metadata / ("guest-" + abi + ".zip"))
            shutil.copyfile(
                manifest_path, metadata / ("guest-" + abi + "-manifest.json")
            )
            provenance["guest_payloads"][abi] = {
                "archive_sha256": manifest["archive_sha256"],
                "manifest_sha256": digest(manifest_path),
                "payload_lock_sha256": manifest["payload_lock_sha256"],
            }
    if getattr(args, "packaged_shared_runtime", False) and set(
        provenance["guest_payloads"]
    ) != set(abis):
        raise ValueError("Packaged guest/native ABI coverage mismatch")
    if args.integration_probes:
        from runtime.peer_inputs import integration_components

        integration_components(provenance)
    (out / "assets/runtime/native-provenance.json").write_text(
        json.dumps(provenance, indent=2) + "\n"
    )
    # Package notices for the exact externally built GPL/LGPL runtime components.
    notices = out / "assets/runtime/licenses"
    notices.mkdir()
    for name in ["COPYING", "AUTHORS"]:
        source = cache / "proot-src" / name
        if source.exists():
            shutil.copyfile(source, notices / ("proot-" + name))
    for name in ["LICENSE"]:
        source = cache / "talloc-2.5.0" / name
        if source.exists():
            shutil.copyfile(source, notices / ("talloc-" + name))
    # No Gradle/Maven dependency graph is needed for this plain-Java diagnostic APK.
    unsigned = out / "unsigned.apk"
    manifest = out / "AndroidManifest.xml"
    text = (HERE / "app/src/main/AndroidManifest.xml").read_text()
    if args.non_debuggable:
        text = text.replace('android:debuggable="true"', 'android:debuggable="false"')
    if args.network_handoff:
        text = text.replace(
            '<uses-permission android:name="android.permission.INTERNET" />',
            '<uses-permission android:name="android.permission.INTERNET" />\n'
            '    <uses-permission android:name="android.permission.ACCESS_NETWORK_STATE" />',
        )
    if boot_identity:
        text = text.replace(
            'package="development.landmine.schemii.runtimeprobe"',
            'package="development.landmine.schemii.bootidentityprobe"',
        )
        text = text.replace(
            'android:name=".ProbeActivity"',
            'android:name="development.landmine.schemii.runtimeprobe.ProbeActivity"',
        )
        text = text.replace(
            '    <uses-permission android:name="android.permission.INTERNET" />\n', ""
        )
        text = text.replace(
            "Schemii runtime probe", "Schemii boot identity observation"
        )
    manifest.write_text(text)
    run(
        bt / "aapt2",
        "link",
        "-o",
        unsigned,
        "-I",
        android,
        "--manifest",
        manifest,
        "-A",
        out / "assets",
    )
    classes = out / "classes"
    classes.mkdir()
    java = sorted((HERE / "app/src/main/java").rglob("*.java")) + peer_java
    run(
        "javac",
        "-source",
        "8",
        "-target",
        "8",
        "-bootclasspath",
        str(android) + os.pathsep + str(bt / "core-lambda-stubs.jar"),
        "-d",
        classes,
        *java,
    )
    dex = out / "dex"
    dex.mkdir()
    run(
        bt / "d8",
        "--min-api",
        "29",
        "--lib",
        android,
        "--output",
        dex,
        *sorted(classes.rglob("*.class")),
    )
    with zipfile.ZipFile(unsigned, "a", compression=zipfile.ZIP_DEFLATED) as apk:
        apk.write(dex / "classes.dex", "classes.dex")
        for p in sorted((out / "lib").rglob("*.so")):
            apk.write(p, str(p.relative_to(out)))
    aligned = out / "aligned.apk"
    run(bt / "zipalign", "-p", "-f", "4", unsigned, aligned)
    signing = cache / "signing"
    signing.mkdir(exist_ok=True, mode=0o700)
    key = signing / "disposable.p12"
    password = signing / "password"
    if not key.exists():
        if not password.exists():
            import secrets

            password.write_text(secrets.token_urlsafe(32))
            password.chmod(0o600)
        run(
            "keytool",
            "-genkeypair",
            "-keystore",
            key,
            "-storepass:file",
            password,
            "-alias",
            "disposable",
            "-keyalg",
            "RSA",
            "-keysize",
            "3072",
            "-validity",
            "365",
            "-dname",
            "CN=Schemii Disposable Runtime Probe",
            "-storetype",
            "PKCS12",
        )
    signed = out / "schemii-runtime-probe.apk"
    run(
        bt / "apksigner",
        "sign",
        "--ks",
        key,
        "--ks-pass",
        "file:" + str(password),
        "--out",
        signed,
        aligned,
    )
    certificate = subprocess.check_output(
        [str(bt / "apksigner"), "verify", "--verbose", "--print-certs", str(signed)],
        text=True,
    )
    (out / "signer-readback.txt").write_text(certificate)
    signer = re.findall(
        r"^Signer #1 certificate SHA-256 digest: ([0-9a-f]{64})$", certificate, re.M
    )
    if len(signer) != 1:
        raise ValueError("Actual signer identity unavailable")
    badging = subprocess.check_output(
        [str(bt / "aapt"), "dump", "badging", str(signed)], text=True
    )
    (out / "manifest-badging.txt").write_text(badging)
    for required in [
        "targetSdkVersion:'36'",
        "sdkVersion:'29'",
        "package: name='" + provenance["application_id"] + "'",
    ]:
        if required not in badging:
            raise ValueError("Actual APK manifest identity drift")
    if boot_identity:
        from runtime.check_boot_identity_report import verify_package
    else:
        from verify_apk import verify_package

    package_readback = verify_package(
        signed, ROOT, args.parity_helper.resolve(), commit, aapt2=bt / "aapt2"
    )
    (out / "package-readback.json").write_text(
        json.dumps(package_readback, indent=2) + "\n"
    )
    if getattr(args, "packaged_shared_runtime", False):
        if "application-debuggable" in badging:
            raise ValueError("Packaged runtime actual manifest is debuggable")
    # The validator checks the actual packaged bytes, not merely the staging tree.
    extracted = out / "apk-readback"
    with zipfile.ZipFile(signed) as apk:
        apk.extractall(extracted)
    packaged_record = json.loads(
        (extracted / "assets/runtime/shared-source.json").read_text()
    )
    extracted_readback = module.check_apk_sources(
        ROOT, extracted / "assets/shared", packaged_record, commit
    )
    (out / "apk-extracted-readback.json").write_text(
        json.dumps(extracted_readback, indent=2) + "\n"
    )
    guest_readback = None
    if getattr(args, "packaged_shared_runtime", False):
        guest_readback = packaged_readback(extracted, provenance, packaged_record)
        (out / "packaged-runtime-readback.json").write_text(
            json.dumps(guest_readback, indent=2) + "\n"
        )
    if adapter_sources() != adapter_before:
        raise ValueError("Adapter sources changed during build; receipt refused")
    if helper_identity(args.parity_helper) != helper_before:
        raise ValueError("Helper changed during the build; acceptance refused")
    receipt = {
        "apk": str(signed),
        "apk_sha256": digest(signed),
        "signer_sha256": signer[0],
        "source_commit": commit,
        "shared_readback": "passed",
        "packaged_runtime_readback": guest_readback,
        "stack_acceptance": False,
        "native": provenance,
        "native_provenance_sha256": digest(
            out / "assets/runtime/native-provenance.json"
        ),
    }
    (out / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps({k: v for k, v in receipt.items() if k != "native"}, indent=2))


def validate_cache_owner(cache, boot_only=False):
    expected_owner = "apk266-boot-identity" if boot_only else "apk264"
    owner_path = cache / "lease.json"
    if owner_path.exists():
        owner = json.loads(owner_path.read_text())
        if owner.get("owner") != expected_owner or owner.get("workspace") != str(ROOT):
            raise ValueError("Cache belongs to another owner/workspace; preserving it")
    else:
        owner_path.write_text(
            json.dumps(
                {
                    "owner": expected_owner,
                    "workspace": str(ROOT),
                    "resource": expected_owner + ":private-sdk-ndk-cache",
                }
            )
            + "\n"
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cache", type=Path, default=Path.home() / ".cache/schemii-apk264"
    )
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser(
        "bootstrap", help="download/verify pinned inputs; accepts authorized SDK terms"
    )
    b = sub.add_parser("build")
    b.add_argument("--build-id", required=True)
    b.add_argument("--abi", action="append", choices=ABI_TRIPLES)
    b.add_argument("--guest-payload", type=Path, action="append")
    b.add_argument(
        "--packaged-shared-runtime",
        action="store_true",
        help="Anchored complete-per-selected-ABI static package; execution remains NOT_RUN",
    )
    b.add_argument(
        "--guest-manifest-sha256",
        action="append",
        help="Independent manifest anchor, in guest-payload argument order",
    )
    b.add_argument(
        "--native-donor",
        type=Path,
        help="Reuse exact committed base native binaries from this receipt",
    )
    b.add_argument(
        "--native-donor-receipt-sha256",
        help="Independently selected immutable donor receipt SHA256 (required for reuse)",
    )
    b.add_argument(
        "--integration-probes",
        action="store_true",
        help="Only reviewed13e subreaper + cde network callbacks; never guest/node",
    )
    b.add_argument("--subreaper-handoff", type=Path)
    b.add_argument("--subreaper-checkout", type=Path)
    b.add_argument("--network-handoff", type=Path)
    b.add_argument("--network-checkout", type=Path)
    b.add_argument("--process-head")
    b.add_argument("--process-checkout", type=Path)
    b.add_argument("--boot-identity-only", action="store_true")
    b.add_argument("--expected-adapter-commit")
    b.add_argument("--non-debuggable", action="store_true")
    b.add_argument("--log-report", action="store_true")
    b.add_argument(
        "--wrapper-controls-only",
        action="store_true",
        help="Run only finite collector fixtures; excludes backend/network/peer campaigns",
    )
    b.add_argument(
        "--parity-helper", type=Path, default=ROOT / "scripts/check_runtime_parity.py"
    )
    args = parser.parse_args()
    os.umask(0o077)
    cache = args.cache.resolve()
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "downloads").mkdir(exist_ok=True)
    validate_cache_owner(cache, getattr(args, "boot_identity_only", False))
    # Prevent parallel builds/bootstrap from racing private native inputs/signing.
    import fcntl

    with (cache / ".lock").open("w") as lease:
        fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.action == "bootstrap":
            bootstrap(cache)
        else:
            build(args, cache)


if __name__ == "__main__":
    main()
