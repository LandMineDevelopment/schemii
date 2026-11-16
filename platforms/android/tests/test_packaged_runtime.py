"""Static packaging refusals, never a guest execution or readiness claim."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import struct
import subprocess
from types import SimpleNamespace
import zipfile

import pytest

ANDROID = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


payload = load("packaged_payload", ANDROID / "runtime/build_payload.py")
build = load("packaged_builder", ANDROID / "build.py")


def elf(
    *, machine=62, needed=(), interpreter=None, search=None, alignment=4096, rpath=False
):
    """Synthetic ELF tables exercise static refusal, not actual runtime payloads."""
    data = bytearray(1024)
    data[:6] = b"\x7fELF\x02\x01"
    struct.pack_into("<HH", data, 16, 3, machine)
    struct.pack_into("<Q", data, 32, 64)
    struct.pack_into("<HH", data, 54, 56, 3 if interpreter else 2)
    struct.pack_into("<IIQQQQQQ", data, 64, 1, 5, 0, 0, 0, 1024, 1024, alignment)
    strings = bytearray(b"\0")
    entries = []
    for tag, value in [(1, n) for n in needed] + (
        [(15 if rpath else 29, search)] if search else []
    ):
        entries.append((tag, len(strings)))
        strings.extend(value.encode() + b"\0")
    entries += [(5, 512), (10, len(strings)), (0, 0)]
    struct.pack_into(
        "<IIQQQQQQ",
        data,
        120,
        2,
        4,
        256,
        256,
        0,
        len(entries) * 16,
        len(entries) * 16,
        8,
    )
    for index, (tag, value) in enumerate(entries):
        struct.pack_into("<qQ", data, 256 + index * 16, tag, value)
    data[512 : 512 + len(strings)] = strings
    if interpreter:
        value = interpreter.encode() + b"\0"
        data[768 : 768 + len(value)] = value
        struct.pack_into(
            "<IIQQQQQQ", data, 176, 3, 4, 768, 768, 0, len(value), len(value), 1
        )
    return bytes(data)


def fixture(tmp_path, changes=None):
    names = [
        "usr/lib/postgresql/17/bin/" + name
        for name in ("initdb", "postgres", "pg_ctl", "psql")
    ]
    names += ["usr/bin/python3.13", "opt/node/bin/node"]
    data = dict.fromkeys(names, elf())
    data.update(changes or {})
    files = {}
    archive = tmp_path / "guest.zip"
    with zipfile.ZipFile(archive, "w") as output:
        for name, value in data.items():
            for parent in reversed(Path(name).parents):
                if str(parent) != ".":
                    files[parent.as_posix()] = {"kind": "directory", "mode": 0o755}
            if value is None:
                continue
            info = zipfile.ZipInfo(name)
            info.external_attr = (stat.S_IFREG | 0o555) << 16
            output.writestr(info, value)
            files[name] = {
                "kind": "file",
                "mode": 0o555,
                "size": len(value),
                "sha256": hashlib.sha256(value).hexdigest(),
            }
    return archive, files


def test_actual_abi_and_page_scope():
    assert payload.guest_elf(elf(), "x86_64")["load_alignment"] == 4096
    assert (
        payload.guest_elf(elf(machine=183, alignment=16384), "arm64-v8a")["machine"]
        == 183
    )
    for data, abi, reason in [
        (elf(machine=183), "x86_64", "ABI"),
        (elf(), "arm64-v8a", "ABI"),
        (elf(machine=183), "arm64-v8a", "16KiB"),
        (elf()[:120], "x86_64", "headers"),
    ]:
        with pytest.raises(ValueError, match=reason):
            payload.guest_elf(data, abi)


def test_complete_archive_and_native_dependency_closure(tmp_path):
    archive, files = fixture(
        tmp_path,
        {
            "opt/node/bin/node": elf(
                needed=("libexample.so",),
                interpreter="/lib/ld.so",
                search="$ORIGIN/../lib",
            ),
            "opt/node/lib/libexample.so": elf(),
            "usr/lib/ld.so": elf(),
        },
    )
    files["lib"] = {"kind": "symlink", "target": "usr/lib"}
    result = payload.inspect_guest(archive, files, "x86_64")
    assert result["opt/node/bin/node"]["resolved_needed"] == {
        "libexample.so": "opt/node/lib/libexample.so"
    }
    files.pop("usr/lib/ld.so")
    with pytest.raises(ValueError, match="inventory"):
        payload.inspect_guest(archive, files, "x86_64")


@pytest.mark.parametrize(
    "changes,reason",
    [
        (
            {"opt/node/bin/node": elf(needed=("libabsent.so",))},
            "Missing guest native dependency",
        ),
        ({"opt/node/bin/node": elf(interpreter="/lib/absent.so")}, "interpreter"),
        ({"opt/node/bin/node": elf(machine=183)}, "ABI"),
        ({"usr/bin/python3.13": None}, "actual guest executable"),
        ({"usr/lib/postgresql/17/bin/initdb": None}, "actual guest executable"),
        ({"opt/node/bin/node": elf(search="relative/path")}, "search path"),
        ({"opt/node/bin/node": elf(needed=("/host/lib.so",))}, "dependency path"),
    ],
)
def test_wrong_abi_missing_native_or_executable_refused(tmp_path, changes, reason):
    archive, files = fixture(tmp_path, changes)
    with pytest.raises(ValueError, match=reason):
        payload.inspect_guest(archive, files, "x86_64")


def test_byte_mode_inventory_and_link_escape_refusals(tmp_path):
    archive, files = fixture(tmp_path)
    original = json.loads(json.dumps(files))
    for entry in [{"sha256": "0" * 64}, {"size": 1}, {"mode": 0o777}]:
        files = json.loads(json.dumps(original))
        files["opt/node/bin/node"].update(entry)
        with pytest.raises(ValueError, match="drift"):
            payload.inspect_guest(archive, files, "x86_64")
    for name, target in [("escape", "../../host"), ("cycle", "cycle")]:
        files = {**original, name: {"kind": "symlink", "target": target}}
        with pytest.raises(ValueError, match="escapes|cycle"):
            payload.inspect_guest(archive, files, "x86_64")
    files = {**original, "../escape": {"kind": "directory"}}
    with pytest.raises(ValueError, match="Unsafe"):
        payload.inspect_guest(archive, files, "x86_64")
    with zipfile.ZipFile(archive, "a") as output:
        output.writestr("unlisted", b"extra")
    with pytest.raises(ValueError, match="inventory"):
        payload.inspect_guest(archive, original, "x86_64")


def test_independent_anchor_and_source_dependency_binding(tmp_path):
    lock, record = payload.payload_record("x86_64", True)
    data = {"usr/sbin/nginx": elf()}
    for pin in record["wheels"]:
        data["opt/python-packages/" + pin["package"] + ".dist-info/METADATA"] = (
            "Name: " + pin["package"] + "\nVersion: " + pin["version"] + "\n"
        ).encode()
    node_lock = json.loads(
        (payload.ROOT / "ai/prototype/package-lock.json").read_text()
    )
    for name, pin in node_lock["packages"].items():
        if name and not pin.get("dev"):
            data[name + "/package.json"] = json.dumps(
                {"name": name.removeprefix("node_modules/"), "version": pin["version"]}
            ).encode()
    data["opt/schemii-venv/pyvenv.cfg"] = b"home = /usr/bin\n"
    data["opt/schemii-venv/bin/placeholder"] = b""
    data["opt/schemii-venv/lib/python3.13/placeholder"] = b""
    archive, files = fixture(tmp_path, data)
    files["opt/schemii-venv/bin/python"] = {
        "kind": "symlink",
        "target": "/usr/bin/python3.13",
    }
    files["opt/schemii-venv/lib/python3.13/site-packages"] = {
        "kind": "symlink",
        "target": "/opt/python-packages",
    }
    import sys

    sys.path.insert(0, str(payload.ROOT / "scripts"))
    from check_runtime_parity import apk_source_manifest, apk_runtime_source_manifest

    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    shared = apk_source_manifest(payload.ROOT, commit)
    manifest = {
        "profile": "packaged-shared-runtime",
        "runtime_source": apk_runtime_source_manifest(payload.ROOT, commit),
        "abi": "x86_64",
        "producer_sha256": payload.sha(Path(payload.__file__)),
        "producer_inputs": payload.producer_inputs(),
        "contract_sha256": lock["contract_sha256"],
        "payload_lock_sha256": payload.sha(ANDROID / "runtime/payload.lock.json"),
        "dependencies": {
            key: record[key] for key in ("base", "packages", "wheels", "node")
        },
        "shared_source": shared,
        "archive_sha256": payload.sha(archive),
        "files": files,
        "elf_inventory": payload.inspect_guest(archive, files, "x86_64"),
        "installed_dependencies": payload.inspect_dependencies(archive, files, record),
    }
    path = tmp_path / "guest-manifest.json"

    def write(value):
        path.write_text(json.dumps(value))
        return payload.sha(path)

    for name in (
        "opt/python-packages/psycopg.dist-info/METADATA",
        "node_modules/@earendil-works/pi-ai/package.json",
        "opt/schemii-venv/bin/python",
    ):
        without = {key: value for key, value in files.items() if key != name}
        with pytest.raises(ValueError, match="inventory drift|Missing"):
            payload.inspect_dependencies(archive, without, record)
    anchor = write(manifest)
    assert payload.verify_payload(tmp_path, "x86_64", shared, anchor) == manifest
    for bad_anchor in (None, "", "0" * 64):
        with pytest.raises(ValueError, match="anchor"):
            payload.verify_payload(tmp_path, "x86_64", shared, bad_anchor)
    for key in (
        "abi",
        "producer_sha256",
        "producer_inputs",
        "payload_lock_sha256",
        "contract_sha256",
        "dependencies",
        "shared_source",
        "runtime_source",
        "elf_inventory",
        "installed_dependencies",
    ):
        mutated = {**manifest, key: None}
        rebound = write(mutated)
        with pytest.raises(ValueError, match="anchor"):
            payload.verify_payload(tmp_path, "x86_64", shared, anchor)
        with pytest.raises(ValueError, match="drift"):
            payload.verify_payload(tmp_path, "x86_64", shared, rebound)


def test_packaged_profile_requires_complete_selected_abi_and_clean_exact_source():
    args = dict(
        packaged_shared_runtime=True,
        guest_payload=[Path("payload")],
        guest_manifest_sha256=["a" * 64],
        abi=["x86_64"],
        non_debuggable=True,
        expected_adapter_commit="wrong",
        native_donor=Path("donor"),
    )
    for key, value in [
        ("abi", ["x86_64", "arm64-v8a"]),
        ("guest_payload", []),
        ("guest_manifest_sha256", []),
        ("non_debuggable", False),
        ("native_donor", None),
        ("expected_adapter_commit", None),
    ]:
        with pytest.raises(ValueError, match="requires"):
            build.packaged_admission(SimpleNamespace(**{**args, key: value}))
    for key in (
        "boot_identity_only",
        "integration_probes",
        "network_handoff",
        "process_head",
        "wrapper_controls_only",
    ):
        with pytest.raises(ValueError, match="excludes"):
            build.packaged_admission(SimpleNamespace(**args, **{key: True}))
    with pytest.raises(ValueError, match="exact committed"):
        build.packaged_admission(SimpleNamespace(**args))
    with pytest.raises(ValueError, match="anchors require"):
        build.packaged_admission(
            SimpleNamespace(
                packaged_shared_runtime=False, guest_manifest_sha256=["a" * 64]
            )
        )


@pytest.fixture
def committed_adapter(tmp_path, monkeypatch):
    root = tmp_path / "source"
    android = root / "platforms/android"
    java = android / "java/ProbeActivity.java"
    java.parent.mkdir(parents=True)
    java.write_text("class ProbeActivity {}\n")
    (android / "AndroidManifest.xml").write_text("<manifest />\n")
    (root / ".gitignore").write_text(
        "**/__pycache__/\nplatforms/android/java/ignored/\n"
    )

    def git(*args):
        return subprocess.check_output(
            [
                "git",
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "commit.gpgsign=false",
                "-c",
                "user.name=Admission fixture",
                "-c",
                "user.email=fixture@example.invalid",
                "-C",
                str(root),
                *args,
            ],
            text=True,
        ).strip()

    git("init", "-q")
    git("add", ".")
    git("commit", "-qm", "Committed admission fixture")
    args = SimpleNamespace(
        packaged_shared_runtime=True,
        guest_payload=[Path("not-opened-payload")],
        guest_manifest_sha256=["a" * 64],
        abi=["x86_64"],
        non_debuggable=True,
        expected_adapter_commit=git("rev-parse", "HEAD"),
        native_donor=Path("not-opened-donor"),
    )

    def forbidden(*args, **kwargs):
        pytest.fail("Admission must not perform SDK, native or cache operations")

    monkeypatch.setattr(build, "ROOT", root)
    monkeypatch.setattr(build, "HERE", android)
    monkeypatch.setattr(build, "run", forbidden)
    monkeypatch.setattr(build, "fetch", forbidden)
    return root, android, args, git


@pytest.mark.parametrize("flag", ["--assume-unchanged", "--skip-worktree"])
@pytest.mark.parametrize("name", ["java/ProbeActivity.java", "AndroidManifest.xml"])
def test_packaged_admission_rejects_hidden_adapter_bytes(committed_adapter, flag, name):
    root, android, args, git = committed_adapter
    git("update-index", flag, "platforms/android/" + name)
    (android / name).write_text("changed but hidden by the Git index\n")
    assert git("status", "--porcelain") == ""
    with pytest.raises(ValueError, match="committed adapter"):
        build.packaged_admission(args)
    assert not (root / "cache").exists()


def test_packaged_admission_accepts_exact_committed_inputs(committed_adapter):
    _, android, args, git = committed_adapter
    generated = android / "__pycache__/unused.cpython-314.pyc"
    generated.parent.mkdir()
    generated.write_bytes(b"unconsumed interpreter cache")
    assert git("status", "--porcelain") == ""
    assert build.packaged_admission(args) == build.adapter_sources()


@pytest.mark.parametrize(
    "mutation", ["missing", "mode", "symlink", "fifo", "ignored_java", "cached_java"]
)
def test_packaged_admission_rejects_hidden_inventory_mode_and_links(
    committed_adapter, mutation
):
    root, android, args, git = committed_adapter
    java = android / "java/ProbeActivity.java"
    git(
        "update-index",
        "--assume-unchanged",
        "platforms/android/java/ProbeActivity.java",
    )
    if mutation == "missing":
        java.unlink()
    elif mutation == "mode":
        java.chmod(0o755)
    elif mutation in ["symlink", "fifo"]:
        data = java.read_bytes()
        java.unlink()
        if mutation == "symlink":
            target = root.parent / "retained-java"
            target.write_bytes(data)
            java.symlink_to(target)
        else:
            os.mkfifo(java, 0o600)
    else:
        extra = java.parent / (
            "ignored/Extra.java"
            if mutation == "ignored_java"
            else "__pycache__/Extra.java"
        )
        extra.parent.mkdir()
        extra.write_text("class Extra {}\n")
    assert git("status", "--porcelain") == ""
    with pytest.raises(ValueError, match="committed adapter"):
        build.packaged_admission(args)


def test_packaged_cli_rejects_hidden_source_before_cache_creation(
    committed_adapter, monkeypatch
):
    root, android, args, git = committed_adapter
    git("update-index", "--skip-worktree", "platforms/android/AndroidManifest.xml")
    (android / "AndroidManifest.xml").write_text("hidden manifest change\n")
    cache = root.parent / "never-created-cache"
    monkeypatch.setattr(
        build.sys,
        "argv",
        [
            "build.py",
            "--cache",
            str(cache),
            "build",
            "--build-id",
            "refused",
            "--abi",
            "x86_64",
            "--guest-payload",
            "not-opened-payload",
            "--guest-manifest-sha256",
            "a" * 64,
            "--packaged-shared-runtime",
            "--non-debuggable",
            "--expected-adapter-commit",
            args.expected_adapter_commit,
            "--native-donor",
            "not-opened-donor",
            "--native-donor-receipt-sha256",
            "b" * 64,
        ],
    )
    previous = os.umask(0o077)
    try:
        with pytest.raises(ValueError, match="committed adapter"):
            build.main()
    finally:
        os.umask(previous)
    assert not cache.exists()


@pytest.mark.parametrize("inherited", [True, False])
def test_only_rpath_propagates_to_bundled_transitive_libraries(tmp_path, inherited):
    archive, files = fixture(
        tmp_path,
        {
            "opt/node/bin/node": elf(
                needed=("liba.so",), search="$ORIGIN/../lib", rpath=inherited
            ),
            "opt/node/lib/liba.so": elf(needed=("libb.so",)),
            "opt/node/lib/libb.so": elf(),
        },
    )
    if inherited:
        result = payload.inspect_guest(archive, files, "x86_64")
        assert result["opt/node/lib/liba.so"]["resolved_needed"] == {
            "libb.so": "opt/node/lib/libb.so"
        }
    else:
        with pytest.raises(ValueError, match="Missing guest native dependency"):
            payload.inspect_guest(archive, files, "x86_64")


def test_unrelated_rpath_cannot_repair_broken_launch_root(tmp_path):
    archive, files = fixture(
        tmp_path,
        {
            "opt/node/bin/node": elf(needed=("liba.so",)),
            "usr/lib/liba.so": elf(needed=("libb.so",)),
            "opt/bundle/lib/libb.so": elf(),
            "opt/other/bin/other": elf(
                needed=("liba.so",), search="/opt/bundle/lib", rpath=True
            ),
        },
    )
    with pytest.raises(ValueError, match="Missing guest native dependency"):
        payload.inspect_guest(archive, files, "x86_64")


def test_frozen_nginx_closure_keeps_existing_diagnostic_pins():
    lock, diagnostic = payload.payload_record("x86_64")
    _, packaged = payload.payload_record("x86_64", True)
    original = lock["abi"]["x86_64"]["packages"]
    assert diagnostic["packages"] == original
    assert packaged["packages"][: len(original)] == original
    extras = packaged["packages"][len(original) :]
    assert {p["package"] for p in extras} == {
        "nginx",
        "nginx-common",
        "iproute2",
        "libbpf1",
        "libelf1t64",
        "libmnl0",
        "libtirpc3t64",
        "libtirpc-common",
        "libxtables12",
        "libcap2-bin",
    }
    assert (
        next(p for p in extras if p["package"] == "nginx")["version"]
        == "1.26.3-3+deb13u10"
    )


@pytest.mark.parametrize("mutation", ["nginx", "origin", "release", "abi"])
def test_frozen_nginx_dependency_provenance_is_required(
    tmp_path, monkeypatch, mutation
):
    lock = json.loads((payload.HERE / "payload.lock.json").read_text())
    extra = lock["abi"]["x86_64"]["packaged_runtime_packages"]
    if mutation == "nginx":
        lock["abi"]["x86_64"]["packaged_runtime_packages"] = [
            p for p in extra if p["package"] != "nginx"
        ]
    elif mutation == "origin":
        extra[0]["url"] = "https://unreviewed.invalid/package.deb"
    elif mutation == "release":
        for index in lock["packaged_runtime_indexes"]:
            index["release_sha256"] = "0" * 64
    else:
        extra[0]["arch"] = "arm64"
    (tmp_path / "payload.lock.json").write_text(json.dumps(lock))
    monkeypatch.setattr(payload, "HERE", tmp_path)
    with pytest.raises(ValueError, match="nginx closure|provenance drift|ABI drift"):
        payload.payload_record("x86_64", True)
