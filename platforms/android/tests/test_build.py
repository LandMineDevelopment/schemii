"""Focused controls for tool input and native package inspection, not runtime acceptance."""

import hashlib
import json
import importlib.util
from pathlib import Path
import stat
import struct
import tempfile
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch
import unittest
import zipfile

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))


def load(name):
    spec = importlib.util.spec_from_file_location(name, HERE / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build = load("build")
verify = load("verify_apk")


def elf(machine=183, alignment=16384, flags=5):
    result = bytearray(120)
    result[:6] = b"\x7fELF\x02\x01"
    struct.pack_into("<HH", result, 16, 3, machine)
    struct.pack_into("<Q", result, 32, 64)
    struct.pack_into("<HH", result, 54, 56, 1)
    struct.pack_into("<IIQQQQQQ", result, 64, 1, flags, 0, 0, 0, 120, 120, alignment)
    return bytes(result)


HISTORICAL_NATIVE = "906fc49667a4ac103d403e376c061fde110cf9c5"


def synthetic_native_donor(root, commit):
    """Immutable Git inputs plus synthetic ELF bytes; never runtime provenance.

    The production guard reads the actual committed builder itself. No mocked
    source lookup, recipe comparison, hash guard or ELF inspection is used.
    """

    def git_bytes(name):
        return subprocess.check_output(
            [
                "git",
                "-C",
                str(build.ROOT),
                "show",
                commit + ":platforms/android/" + name,
            ]
        )

    inputs = [
        "build.py",
        "native/runtime_probe.c",
        "native/replace.h",
        "native/proot-string-header.patch",
    ]
    if commit != HISTORICAL_NATIVE:
        inputs += [
            "native/proot-actor-channel.patch",
            "native/host/actor_channel.c",
            "native/host/actor_channel.h",
            "native/host/actor-channel-v1.json",
        ]
    native = {
        "adapter_dirty": False,
        "compile_sdk": 36,
        "target_sdk": 36,
        "min_sdk": 29,
        "toolchain_lock_sha256": hashlib.sha256(
            git_bytes("toolchain.lock.json")
        ).hexdigest(),
        "source_commit": commit,
        "abis": ["arm64-v8a"],
        "native_files": {},
        "adapter_sources": {
            "platforms/android/" + name: hashlib.sha256(git_bytes(name)).hexdigest()
            for name in inputs
        },
    }
    files = {
        "lib/arm64-v8a/" + name: elf()
        for name in ["libproot.so", "libproot_loader.so", "libruntime_probe.so"]
    }
    apk, receipt = root / "donor.apk", root / "receipt.json"

    def rebind():
        native["native_files"] = {
            k: hashlib.sha256(v).hexdigest() for k, v in files.items()
        }
        encoded = json.dumps(native).encode()
        with zipfile.ZipFile(apk, "w") as z:
            z.writestr("assets/runtime/native-provenance.json", encoded)
            for k, v in files.items():
                z.writestr(k, v)
        receipt.write_text(
            json.dumps(
                {
                    "apk": str(apk),
                    "apk_sha256": build.digest(apk),
                    "native_provenance_sha256": hashlib.sha256(encoded).hexdigest(),
                    "native": native,
                }
            )
        )

    rebind()
    return native, files, apk, receipt, rebind


class BuildControls(unittest.TestCase):
    def test_native_reuse_recipe_binds_flags_and_abi_mapping(self):
        source = "ABI_TRIPLES = {'x': 'triple'}\ndef run(): pass\ndef digest(): pass\ndef native(cache, out, abi):\n return ['-fPIE']\n"
        self.assertEqual(
            build.native_recipe(source),
            build.native_recipe(source + "\n# unrelated comment\n"),
        )
        self.assertNotEqual(
            build.native_recipe(source),
            build.native_recipe(source.replace("-fPIE", "-fPIC")),
        )
        self.assertNotEqual(
            build.native_recipe(source),
            build.native_recipe(source.replace("triple", "other")),
        )
        self.assertNotEqual(
            build.native_recipe(source),
            build.native_recipe(
                source.replace("def run(): pass", "def run(): return 1")
            ),
        )

        managed = (HERE / "build.py").read_text()
        for before, after in [("--fuzz=0", "--fuzz=1"), ("return lock", "return {}")]:
            self.assertIn(before, managed)
            self.assertNotEqual(
                build.native_recipe(managed),
                build.native_recipe(managed.replace(before, after)),
            )

    def test_native_reuse_requires_unambiguous_recipe(self):
        for source in [
            "ABI_TRIPLES = {}",
            "def native(): pass",
            "ABI_TRIPLES={}\ndef native(): pass\ndef native(): pass",
        ]:
            with self.subTest(source=source), self.assertRaises(ValueError):
                build.native_recipe(source)

    def test_native_donor_and_external_anchor_are_paired_at_build_admission(self):
        for donor, anchor in [(Path("donor.json"), None), (None, "a" * 64)]:
            with (
                self.subTest(donor=donor),
                self.assertRaisesRegex(ValueError, "supplied together"),
            ):
                build.build(
                    SimpleNamespace(
                        native_donor=donor, native_donor_receipt_sha256=anchor
                    ),
                    Path("unused"),
                )

    def test_native_donor_rebinding_and_stale_bytes_refused(self):
        # Synthetic package controls the guard, not a runtime/toolchain claim.
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(sys.modules, {"verify_apk": verify}),
        ):
            root = Path(directory)
            commit = subprocess.check_output(
                ["git", "-C", str(build.ROOT), "rev-parse", "HEAD"], text=True
            ).strip()
            native, files, apk, receipt, rebind = synthetic_native_donor(root, commit)
            anchor = build.digest(
                receipt
            )  # Selected before the supplied donor changes.
            copied, proof = build.reuse_native(
                receipt, root / "positive", ["arm64-v8a"], anchor
            )
            self.assertEqual(len(copied), 3)
            self.assertEqual(proof["expected_receipt_sha256"], anchor)
            for wrong in [None, "", "A" * 64, "0" * 64]:
                with self.subTest(anchor=wrong), self.assertRaises(ValueError):
                    build.reuse_native(receipt, root / "refused", ["arm64-v8a"], wrong)
            apk.write_bytes(apk.read_bytes() + b"stale")
            with self.assertRaisesRegex(ValueError, "APK byte drift"):
                build.reuse_native(receipt, root / "stale", ["arm64-v8a"], anchor)
            files["lib/arm64-v8a/libruntime_probe.so"] = (
                elf() + b"different valid ELF executable"
            )
            rebind()  # Rebind every donor-local product/provenance/receipt hash.
            with self.assertRaisesRegex(ValueError, "independently selected receipt"):
                build.reuse_native(receipt, root / "rebound", ["arm64-v8a"], anchor)
            self.assertFalse((root / "rebound").exists())

    def test_historical_unpatched_native_donor_is_refused(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(sys.modules, {"verify_apk": verify}),
        ):
            root = Path(directory)
            _, _, _, receipt, _ = synthetic_native_donor(root, HISTORICAL_NATIVE)
            with self.assertRaisesRegex(ValueError, "compiler recipe drift"):
                build.reuse_native(
                    receipt, root / "refused", ["arm64-v8a"], build.digest(receipt)
                )
            self.assertFalse((root / "refused").exists())

    def test_managed_native_donor_protocol_input_drift_is_refused(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(sys.modules, {"verify_apk": verify}),
        ):
            root = Path(directory)
            commit = subprocess.check_output(
                ["git", "-C", str(build.ROOT), "rev-parse", "HEAD"], text=True
            ).strip()
            native, _, _, receipt, rebind = synthetic_native_donor(root, commit)
            native["adapter_sources"][
                "platforms/android/native/host/actor-channel-v1.json"
            ] = "0" * 64
            rebind()
            with self.assertRaisesRegex(ValueError, "source input drift"):
                build.reuse_native(
                    receipt, root / "refused", ["arm64-v8a"], build.digest(receipt)
                )
            self.assertFalse((root / "refused").exists())

    def test_sdk_unix_link_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / "sdk.zip"
            with zipfile.ZipFile(archive, "w") as source:
                source.writestr("sdk/clang-21", b"real-binary")
                info = zipfile.ZipInfo("sdk/clang")
                info.create_system = 3
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
                source.writestr(info, b"clang-21")
            build.unzip(archive, root / "out")
            self.assertTrue((root / "out/sdk/clang").is_symlink())
            self.assertEqual((root / "out/sdk/clang").read_bytes(), b"real-binary")
            build.unzip(archive, root / "out")  # Verified extraction is reusable.

    def test_sdk_escape_refused(self):
        for name, link in [("../escape", None), ("sdk/link", "../../escape")]:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                with zipfile.ZipFile(root / "sdk.zip", "w") as source:
                    info = zipfile.ZipInfo(name)
                    if link:
                        info.create_system = 3
                        info.external_attr = (stat.S_IFLNK | 0o777) << 16
                    source.writestr(info, (link or "x").encode())
                with self.assertRaises(ValueError):
                    build.unzip(root / "sdk.zip", root / "out")
                self.assertFalse((root / "escape").exists())

    def test_existing_sdk_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "out/sdk").mkdir(parents=True)
            (root / "out/sdk/keep").write_text("owned")
            with zipfile.ZipFile(root / "sdk.zip", "w") as source:
                source.writestr("sdk/new", b"x")
            with self.assertRaises(ValueError):
                build.unzip(root / "sdk.zip", root / "out")
            self.assertEqual((root / "out/sdk/keep").read_text(), "owned")

    def test_checksum_mismatch_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp) / "cached.zip"
            p.write_bytes(b"wrong")
            with self.assertRaises(ValueError):
                build.fetch("https://unused.invalid", "0" * 64, p)
            self.assertEqual(p.read_bytes(), b"wrong")

    def test_helper_snapshot_binds_selector(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "scripts").mkdir()
            helper = root / "scripts/check_runtime_parity.py"
            selector = root / "scripts/build-android-release.py"
            helper.write_text("# helper")
            selector.write_text("# selector")
            record = {
                "source_head": "a" * 40,
                "files": {
                    "scripts/check_runtime_parity.py": build.digest(helper),
                    "scripts/build-android-release.py": build.digest(selector),
                },
            }
            (root / "provenance.json").write_text(json.dumps(record))
            self.assertEqual(build.helper_identity(helper)["source_head"], "a" * 40)
            selector.write_text("# changed")
            with self.assertRaises(ValueError):
                build.helper_identity(helper)

    def test_elf_abi_and_page_alignment(self):
        self.assertEqual(
            verify.elf_identity(elf(), "arm64-v8a")["load_alignment"], 16384
        )
        for data in [elf(machine=62), elf(alignment=4096), elf(flags=7), elf()[:80]]:
            with self.subTest(data=hashlib.sha256(data).hexdigest()):
                with self.assertRaises(ValueError):
                    verify.elf_identity(data, "arm64-v8a")


class BootOnlyControls(unittest.TestCase):
    def args(self, **updates):
        args = dict(
            boot_identity_only=True,
            non_debuggable=True,
            log_report=True,
            native_donor=Path("donor"),
            expected_adapter_commit="0" * 40,
        )
        args.update(updates)
        return SimpleNamespace(**args)

    def test_boot_admission_rejects_every_foreign_input_before_source_or_native(self):
        for name in (
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
        ):
            with (
                self.subTest(name=name),
                patch.object(build, "run") as native,
                self.assertRaisesRegex(ValueError, "excludes"),
            ):
                build.boot_admission(self.args(**{name: True}))
            native.assert_not_called()

    def test_boot_admission_requires_nondebug_export_donor_and_exact_commit(self):
        for key in ("non_debuggable", "log_report", "native_donor"):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "requires"):
                build.boot_admission(self.args(**{key: None}))
        with self.assertRaisesRegex(ValueError, "commit mismatch"):
            build.boot_admission(self.args())

    def test_boot_lease_is_separate_and_does_not_adopt_peer(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)
            build.validate_cache_owner(cache, True)
            self.assertEqual(
                json.loads((cache / "lease.json").read_text())["owner"],
                "apk266-boot-identity",
            )
            with self.assertRaises(ValueError):
                build.validate_cache_owner(cache)


if __name__ == "__main__":
    unittest.main()
