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


def known_fixture():
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
    return p, identity, r


def validate_diagnostics(bundle, p, identity):
    return check.check_diagnostics(
        bundle,
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

    def test_known_stage_and_eof_counters(self):
        for size in (36, 37):
            for calls in (2, size + 1):
                p, identity, r = known_fixture()
                for s in r["native"]["samples"]:
                    s.update(bytes_read=size, read_calls=calls)
                self.assertTrue(validate(p, identity, r)["observation_complete"])
        for stage in range(1, 9):
            p, identity, r = known_fixture()
            r["native"]["stage"] = stage
            with self.subTest(stage=stage), self.assertRaises(ValueError):
                validate(p, identity, r)
        for index in (0, 1):
            for size, calls in ((36, 0), (37, 0), (37, 1), (36, 38), (38, 2)):
                p, identity, r = known_fixture()
                r["native"]["samples"][index].update(bytes_read=size, read_calls=calls)
                with (
                    self.subTest(index=index, size=size, calls=calls),
                    self.assertRaises(ValueError),
                ):
                    validate(p, identity, r)

    def test_failure_sample_transitions_match_pinned_core(self):
        # Every refusal family, both samples, and feasible partial path/read
        # states. These are evidence-consumer controls, never kernel reads.
        cases = []
        for node in range(1, 6):
            cases.append((1, 1, node, node - 1, node - 1, node - 1, node - 1, 0, 0, 0))
            for stage in (2, 3, 4):
                counts = (node, node - (stage == 2), node - (stage in (2, 3)), node - 1)
                cases.append((2, stage, node, *counts, 0, 0, 0))
                cases.append((4, stage, node, *counts, 0, 0, 2))
            cases.append((3, 4, node, node, node, node, node - 1, 0, 0, 0))
        for status, size, calls in (
            (5, 0, 1),
            (5, 37, 38),
            (6, 0, 1),
            (7, 1, 2),
            (8, 36, 2),
            (9, 38, 1),
            (9, 38, 38),
        ):
            cases.append(
                (status, 5 if status == 5 else 6, 5, 5, 5, 5, 5, calls, size, 1)
            )
        for node in range(1, 5):
            cases.append((10, 7, node, node + 1, node, node, node, 0, 0, 0))
        cases.append((10, 7, 5, 5, 5, 5, 5, 2, 37, 1))
        for sample_index in (0, 1):
            for (
                status,
                stage,
                node,
                opened,
                fs,
                types,
                mount,
                calls,
                size,
                origin,
            ) in cases:
                p, identity, r = known_fixture()
                n = r["native"]
                n.update(
                    knownness=0,
                    status=status,
                    stage=stage,
                    failed_sample=sample_index + 1,
                    failed_node=node,
                    errno=13 if status in (1, 2, 5, 10) else 0,
                )
                n["samples"][sample_index] = dict(
                    origin=origin,
                    opened_nodes=opened,
                    procfs_checks=fs,
                    type_checks=types,
                    mount_checks=mount,
                    read_calls=calls,
                    bytes_read=size,
                )
                if sample_index == 0:
                    n["samples"][1] = {k: 0 for k in n["samples"][1]}
                r["runtime_gate"] = "NO-GO-native-unknown"
                with self.subTest(
                    status=status, stage=stage, node=node, sample=sample_index
                ):
                    self.assertFalse(validate(p, identity, r)["owner_authority"])
                corrupt = copy.deepcopy(r)
                corrupt["native"]["samples"][sample_index]["read_calls"] = (
                    38 if calls == 0 else 0
                )
                with (
                    self.subTest(corrupt=status, node=node, sample=sample_index),
                    self.assertRaises(ValueError),
                ):
                    validate(p, identity, corrupt)
        p, identity, r = known_fixture()
        r["native"].update(knownness=0, status=11, stage=8, failed_sample=2)
        r["runtime_gate"] = "NO-GO-native-unknown"
        self.assertFalse(validate(p, identity, r)["recovery_acceptance"])
        r["native"]["samples"][0]["read_calls"] = 0
        with self.assertRaises(ValueError):
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

    def test_production_canonical_request_and_refusal_paths(self):
        # Exact production pure-Java seams; no app/JNI/kernel boot read.
        source = (
            PATH.parents[1]
            / "app/src/main/java/development/landmine/schemii/runtimeprobe/BootIdentityProbe.java"
        ).read_text()
        helpers = "".join(
            source[source.index(first) : source.index(after)]
            for first, after in (
                ("    private static void require(", "    private static String sha("),
                (
                    "    private static String sha(",
                    "    private static byte[] bounded(",
                ),
                ("    private static byte[] bounded(", "    private static void keys("),
                (
                    "    private static void persist(",
                    "    private static synchronized void export(",
                ),
                (
                    "    // Request/diagnostic helpers:",
                    "    // End request/diagnostic helpers.",
                ),
                ("    static final class Journal {", "    // End diagnostic journal."),
            )
        )
        harness = r"""
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.security.MessageDigest;
import java.util.*;
public class RequestControls {
__PRODUCTION__
    static final String SECRET = "private-exception-or-extra-value-must-not-escape";
    static Map<String, Object> extras() {
        Map<String, Object> e = new HashMap<>();
        e.put("run_id", "p1b-01"); e.put("expected_source_commit", "c".repeat(40));
        e.put("expected_source_sha256", "d".repeat(64)); e.put("expected_native_provenance", "e".repeat(64));
        return e;
    }
    static void rejected(Map<String, Object> e, boolean delivered) throws Exception {
        try { new Request(e, delivered); throw new AssertionError("accepted unsafe request"); }
        catch (IOException expected) { }
    }
    static void emit(String name, Refusal r) throws Exception {
        require(r.emit((raw, run) -> {
            require(!new String(raw, StandardCharsets.UTF_8).contains(SECRET));
            System.out.println(name + " " + Base64.getEncoder().encodeToString(raw));
        }));
    }
    public static void main(String[] args) throws Exception {
        Map<String, String> files = new HashMap<>();
        for (String line : Files.readAllLines(Paths.get(args[0]))) {
            String[] pair = line.split("=", 2); files.put(pair[0], pair[1]);
        }
        System.out.println("digest " + sourceDigest(files));
        Map<String, String> badFiles = new HashMap<>(files); badFiles.replaceAll((k, v) -> SECRET);
        try { sourceDigest(badFiles); throw new AssertionError(); } catch (IOException expected) { }
        Map<String, Object> original = extras(); Request captured = new Request(original, false);
        original.put("run_id", "changed-after-capture"); original.put("export_report_only", true);
        require(captured.run.equals("p1b-01") && !captured.exportOnly);
        rejected(extras(), true); // Every new-intent delivery requires explicit export-only.
        for (Object invalid : new Object[]{null, "true", 1, new Object()}) {
            Map<String, Object> bad = extras(); bad.put("export_report_only", invalid); rejected(bad, false);
        }
        for (String key : extras().keySet()) {
            Map<String, Object> missing = extras(); missing.remove(key); rejected(missing, false);
            Map<String, Object> wrong = extras(); wrong.put(key, SECRET + " /"); rejected(wrong, false);
            Map<String, Object> typed = extras(); typed.put(key, true); rejected(typed, false);
        }
        Map<String, Object> unknown = extras(); unknown.put("secret", SECRET); rejected(unknown, false);
        Map<String, Object> export = extras(); export.put("export_report_only", true);
        Request delivered = new Request(export, true); require(delivered.exportOnly);
        Progress progress = new Progress(); progress.phase = "source-digest";
        Refusal first = new Refusal(extras(), progress, new IOException(SECRET)); first.first = true;
        require(first.matches(delivered)); emit("canonical_refused", first);
        require(!first.emit((raw, run) -> { throw new InterruptedException(SECRET); }));
        require(Thread.currentThread().isInterrupted()); Thread.interrupted();
        emit("retained_transport_loss", first);
        require(!new String(first.envelope(), StandardCharsets.UTF_8).contains(SECRET));
        Map<String, Object> foreign = extras(); foreign.put("expected_source_commit", "0".repeat(40));
        require(!first.matches(new Request(foreign, false)));
        emit("foreign_declaration", new Refusal(foreign, progress, new IOException(SECRET)));
        Map<String, Object> invalid = extras(); invalid.put("run_id", SECRET + " /"); invalid.put("expected_source_commit", SECRET);
        progress.phase = "request";
        emit("unbound", new Refusal(invalid, progress, new AssertionError(SECRET)));
        progress.phase = "claim"; progress.admitted = true;
        emit("admitted_claim_refused", new Refusal(extras(), progress, new IOException(SECRET)));
    }
}
""".replace("__PRODUCTION__", helpers)
        p, identity, _ = fixture()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "RequestControls.java").write_text(harness)
            (root / "files").write_text(
                "".join(k + "=" + v + "\n" for k, v in identity["files"].items())
            )
            subprocess.run(
                ["javac", "-Xlint:all", str(root / "RequestControls.java")],
                check=True,
                capture_output=True,
            )
            result = subprocess.run(
                ["java", "-cp", str(root), "RequestControls", str(root / "files")],
                check=True,
                capture_output=True,
                text=True,
            )
        lines = dict(line.split(" ", 1) for line in result.stdout.splitlines())
        canonical = json.dumps(identity["files"], sort_keys=True, separators=(",", ":"))
        self.assertEqual(lines.pop("digest"), check.sha(canonical.encode()))
        self.assertNotEqual(
            check.sha(canonical.replace("/", r"\/").encode()),
            check.sha(canonical.encode()),
        )
        self.assertEqual(len(lines), 5)
        bundles = {
            name: check.loads(base64.b64decode(raw)) for name, raw in lines.items()
        }
        for label, bundle in bundles.items():
            result = check.check_refusal(
                bundle,
                p,
                identity,
                run_id="unbound" if label == "unbound" else "p1b-01",
                provenance_sha256="e" * 64,
            )
            with self.subTest(label=label):
                self.assertFalse(result["observation_complete"])
                self.assertFalse(result["diagnostic_complete"])
                self.assertTrue(result["evidence_loss"])
                self.assertFalse(result["owner_authority"])
                self.assertFalse(result["recovery_acceptance"])
                self.assertEqual(
                    result["identity_admitted"], label == "admitted_claim_refused"
                )
                self.assertEqual(
                    result["declared_identity_matches_expected"],
                    label not in ("unbound", "foreign_declaration"),
                )
        first, replay = bundles["canonical_refused"], bundles["retained_transport_loss"]
        self.assertEqual(
            {k: v for k, v in replay.items() if k != "prior_export_failure"},
            {k: v for k, v in first.items() if k != "prior_export_failure"},
        )
        self.assertTrue(replay["prior_export_failure"])
        for key, value in (
            ("schema", True),
            ("sequence", 2),
            ("sequence", True),
            ("phase", "native-call"),
            ("category", "exception-text"),
            ("identity_trust", "admitted"),
            ("identity_trust", []),
            ("retention", "file-synced"),
            ("observation_complete", True),
            ("delivery", "acknowledged"),
            ("stack_acceptance", True),
            ("source_commit", None),
            ("source_commit", "private-value"),
            ("monotonic_ns", True),
            ("prior_export_failure", 1),
            ("run_id", "wrong"),
            ("raw_exception", "forbidden"),
        ):
            bad = copy.deepcopy(first)
            bad[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                check.check_refusal(
                    bad, p, identity, run_id="p1b-01", provenance_sha256="e" * 64
                )
        wrong = copy.deepcopy(bundles["admitted_claim_refused"])
        wrong["source_commit"] = "0" * 40
        with self.assertRaises(ValueError):
            check.check_refusal(
                wrong, p, identity, run_id="p1b-01", provenance_sha256="e" * 64
            )

    def test_production_dispatch_prioritizes_original_disk_journal(self):
        # Production dispatch and journal, with explicit Android/preflight facades.
        # No Android/native/host boot read. The cold-JVM state is static-slot reset
        # plus real private fixture files; original record bytes must survive.
        source = (
            PATH.parents[1]
            / "app/src/main/java/development/landmine/schemii/runtimeprobe/BootIdentityProbe.java"
        ).read_text()
        production = "\n".join(
            source[source.index(first) : source.index(after, source.index(first))]
            for first, after in (
                ("    private static void require(", "    private static void keys("),
                (
                    "    private static void persist(",
                    "    private static synchronized void export(",
                ),
                (
                    "    // Request/diagnostic helpers:",
                    "    // End request/diagnostic helpers.",
                ),
                ("    static final class Journal {", "    // End diagnostic journal."),
                ("    static boolean dispatch(Activity a, TextView view)", "\n}\n"),
            )
        )
        prefix = r"""
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.security.MessageDigest;
import java.util.*;
import java.util.concurrent.*;
public class BootIdentityProbe {
    static final String PACKAGE="development.landmine.schemii.bootidentityprobe", PROFILE="boot-identity-p1b-v1";
    static Journal retained;
    static Refusal retainedRefusal;
    static int preflights=0, forbiddenWork=0;
    static List<byte[]> sent=new ArrayList<>();
    static String observe() { forbiddenWork++; throw new AssertionError("native seam forbidden"); }
    static JSONObject fact(String p,String k) { forbiddenWork++; throw new AssertionError("context read forbidden"); }
    static void validateRetainedReport(byte[] bytes,JSONObject id,String run,String provenance) throws Exception { throw new IOException("synthetic invalid report"); }
    static JSONObject preflight(Activity a,JSONObject p,byte[] raw,Request request,Progress progress) {
        preflights++; progress.admitted=true;
        return new JSONObject().put("source_commit",request.commit).put("source_sha256",request.source);
    }
    static void export(byte[] raw,String run) { synchronized(sent) { sent.add(raw); } }
    // Test-only Android/value facades: no Android classes, device, JNI, shell or kernel read.
    static class Bundle extends HashMap<String,Object> { private static final long serialVersionUID=1; }
    static class Intent { final Bundle values; Intent(Bundle b){values=b;} Bundle getExtras(){return values;} }
    static class TextView { void setText(String value){} }
    static class Assets { InputStream open(String p) { return new ByteArrayInputStream("{}".getBytes(StandardCharsets.UTF_8)); } }
    static class ApplicationInfo { String nativeLibraryDir="x86_64"; int targetSdkVersion=36,minSdkVersion=29; }
    static class Signature { byte[] toByteArray(){return new byte[0];} }
    static class SigningInfo { Signature[] getApkContentsSigners(){return new Signature[]{new Signature()};} }
    static class PackageInfo { SigningInfo signingInfo=new SigningInfo(); }
    static class PackageManager { static final int GET_SIGNING_CERTIFICATES=1; PackageInfo getPackageInfo(String s,int i){return new PackageInfo();} }
    static class Activity {
        final File dir; Intent original; volatile CountDownLatch done=new CountDownLatch(1);
        Activity(File d,Bundle b){dir=d;original=new Intent(b);}
        String getPackageName(){return PACKAGE;} Intent getIntent(){return original;}
        Assets getAssets(){return new Assets();} File getFilesDir(){return dir;}
        ApplicationInfo getApplicationInfo(){return new ApplicationInfo();} PackageManager getPackageManager(){return new PackageManager();}
        void runOnUiThread(Runnable r){r.run();done.countDown();}
        void await() throws Exception { if(!done.await(5,TimeUnit.SECONDS)) throw new AssertionError("dispatch timeout"); done=new CountDownLatch(1); }
    }
    static class JSONObject {
        Map<String,Object> values=new HashMap<>();
        JSONObject(){} JSONObject(String s){if(!s.equals("{}"))throw new AssertionError("unexpected JSON input");}
        JSONObject put(String k,Object v){values.put(k,v);return this;}
        Object get(String k){return values.get(k);} Object opt(String k){return values.get(k);}
        String getString(String k){return (String)get(k);} int getInt(String k){return (Integer)get(k);}
        Object getJSONArray(String k){return get(k);}
    }
    static class android { static class os { static class Build { static class VERSION { static int SDK_INT=36; } } static class Process { static int myPid(){return 0;} static int myUid(){return 0;} } } }
"""
        suffix = r"""
    static Bundle extras(boolean export){
        Bundle b=new Bundle();b.put("run_id","p1b-01");b.put("expected_source_commit","c".repeat(40));
        b.put("expected_source_sha256","d".repeat(64));b.put("expected_native_provenance","e".repeat(64));
        if(export)b.put("export_report_only",true);return b;
    }
    static void print(String name,byte[] raw){System.out.println(name+" "+Base64.getEncoder().encodeToString(raw));}
    static void emit(String name,Activity a,TextView view,Bundle request) throws Exception {
        int before; synchronized(sent){before=sent.size();}
        dispatchExport(a,view,new Intent(request));a.await();
        synchronized(sent){require(sent.size()==before+1);print(name,sent.get(before));}
    }
    static Refusal cache() throws Exception {
        Progress p=new Progress();p.phase="source-digest";
        Refusal r=new Refusal(extras(false),p,new IOException("private-original-exception"));r.first=true;return r;
    }
    static Journal original(File dir) throws Exception {
        require(dir.mkdir());Journal j=new Journal(dir,"p1b-01","c".repeat(40),"d".repeat(64),"e".repeat(64));
        j.run(state->{state.phase("native-load");throw new IOException("private-original-exception");},b->{throw new AssertionError();});
        return j;
    }
    public static void main(String[] args) throws Exception {
        File root=new File(args[0]);TextView view=new TextView();
        Journal original=original(new File(root,"original"));
        byte[] start=Files.readAllBytes(original.file("started").toPath());
        byte[] terminal=Files.readAllBytes(original.file("terminal.record").toPath());
        retained=null;retainedRefusal=null; // New JVM, original disk evidence intact.
        Activity a=new Activity(original.directory,extras(false));
        dispatch(a,view);a.await();
        require(retained==null && retainedRefusal!=null && retainedRefusal.first);
        synchronized(sent){print("replay_refusal",sent.get(0));}
        int before=preflights;
        emit("cold_original",a,view,extras(true));
        require(preflights==before+1 && retained!=null && retained.finished);
        require(Arrays.equals(start,Files.readAllBytes(original.file("started").toPath())));
        require(Arrays.equals(terminal,Files.readAllBytes(original.file("terminal.record").toPath())));
        emit("memory_original",a,view,extras(true));
        // A foreign run cannot relabel the live OR cold original journal.
        Bundle foreign=extras(true);foreign.put("run_id","foreign-run");
        Journal liveOriginal=retained;
        emit("foreign_live",a,view,foreign);require(retained==liveOriginal);
        retained=null;
        emit("foreign_disk",a,view,foreign);require(retained==null);
        emit("original_after_foreign",a,view,extras(true));require(retained!=null);
        require(Arrays.equals(start,Files.readAllBytes(original.file("started").toPath())));
        require(Arrays.equals(terminal,Files.readAllBytes(original.file("terminal.record").toPath())));
        // A request while the original is still running must not replace it.
        File ld=new File(root,"live");require(ld.mkdir());
        Journal live=new Journal(ld,"p1b-01","c".repeat(40),"d".repeat(64),"e".repeat(64));
        CountDownLatch started=new CountDownLatch(1),release=new CountDownLatch(1);
        Thread worker=new Thread(()->{try{live.run(j->{j.phase("native-load");started.countDown();require(release.await(5,TimeUnit.SECONDS));throw new IOException("private-original-exception");},b->{throw new AssertionError();});}catch(Exception e){throw new AssertionError(e);}});
        retained=live;retainedRefusal=null;worker.start();require(started.await(5,TimeUnit.SECONDS));
        Activity la=new Activity(ld,extras(false));emit("in_flight_refusal",la,view,extras(true));require(retained==live && !live.finished);
        release.countDown();worker.join(5000);require(!worker.isAlive() && live.finished);
        emit("live_finished_original",la,view,extras(true));require(retained==live);
        // Corrupt/partial/nonregular records still take precedence as loss evidence.
        Journal partial=original(new File(root,"partial"));
        Files.writeString(partial.file("started").toPath(),"private-corrupt-record");
        retained=null;retainedRefusal=cache();
        emit("partial_original",new Activity(partial.directory,extras(false)),view,extras(true));
        require(Files.readString(partial.file("started").toPath()).equals("private-corrupt-record"));
        for(String marker:new String[]{"started","terminal.record","report.json","export-loss.record"}){
            File dir=new File(root,"marker-"+marker);require(dir.mkdir());
            File file=new File(dir,"boot-identity-"+marker);
            if(marker.equals("started"))require(file.mkdir());else Files.writeString(file.toPath(),"private-corrupt-record");
            retained=null;retainedRefusal=cache();
            emit("marker_"+marker,new Activity(dir,extras(false)),view,extras(true));
            require(file.exists());
        }
        // Failed stat is uncertainty, not proof of absence or eligibility for cache.
        File uncertain=new File(root,"not-a-directory");Files.writeString(uncertain.toPath(),"private-record");
        retained=null;retainedRefusal=cache();before=preflights;
        emit("uncertain_path",new Activity(uncertain,extras(false)),view,extras(true));
        require(preflights==before+1 && retained==null && Files.readString(uncertain.toPath()).equals("private-record"));
        // Only positively absent original files permit the RAM-only shortcut.
        File empty=new File(root,"empty");require(empty.mkdir());
        retained=null;retainedRefusal=cache();before=preflights;
        byte[] first=retainedRefusal.envelope();
        emit("ram_only",new Activity(empty,extras(false)),view,extras(true));
        synchronized(sent){require(Arrays.equals(first,sent.get(sent.size()-1)));}
        require(preflights==before && retained==null && empty.list().length==0);
        require(forbiddenWork==0);
    }
}
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "BootIdentityProbe.java").write_text(prefix + production + suffix)
            for command in (
                ["javac", "-Xlint:all", str(root / "BootIdentityProbe.java")],
                ["java", "-cp", str(root), "BootIdentityProbe", str(root)],
            ):
                result = subprocess.run(command, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
        envelopes = {
            name: check.loads(base64.b64decode(raw))
            for name, raw in (line.split(" ", 1) for line in result.stdout.splitlines())
        }
        self.assertEqual(len(envelopes), 15)
        p, identity, _ = fixture()
        for label, bundle in envelopes.items():
            run = "foreign-run" if label.startswith("foreign_") else "p1b-01"
            validator = (
                check.check_refusal
                if bundle["kind"] == "boot-identity-refusal-v1"
                else check.check_diagnostics
            )
            inspected = validator(
                bundle,
                p,
                identity,
                run_id=run,
                provenance_sha256="e" * 64,
                signer_sha256="f" * 64,
                abi="x86_64",
            )
            with self.subTest(label=label):
                self.assertFalse(inspected["observation_complete"])
                self.assertFalse(inspected["owner_authority"])
                self.assertFalse(inspected["recovery_acceptance"])
                self.assertNotIn("private-", json.dumps(bundle))
                self.assertEqual(
                    inspected["diagnostic_complete"],
                    label
                    in {
                        "cold_original",
                        "memory_original",
                        "original_after_foreign",
                        "live_finished_original",
                    },
                )
        for label in ("cold_original", "memory_original", "original_after_foreign"):
            self.assertEqual(
                envelopes[label]["terminal"]["event"]["phase"], "native-load"
            )
        self.assertEqual(
            envelopes["cold_original"]["start"]["event"],
            envelopes["original_after_foreign"]["start"]["event"],
        )
        for label in (
            "partial_original",
            "marker_started",
            "marker_terminal.record",
            "marker_report.json",
            "marker_export-loss.record",
        ):
            self.assertEqual(envelopes[label]["kind"], "boot-identity-diagnostics-v1")
        self.assertEqual(envelopes["uncertain_path"]["phase"], "retained-export")
        self.assertEqual(envelopes["ram_only"]["phase"], "source-digest")

    def test_production_java_journal_failure_and_export_only(self):
        # Compile the exact production pure-Java journal and its four I/O/hash
        # helpers. The Work callback injects failures instead of loading JNI;
        # real private fixture files and export-only recovery exercise the path.
        source = (
            PATH.parents[1]
            / "app/src/main/java/development/landmine/schemii/runtimeprobe/BootIdentityProbe.java"
        ).read_text()
        journal = source[
            source.index("    static final class Journal {") : source.index(
                "    // End diagnostic journal."
            )
        ]
        helpers = "".join(
            source[source.index(first) : source.index(after)]
            for first, after in (
                ("    private static void require(", "    private static String sha("),
                (
                    "    private static String sha(",
                    "    private static byte[] bounded(",
                ),
                ("    private static byte[] bounded(", "    private static void keys("),
                (
                    "    private static void persist(",
                    "    private static synchronized void export(",
                ),
            )
        )
        harness = r"""
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.security.MessageDigest;
import java.util.*;
public class JournalControls {
__PRODUCTION__
    static int calls;
    static final String SECRET = "must-never-export-original-exception-text";
    static final String COMMIT = "c".repeat(40), SOURCE = "d".repeat(64), PROVENANCE = "e".repeat(64);
    static Journal create(File root, String name) throws Exception {
        File dir = new File(root, name); require(dir.mkdir());
        return new Journal(dir, "p1b-01", COMMIT, SOURCE, PROVENANCE);
    }
    static void emit(String label, Journal journal) throws Exception {
        require(journal.emit((bytes, run) -> {
            require("p1b-01".equals(run) && !new String(bytes, StandardCharsets.UTF_8).contains(SECRET));
            System.out.println(label + " " + Base64.getEncoder().encodeToString(bytes));
        }));
    }
    public static void main(String[] args) throws Exception {
        File root = new File(args[0]); byte[] report = args[1].getBytes(StandardCharsets.UTF_8);
        Journal.Validator validator = bytes -> require(Arrays.equals(bytes, report));
        Journal failed = create(root, "failed");
        failed.run(j -> { calls++; j.phase("native-load"); throw new UnsatisfiedLinkError(SECRET); }, validator);
        require(calls == 1 && !failed.file("report.json").exists());
        byte[] start = Files.readAllBytes(failed.file("started").toPath());
        byte[] terminal = Files.readAllBytes(failed.file("terminal.record").toPath());
        emit("load", failed);
        Journal recovered = new Journal(failed.directory, "p1b-01", COMMIT, SOURCE, PROVENANCE);
        recovered.recover(validator); emit("load_recovered", recovered);
        try { recovered.run(j -> { calls++; return report; }, validator); throw new AssertionError(); }
        catch (IOException expected) { require(calls == 1); }
        require(Arrays.equals(start, Files.readAllBytes(failed.file("started").toPath())));
        require(Arrays.equals(terminal, Files.readAllBytes(failed.file("terminal.record").toPath())));
        try {
            new Journal(failed.directory, "wrong-run", COMMIT, SOURCE, PROVENANCE).recover(validator);
            throw new AssertionError();
        } catch (Journal.IdentityRefusal expected) { require(calls == 1); }
        Journal io = create(root, "report_io");
        io.run(j -> { j.phase("report-build"); require(j.file("report.json").mkdir()); return report; }, validator);
        emit("report_write_failed", io);
        Journal ioRestored = new Journal(io.directory, "p1b-01", COMMIT, SOURCE, PROVENANCE);
        ioRestored.recover(validator); emit("report_lost", ioRestored);
        // Remove only this fixture's empty obstructing directory. Recovery now
        // sees an absent report despite the retained report-persist failure.
        byte[] ioStart = Files.readAllBytes(io.file("started").toPath());
        byte[] ioTerminal = Files.readAllBytes(io.file("terminal.record").toPath());
        Files.delete(io.file("report.json").toPath());
        Journal ioAbsent = new Journal(io.directory, "p1b-01", COMMIT, SOURCE, PROVENANCE);
        ioAbsent.recover(validator); emit("report_absent_recovered", ioAbsent);
        require(Arrays.equals(ioStart, Files.readAllBytes(ioAbsent.file("started").toPath())));
        require(Arrays.equals(ioTerminal, Files.readAllBytes(ioAbsent.file("terminal.record").toPath())));
        Journal end = create(root, "terminal_io");
        end.run(j -> { j.phase("report-build"); require(j.file("terminal.record").mkdir()); return report; }, validator);
        emit("terminal_write_failed", end);
        Journal endRestored = new Journal(end.directory, "p1b-01", COMMIT, SOURCE, PROVENANCE);
        endRestored.recover(validator); emit("terminal_lost", endRestored);
        Journal startLost = new Journal(new File(root, "missing-parent/child"), "p1b-01", COMMIT, SOURCE, PROVENANCE);
        startLost.run(j -> { calls++; return report; }, validator);
        require(calls == 1); emit("start_storage_failed", startLost); emit("start_memory_export", startLost);
        Journal ok = create(root, "success");
        ok.run(j -> { calls++; j.phase("report-build"); return report; }, validator);
        emit("success", ok);
        byte[] originalTerminal = Files.readAllBytes(ok.file("terminal.record").toPath());
        require(!ok.emit((bytes, run) -> { throw new IOException(SECRET); }));
        Journal okRestored = new Journal(ok.directory, "p1b-01", COMMIT, SOURCE, PROVENANCE);
        okRestored.recover(validator); emit("export_recovered", okRestored);
        require(calls == 2 && Arrays.equals(originalTerminal, Files.readAllBytes(ok.file("terminal.record").toPath())));
        Journal error = create(root, "error");
        error.run(j -> { j.phase("context"); throw new AssertionError(SECRET); }, validator); emit("java_error", error);
        Journal interrupted = create(root, "interrupted");
        interrupted.run(j -> { j.phase("context"); throw new InterruptedException(SECRET); }, validator);
        require(Thread.currentThread().isInterrupted()); Thread.interrupted(); emit("interrupted", interrupted);
        Journal corrupt = create(root, "corrupt");
        corrupt.run(j -> { j.phase("report-build"); return report; }, validator);
        Files.writeString(corrupt.file("started").toPath(), SECRET);
        Journal corruptRestored = new Journal(corrupt.directory, "p1b-01", COMMIT, SOURCE, PROVENANCE);
        corruptRestored.recover(validator); emit("corrupt_start", corruptRestored);
    }
}
""".replace("__PRODUCTION__", helpers + journal)
        p, identity, report = fixture()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "JournalControls.java").write_text(harness)
            subprocess.run(
                ["javac", "-Xlint:all", str(root / "JournalControls.java")],
                check=True,
                capture_output=True,
            )
            result = subprocess.run(
                [
                    "java",
                    "-cp",
                    str(root),
                    "JournalControls",
                    str(root),
                    json.dumps(report, separators=(",", ":")),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
        bundles = {
            label: check.loads(base64.b64decode(encoded))
            for label, encoded in (
                line.split(" ", 1) for line in result.stdout.splitlines()
            )
        }
        self.assertEqual(len(bundles), 14)
        for label, bundle in bundles.items():
            with self.subTest(label=label):
                validated = validate_diagnostics(bundle, p, identity)
                self.assertEqual(
                    validated["observation_complete"],
                    label in ("success", "export_recovered"),
                )
                self.assertFalse(validated["owner_authority"])
                self.assertFalse(validated["recovery_acceptance"])
                complete = label in (
                    "load",
                    "load_recovered",
                    "success",
                    "export_recovered",
                    "java_error",
                    "interrupted",
                )
                self.assertEqual(validated["diagnostic_complete"], complete)
                self.assertEqual(validated["evidence_loss"], not complete)
                self.assertNotIn(
                    "must-never-export-original-exception-text", json.dumps(bundle)
                )
        for label in ("load", "load_recovered"):
            terminal = bundles[label]["terminal"]["event"]
            self.assertEqual(
                (terminal["phase"], terminal["category"]),
                ("native-load", "native-linkage"),
            )
            self.assertEqual(terminal["run_id"], "p1b-01")
            self.assertEqual(terminal["source_sha256"], identity["source_sha256"])
            self.assertIsNone(bundles[label]["report"]["json"])
        absent = bundles["report_absent_recovered"]
        self.assertEqual(absent["report"]["storage"], "absent")
        self.assertEqual(absent["terminal"]["storage"], "readback-valid")
        self.assertEqual(absent["terminal"]["event"]["phase"], "report-persist")
        for phase in ("report-persist", "terminal-persist"):
            late = copy.deepcopy(absent)
            late["terminal"]["event"]["phase"] = phase
            validated = validate_diagnostics(late, p, identity)
            with self.subTest(missing_report_phase=phase):
                self.assertFalse(validated["diagnostic_complete"])
                self.assertTrue(validated["evidence_loss"])
                self.assertFalse(validated["observation_complete"])
        self.assertTrue(
            validate_diagnostics(bundles["export_recovered"], p, identity)[
                "prior_export_failure"
            ]
        )
        self.assertEqual(
            bundles["success"]["terminal"]["event"],
            bundles["export_recovered"]["terminal"]["event"],
        )
        for path, value in (
            ((), "extra"),
            (("start",), "raw"),
            (("terminal", "event"), "exception"),
        ):
            bad = copy.deepcopy(bundles["success"])
            target = bad
            for key in path:
                target = target[key]
            target[value] = "forbidden"
            with self.subTest(path=path), self.assertRaises(ValueError):
                validate_diagnostics(bad, p, identity)
        for key, value in (("run_id", "wrong"), ("source_commit", "0" * 40)):
            bad = copy.deepcopy(bundles["load_recovered"])
            bad["terminal"]["event"][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_diagnostics(bad, p, identity)
        bad = copy.deepcopy(bundles["success"])
        bad["terminal"]["event"]["monotonic_ns"] = (
            bad["start"]["event"]["monotonic_ns"] - 1
        )
        with self.assertRaises(ValueError):
            validate_diagnostics(bad, p, identity)
        for path, key, mutation in (
            (("start",), "storage", {}),
            (("report",), "storage", []),
            (("terminal", "event"), "phase", []),
            (("terminal", "event"), "category", {}),
            (("terminal", "event"), "sequence", True),
            (("terminal", "event"), "outcome", "started"),
            (("report",), "sha256", "0" * 64),
            (("report",), "json", '{"private":"refuse"}'),
        ):
            bad = copy.deepcopy(bundles["success"])
            target = bad
            for key_path in path:
                target = target[key_path]
            target[key] = mutation
            with self.subTest(path=path, key=key), self.assertRaises(ValueError):
                validate_diagnostics(bad, p, identity)


if __name__ == "__main__":
    unittest.main()
