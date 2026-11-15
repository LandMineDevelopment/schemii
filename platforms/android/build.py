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
import shutil
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


def reuse_native(donor_path, out, abis):
    """Reuse only exact base executables; no peer component or acceptance inheritance."""
    from verify_apk import elf_identity

    raw = donor_path.read_bytes()
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


def adapter_sources():
    return {
        str(p.relative_to(ROOT)): digest(p)
        for p in sorted(HERE.rglob("*"))
        if p.is_file() and "__pycache__" not in p.parts
    }


def build(args, cache):
    if not args.build_id or any(
        c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
        for c in args.build_id
    ):
        raise ValueError("Build ID must be a single safe name")
    if args.wrapper_controls_only and (
        args.guest_payload or args.network_handoff or args.process_head
    ):
        raise ValueError(
            "Wrapper fixture mode cannot include guest, network or peer process campaigns"
        )
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
        artifacts, native_donor = reuse_native(args.native_donor, out, abis)
    else:
        for abi in abis:
            artifacts.update(native(cache, out, abi))
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
        "application_id": "development.landmine.schemii.runtimeprobe",
        "abis": abis,
        "peer_components": peer_components,
        "debuggable": not args.non_debuggable,
        "diagnostic_log_export": args.log_report,
        "wrapper_controls_only": args.wrapper_controls_only,
        "wrapper_fixture": fixture,
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
            expected_inputs = [
                "platforms/android/runtime/build_payload.py",
                "platforms/android/runtime/payload.lock.json",
                "platforms/android/native/runtime/guest_compat.c",
                "dev/runtime-contract.json",
                "ai/prototype/package.json",
                "ai/prototype/package-lock.json",
            ]
            if manifest.get("producer_inputs") != {
                name: digest(ROOT / name) for name in expected_inputs
            }:
                raise ValueError("Guest producer/dependency input drift")
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
    run(bt / "apksigner", "verify", "--verbose", signed)
    badging = subprocess.check_output(
        [str(bt / "aapt"), "dump", "badging", str(signed)], text=True
    )
    (out / "manifest-badging.txt").write_text(badging)
    for required in [
        "targetSdkVersion:'36'",
        "sdkVersion:'29'",
        "package: name='development.landmine.schemii.runtimeprobe'",
    ]:
        if required not in badging:
            raise ValueError("Actual APK manifest identity drift")
    from verify_apk import verify_package

    package_readback = verify_package(
        signed, ROOT, args.parity_helper.resolve(), commit
    )
    (out / "package-readback.json").write_text(
        json.dumps(package_readback, indent=2) + "\n"
    )
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
    if adapter_sources() != adapter_before:
        raise ValueError("Adapter sources changed during build; receipt refused")
    if helper_identity(args.parity_helper) != helper_before:
        raise ValueError("Helper changed during the build; acceptance refused")
    receipt = {
        "apk": str(signed),
        "apk_sha256": digest(signed),
        "source_commit": commit,
        "shared_readback": "passed",
        "stack_acceptance": False,
        "native": provenance,
        "native_provenance_sha256": digest(
            out / "assets/runtime/native-provenance.json"
        ),
    }
    (out / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps({k: v for k, v in receipt.items() if k != "native"}, indent=2))


def validate_cache_owner(cache):
    owner_path = cache / "lease.json"
    if owner_path.exists():
        owner = json.loads(owner_path.read_text())
        if owner.get("owner") != "apk264" or owner.get("workspace") != str(ROOT):
            raise ValueError("Cache belongs to another owner/workspace; preserving it")
    else:
        owner_path.write_text(
            json.dumps(
                {
                    "owner": "apk264",
                    "workspace": str(ROOT),
                    "resource": "apk264:private-sdk-ndk-cache",
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
        "--native-donor",
        type=Path,
        help="Reuse exact committed base native binaries from this receipt",
    )
    b.add_argument("--network-handoff", type=Path)
    b.add_argument("--network-checkout", type=Path)
    b.add_argument("--process-head")
    b.add_argument("--process-checkout", type=Path)
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
    validate_cache_owner(cache)
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
