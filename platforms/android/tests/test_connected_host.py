"""Actual installed-source Java extractor controls; no Android execution."""

from __future__ import annotations
import hashlib
import importlib.util
import json
from pathlib import Path
import stat
import subprocess
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[3]
JAVA = (
    ROOT
    / "platforms/android/app/src/main/java/development/landmine/schemii/android/host/InstalledSource.java"
)

# Compile-only dependencies of InstalledSource.framework. This fixture runs the
# complete production compose implementation, never the framework observation
# edge; every callable double fails loudly if that boundary is crossed.
FRAMEWORK_COMPILE_STUBS = {
    "android/content/Context.java": """package android.content;
public class Context {
 public Context getApplicationContext(){throw new AssertionError("framework not exercised");}
 public ClassLoader getClassLoader(){throw new AssertionError("framework not exercised");}
 public String getPackageName(){throw new AssertionError("framework not exercised");}
 public android.content.pm.PackageManager getPackageManager(){throw new AssertionError("framework not exercised");}
 public android.content.pm.ApplicationInfo getApplicationInfo(){throw new AssertionError("framework not exercised");}
 public java.io.File getDataDir(){throw new AssertionError("framework not exercised");}
}""",
    "android/content/pm/ApplicationInfo.java": """package android.content.pm;
public class ApplicationInfo {
 public static final int FLAG_DEBUGGABLE=2;
 public int targetSdkVersion, uid, flags;
 public String packageName,className,appComponentFactory,processName,sourceDir,publicSourceDir,nativeLibraryDir,dataDir;
 public String[] splitSourceDirs,splitPublicSourceDirs,sharedLibraryFiles;
}""",
    "android/content/pm/PackageInfo.java": """package android.content.pm;
public class PackageInfo {
 public ApplicationInfo applicationInfo;
 public String packageName;
 public String[] splitNames;
 public Object[] providers,receivers,instrumentation;
 public ActivityInfo[] activities;
 public ServiceInfo[] services;
 public SigningInfo signingInfo;
}""",
    "android/content/pm/PackageManager.java": """package android.content.pm;
public class PackageManager {
 public static final int GET_SIGNING_CERTIFICATES=1,GET_ACTIVITIES=2,GET_SERVICES=4,
 GET_PROVIDERS=8,GET_RECEIVERS=16,GET_INSTRUMENTATION=32,GET_SHARED_LIBRARY_FILES=64;
 public PackageInfo getPackageInfo(String name,int flags){throw new AssertionError("framework not exercised");}
}""",
    "android/content/pm/ActivityInfo.java": """package android.content.pm;
public class ActivityInfo {public String name,processName; public boolean exported;}""",
    "android/content/pm/ServiceInfo.java": """package android.content.pm;
public class ServiceInfo {public String name,processName; public boolean exported;}""",
    "android/content/pm/SigningInfo.java": """package android.content.pm;
public class SigningInfo {
 public boolean hasMultipleSigners(){throw new AssertionError("framework not exercised");}
 public Signature[] getApkContentsSigners(){throw new AssertionError("framework not exercised");}
}""",
    "android/content/pm/Signature.java": """package android.content.pm;
public class Signature {public byte[] toByteArray(){throw new AssertionError("framework not exercised");}}""",
    "android/os/Process.java": """package android.os;
public class Process {public static int myUid(){throw new AssertionError("framework not exercised");}}""",
    "development/landmine/schemii/android/host/StorageProfile.java": """package development.landmine.schemii.android.host;
public class StorageProfile {
 public static void select(android.content.Context app,boolean de){throw new AssertionError("framework not exercised");}
}""",
    **{
        "development/landmine/schemii/android/host/" + name + ".java": (
            "package development.landmine.schemii.android.host; public class "
            + name
            + " {}"
        )
        for name in ("HostActivity", "HostService", "NativeOwner")
    },
}


@pytest.fixture(scope="module")
def java(tmp_path_factory):
    out = tmp_path_factory.mktemp("connected-java")
    harness = out / "Extract.java"
    harness.write_text("""import java.io.*; import java.util.*;
import development.landmine.schemii.android.host.InstalledSource;
public class Extract {
 static Map<String,String> read(String path) throws Exception {
  Properties p=new Properties(); try(InputStream in=new FileInputStream(path)){ p.load(in); }
  Map<String,String> m=new TreeMap<>(); for(String k:p.stringPropertyNames())m.put(k,p.getProperty(k)); return m;
 }
 public static void main(String[] args) throws Exception {
  InstalledSource.compose(new File(args[0]),new File(args[1]),read(args[2]),read(args[3]));
 }
}""")
    dependencies = []
    for name, content in FRAMEWORK_COMPILE_STUBS.items():
        path = out / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        dependencies.append(str(path))
    subprocess.run(
        ["javac", "-d", str(out), *dependencies, str(JAVA), str(harness)], check=True
    )
    return out


def package(tmp_path, changes=None, *, real=False):
    files = {
        "dev/runtime-contract.json": b"public contract",
        "dev/android/runtime.py": b"shared Runtime",
        "src/app.py": b"shared API",
    }
    if real:
        spec = importlib.util.spec_from_file_location(
            "parity", ROOT / "scripts/check_runtime_parity.py"
        )
        parity = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(parity)
        head = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip()
        record = parity.apk_runtime_source_manifest(ROOT, head)
        files = {name: (ROOT / name).read_bytes() for name in record["source_files"]}
    source = {n: hashlib.sha256(b).hexdigest() for n, b in files.items()}
    entries = {
        (
            "assets/runtime-source/"
            if n.startswith("dev/android/") or n == "dev/schemii.toml"
            else "assets/shared/"
        )
        + n: (b, stat.S_IFREG | 0o644)
        for n, b in files.items()
    }
    # Real control includes actual source records; native/payload are explicitly
    # synthetic manifest bytes here, never previous binary acceptance donors.
    support = {
        "assets/runtime/packaged-source.json": json.dumps(source).encode(),
        "assets/runtime/native-provenance.json": b"native anchor",
        "assets/runtime/guest-manifest.json": b"payload anchor",
        "lib/x86_64/libschemii_owner.so": b"synthetic native",
    }
    assets = {n: hashlib.sha256(b).hexdigest() for n, b in support.items()}
    entries.update({n: (b, stat.S_IFREG | 0o644) for n, b in support.items()})
    if changes:
        changes(entries)
    apk = tmp_path / "input.apk"
    with zipfile.ZipFile(apk, "w", compression=zipfile.ZIP_DEFLATED) as output:
        for name, (data, mode) in entries.items():
            info = zipfile.ZipInfo(name)
            info.external_attr = mode << 16
            output.writestr(info, data)
    for name, values in [("source", source), ("assets", assets)]:
        (tmp_path / name).write_text(
            "".join(n + "=" + sha + "\n" for n, sha in values.items())
        )
    return apk, files


def execute(java, tmp_path, apk):
    return subprocess.run(
        [
            "java",
            "-cp",
            str(java),
            "Extract",
            str(apk),
            str(tmp_path / "shared"),
            str(tmp_path / "source"),
            str(tmp_path / "assets"),
        ],
        capture_output=True,
        text=True,
        timeout=20,
    )


def test_installed_composition_reads_back_exact_committed_inventory(java, tmp_path):
    apk, files = package(tmp_path, real=True)
    result = execute(java, tmp_path, apk)
    assert result.returncode == 0, result.stderr
    actual = {
        p.relative_to(tmp_path / "shared").as_posix(): p.read_bytes()
        for p in (tmp_path / "shared").rglob("*")
        if p.is_file()
    }
    assert actual == files
    for p in (tmp_path / "shared").rglob("*"):
        assert not p.stat().st_mode & 0o222


@pytest.mark.parametrize(
    "fault",
    [
        "unknown",
        "missing",
        "changed",
        "traversal",
        "alias",
        "case",
        "symlink",
        "special",
        "private",
        "manifest",
        "native",
        "payload",
    ],
)
def test_actual_extractor_refuses_before_publication(java, tmp_path, fault):
    key = "assets/shared/src/app.py"

    def change(entries):
        if fault == "unknown":
            entries["assets/shared/unknown.py"] = (b"x", stat.S_IFREG | 0o644)
        elif fault == "missing":
            entries.pop(key)
        elif fault == "changed":
            entries[key] = (b"changed", stat.S_IFREG | 0o644)
        elif fault == "traversal":
            entries["assets/shared/../escape"] = (b"x", stat.S_IFREG | 0o644)
        elif fault == "alias":
            entries["assets/shared/src/./app.py"] = entries[key]
        elif fault == "case":
            entries["assets/shared/src/App.py"] = entries[key]
        elif fault == "symlink":
            entries[key] = (b"../../secret", stat.S_IFLNK | 0o777)
        elif fault == "special":
            entries[key] = (b"", stat.S_IFIFO | 0o600)
        elif fault == "private":
            entries["assets/shared/.schemii/secrets/key"] = (
                b"PRIVATE_SENTINEL",
                stat.S_IFREG | 0o600,
            )
        elif fault == "manifest":
            entries["assets/runtime/packaged-source.json"] = (
                b"{}",
                stat.S_IFREG | 0o644,
            )
        elif fault == "native":
            entries["lib/x86_64/libschemii_owner.so"] = (
                b"changed",
                stat.S_IFREG | 0o755,
            )
        else:
            entries["assets/runtime/guest-manifest.json"] = (
                b"changed",
                stat.S_IFREG | 0o644,
            )

    apk, _ = package(tmp_path, change)
    result = execute(java, tmp_path, apk)
    assert result.returncode != 0
    assert not (tmp_path / "shared").exists()
    assert "PRIVATE_SENTINEL" not in result.stderr


def test_duplicate_zip_refused(java, tmp_path):
    apk, _ = package(tmp_path)
    with zipfile.ZipFile(apk, "a") as output:
        with pytest.warns(UserWarning):
            output.writestr("assets/shared/src/app.py", b"shared API")
    assert execute(java, tmp_path, apk).returncode != 0
    assert not (tmp_path / "shared").exists()


def test_retained_or_linked_destination_is_never_reused(java, tmp_path):
    apk, _ = package(tmp_path)
    (tmp_path / "shared").symlink_to(tmp_path / "source")
    assert execute(java, tmp_path, apk).returncode != 0
    assert (tmp_path / "shared").is_symlink()


def test_actual_build_generates_independent_java_anchors(java, tmp_path):
    spec = importlib.util.spec_from_file_location(
        "connected_builder", ROOT / "platforms/android/build.py"
    )
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    for name in (
        "assets/runtime/native-provenance.json",
        "lib/x86_64/libschemii_owner.so",
    ):
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"anchored-input")
    record = {"commit": "a" * 40, "source_files": {"dev/android/runtime.py": "b" * 64}}
    generated = builder.connected_anchors(tmp_path, record, "example.package", None)
    harness = tmp_path / "AnchorCheck.java"
    harness.write_text("""import development.landmine.schemii.android.host.HostAnchors;
public class AnchorCheck { public static void main(String[] args) {
 if (!HostAnchors.COMMIT.equals("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa") ||
     !HostAnchors.SIGNER.equals("") || HostAnchors.ASSETS.size()!=2 ||
     !HostAnchors.SOURCE.get("dev/android/runtime.py").equals(new String(new char[64]).replace('\\0','b')))
     throw new IllegalStateException("Compiled anchor drift");
} }""")
    subprocess.run(
        ["javac", "-d", str(tmp_path), str(generated), str(harness)], check=True
    )
    subprocess.run(["java", "-cp", str(tmp_path), "AnchorCheck"], check=True)
    with pytest.raises(ValueError, match="public SHA256"):
        builder.connected_anchors(tmp_path, record, "example.package", "untrusted")
