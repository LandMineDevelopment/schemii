"""Closed report and chunk corruption controls; synthetic facts are not device proof."""

import base64
import copy
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
import unittest

PATH = Path(__file__).resolve().parents[1] / "runtime/check_boot_identity_report.py"
spec = importlib.util.spec_from_file_location("boot_checker", PATH)
assert spec is not None and spec.loader is not None
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


def fixture():
    files = dict(check.CORE)
    files.update({k: "a" * 64 for k in check.SOURCES if k not in files})
    identity = {"source_commit": "c" * 40, "files": files, "source_sha256": "d" * 64}
    p = dict(
        schema=1,
        kind="disposable-feasibility",
        stack_acceptance=False,
        source_commit=identity["source_commit"],
        adapter_dirty=False,
        target_sdk=36,
        compile_sdk=36,
        min_sdk=29,
        application_id=check.PACKAGE,
        abis=["x86_64"],
        peer_components={},
        debuggable=False,
        diagnostic_log_export=True,
        wrapper_controls_only=False,
        wrapper_fixture=None,
        diagnostic_profile=check.PROFILE,
        native_donor=dict(
            source_commit="b" * 40,
            apk_sha256="a" * 64,
            native_provenance_sha256="a" * 64,
            receipt_sha256="481d8a7b7230bbad8229517d75f96d0232b5f89f27edf7f8980c853ff8844546",
            expected_receipt_sha256="481d8a7b7230bbad8229517d75f96d0232b5f89f27edf7f8980c853ff8844546",
            recipe_sha256="a" * 64,
            scope="base bytes",
        ),
        toolchain_lock_sha256="a" * 64,
        adapter_sources=files,
        parity_helper={},
        host_tools={},
        pending=[],
        boot_identity=identity,
        native_files={
            "lib/x86_64/" + k: "a" * 64
            for k in (
                "libproot.so",
                "libproot_loader.so",
                "libruntime_probe.so",
                "libboot_identity_jni.so",
            )
        },
    )
    sample = dict(
        origin=0,
        opened_nodes=1,
        procfs_checks=1,
        type_checks=1,
        mount_checks=1,
        read_calls=0,
        bytes_read=0,
    )
    n = dict(
        source_sha256=identity["source_sha256"],
        pid=123,
        uid=10123,
        pages=4096,
        kernel="6.6.0-android",
        knownness=0,
        status=1,
        stage=1,
        method=1,
        context=1,
        untraced_owner_verified=False,
        errno=13,
        cleanup_errno=0,
        failed_sample=1,
        failed_node=2,
        samples=[sample, {k: 0 for k in sample}],
    )
    r = dict(
        schema=1,
        profile=check.PROFILE,
        package=check.PACKAGE,
        run_id="p1b-01",
        source_commit=identity["source_commit"],
        source_sha256=identity["source_sha256"],
        native_provenance_sha256="e" * 64,
        signer_sha256="f" * 64,
        packaged_abis=["x86_64"],
        abi="x86_64",
        device_api=36,
        target_sdk=36,
        min_sdk=29,
        debuggable=False,
        java_pid=123,
        java_uid=10123,
        context={
            k: {"status": "AccessDenied", "value": None}
            for k in ("birth_ticks", "tracer_pid", "selinux_domain")
        },
        native=n,
        observation_complete=True,
        runtime_gate="NO-GO-native-unknown",
        stack_acceptance=False,
    )
    return p, identity, r


def validate(p, identity, r):
    return check.check_report(
        r,
        p,
        identity,
        run_id="p1b-01",
        provenance_sha256="e" * 64,
        signer_sha256="f" * 64,
        abi="x86_64",
    )


class ReportControls(unittest.TestCase):
    def test_duplicate_json_keys_refused(self):
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            check.loads('{"package":"hidden", "package":"safe"}')

    def test_real_jni_bridge_serializes_only_scalars_with_synthetic_core(self):
        # Exercise the production formatter/JNI boundary, never the host reader.
        javac = shutil.which("javac")
        assert javac is not None
        java_home = Path(javac).resolve().parents[1]
        android = PATH.parents[1]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            harness = root / "harness.c"
            harness.write_text(r"""#include "boot_identity.h"
#include <jni.h>
#include <stdio.h>
#include <string.h>
struct boot_identity_result boot_identity_read(void) {
  struct boot_identity_result r = {.knownness=BOOT_ID_KNOWN,
      .method=BOOT_ID_NATIVE_FIXED_PROC, .context=BOOT_ID_CALLER_MOUNT_NAMESPACE};
  memset(r.uuid, 0x71, sizeof(r.uuid));
  return r;
}
static jstring capture(JNIEnv *env, const char *text) {
  (void)env;
  puts(text);
  return (jstring)1;
}
extern jstring Java_development_landmine_schemii_runtimeprobe_BootIdentityProbe_observe(JNIEnv *, jclass);
int main(void) {
  const struct JNINativeInterface_ functions = {.NewStringUTF=capture};
  JNIEnv env = &functions;
  return Java_development_landmine_schemii_runtimeprobe_BootIdentityProbe_observe(&env, NULL) ? 0 : 1;
}
""")
            binary = root / "bridge-control"
            subprocess.run(
                [
                    "cc",
                    "-std=c11",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    "-O2",
                    "-I" + str(java_home / "include"),
                    "-I" + str(java_home / "include/linux"),
                    "-I" + str(android / "native/host"),
                    '-DBOOT_IDENTITY_SOURCE_SHA256="' + "a" * 64 + '"',
                    str(harness),
                    str(android / "native/host/boot_identity_jni.c"),
                    "-o",
                    str(binary),
                ],
                check=True,
                capture_output=True,
            )
            raw = subprocess.check_output([str(binary)])
            result = check.loads(raw)
            self.assertGreater(result["pid"], 0)
            self.assertEqual(result["uid"], os.getuid())
            self.assertEqual(result["source_sha256"], "a" * 64)
            self.assertNotIn("uuid", result)
            self.assertNotIn(b"71" * 16, raw)
            self.assertNotIn(b"q" * 16, raw)
            check.closed(
                result,
                "source_sha256 pid uid pages kernel knownness status stage method context untraced_owner_verified errno cleanup_errno failed_sample failed_node samples",
            )

    def test_denied_is_complete_but_no_go(self):
        p, identity, r = fixture()
        result = validate(p, identity, r)
        self.assertEqual(result["runtime_gate"], "NO-GO-native-unknown")
        self.assertFalse(result["owner_authority"])

    def test_no_raw_identity_field_at_any_schema_boundary(self):
        for path in ((), ("native",), ("context",), ("native", "samples", 0)):
            for key in ("boot_id", "uuid", "raw", "extra"):
                p, identity, r = fixture()
                target = r
                for part in path:
                    target = target[part]
                target[key] = "redaction-must-not-be-needed"
                with self.subTest(path=path, key=key), self.assertRaises(ValueError):
                    validate(p, identity, r)

    def test_report_mutations(self):
        changes = {
            "package": "development.landmine.schemii.runtimeprobe",
            "source_commit": "0" * 40,
            "run_id": "other",
            "source_sha256": "0" * 64,
            "native_provenance_sha256": "0" * 64,
            "signer_sha256": "0" * 64,
            "debuggable": True,
            "stack_acceptance": True,
            "device_api": 35,
            "target_sdk": "36",
            "min_sdk": 28,
            "java_pid": True,
            "abi": "arm64-v8a",
            "packaged_abis": ["arm64-v8a"],
            "observation_complete": 1,
            "runtime_gate": "caller-mount-observed-only",
            "profile": None,
        }
        for key, value in changes.items():
            p, identity, r = fixture()
            r[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate(p, identity, r)
        for key, value in {
            "knownness": 1,
            "untraced_owner_verified": True,
            "pid": 124,
            "errno": "13",
            "pages": 8192,
            "stage": 0,
            "status": False,
            "source_sha256": "0" * 64,
            "method": 0,
        }.items():
            p, identity, r = fixture()
            r["native"][key] = value
            with self.subTest(native=key), self.assertRaises(ValueError):
                validate(p, identity, r)

    def test_known_requires_both_fully_verified_walks(self):
        p, identity, r = fixture()
        r["native"].update(
            knownness=1, status=0, stage=0, errno=0, failed_node=0, failed_sample=0
        )
        sample = dict(
            origin=1,
            opened_nodes=5,
            procfs_checks=5,
            type_checks=5,
            mount_checks=5,
            read_calls=2,
            bytes_read=37,
        )
        r["native"]["samples"] = [sample, copy.deepcopy(sample)]
        r["runtime_gate"] = "caller-mount-observed-only"
        self.assertFalse(validate(p, identity, r)["recovery_acceptance"])
        r["native"]["samples"][1]["origin"] = 0
        with self.assertRaises(ValueError):
            validate(p, identity, r)

    def test_profile_mutations(self):
        mutations: dict[str, object] = {
            "diagnostic_profile": None,
            "application_id": "wrong",
            "debuggable": 0,
            "guest_payloads": {},
            "wrapper_controls_only": True,
            "boot_identity": {},
            "peer_components": {"network": {}},
            "target_sdk": True,
            "native_files": {},
            "native_donor": None,
        }
        for key, value in mutations.items():
            p, identity, r = fixture()
            p[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate(p, identity, r)

    def test_transport_complete_and_corruptions(self):
        raw = json.dumps({"run_id": "unrelated-random-run-identity"}).encode()
        parts = [raw[:12], raw[12:]]
        lines = [
            f"p1b-01:{i}/2:{len(raw)}:{check.sha(raw)}:"
            + base64.b64encode(part).decode()
            for i, part in enumerate(parts)
        ]
        self.assertEqual(check.decode_export(reversed(lines), "p1b-01"), raw)
        for bad in (
            lines[:1],
            lines + lines[:1],
            [lines[0].replace("/2:", "/3:")] + lines[1:],
            [lines[0].replace(check.sha(raw), "0" * 64)] + lines[1:],
            [line.replace("p1b-01", "wrong") for line in lines],
            [lines[0] + "A"] + lines[1:],
            [],
        ):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                check.decode_export(bad, "p1b-01")


if __name__ == "__main__":
    unittest.main()
