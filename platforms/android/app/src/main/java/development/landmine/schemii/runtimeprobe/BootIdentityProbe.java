package development.landmine.schemii.runtimeprobe;

import android.app.Activity;
import android.content.Intent;
import android.content.pm.ApplicationInfo;
import android.content.pm.PackageInfo;
import android.content.pm.PackageManager;
import android.os.Bundle;
import android.widget.TextView;
import org.json.JSONArray;
import org.json.JSONObject;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.AccessDeniedException;
import java.nio.file.Files;
import java.nio.file.Paths;
import java.security.MessageDigest;
import java.util.*;

/** One finite app-JVM observation. No process, guest, network or recovery actions. */
final class BootIdentityProbe {
    static final String PACKAGE = "development.landmine.schemii.bootidentityprobe";
    static final String PROFILE = "boot-identity-p1b-v1";
    private static native String observe();
    private static Journal retained;
    private static Refusal retainedRefusal;

    private static void require(boolean ok) throws IOException {
        if (!ok) throw new IOException("Boot-only identity/profile refused");
    }
    private static String sha(byte[] data) throws Exception {
        StringBuilder s = new StringBuilder();
        for (byte b : MessageDigest.getInstance("SHA-256").digest(data)) s.append(String.format(Locale.ROOT, "%02x", b & 255));
        return s.toString();
    }
    private static byte[] bounded(InputStream in, int max) throws IOException {
        try (InputStream input = in; ByteArrayOutputStream bytes = new ByteArrayOutputStream()) {
            byte[] buffer = new byte[4096];
            for (int n; (n = input.read(buffer)) != -1;) {
                require(bytes.size() + n <= max);
                bytes.write(buffer, 0, n);
            }
            return bytes.toByteArray();
        }
    }
    private static void keys(JSONObject o, String names) throws Exception {
        Set<String> actual = new HashSet<>();
        Iterator<String> it = o.keys();
        while (it.hasNext()) actual.add(it.next());
        require(actual.equals(new HashSet<>(Arrays.asList(names.split(" ")))));
    }
    private static void persist(File file, byte[] raw) throws Exception {
        require(raw.length <= 65536 && file.createNewFile());
        try (FileOutputStream out = new FileOutputStream(file)) {
            out.write(raw); out.getFD().sync();
        }
    }
    private static synchronized void export(byte[] raw, String run) throws Exception {
        require(raw.length > 0 && raw.length <= 65536);
        String digest = sha(raw);
        int count = (raw.length + 1199) / 1200;
        for (int i = 0; i < count; i++) {
            String chunk = android.util.Base64.encodeToString(Arrays.copyOfRange(raw, i * 1200, Math.min(raw.length, (i + 1) * 1200)), android.util.Base64.NO_WRAP);
            require(android.util.Log.i("SchemiiBootIdentity", run + ":" + i + "/" + count + ":" + raw.length + ":" + digest + ":" + chunk) > 0);
        }
    }
    // Request/diagnostic helpers: no Android, native observation or recovery authority.
    static final class Request {
        final String run, commit, source, provenance;
        final boolean exportOnly;
        Request(Map<String, Object> extras, boolean newIntent) throws Exception {
            Set<String> allowed = new HashSet<>(Arrays.asList("run_id", "expected_source_commit", "expected_source_sha256", "expected_native_provenance", "export_report_only"));
            require(allowed.containsAll(extras.keySet()));
            run = token(extras.get("run_id"), "[a-zA-Z0-9_-]{1,80}");
            commit = token(extras.get("expected_source_commit"), "[0-9a-f]{40}");
            source = token(extras.get("expected_source_sha256"), "[0-9a-f]{64}");
            provenance = token(extras.get("expected_native_provenance"), "[0-9a-f]{64}");
            require(run != null && commit != null && source != null && provenance != null);
            require(!extras.containsKey("export_report_only") || extras.get("export_report_only") instanceof Boolean);
            exportOnly = Boolean.TRUE.equals(extras.get("export_report_only"));
            require(!newIntent || exportOnly); // A delivered intent can NEVER dispatch native work.
        }
        static String token(Object value, String pattern) {
            return value instanceof String && ((String)value).matches(pattern) ? (String)value : null;
        }
    }
    static String sourceDigest(Map<String, String> files) throws Exception {
        // JSONObject.quote escapes '/' on Android. The build contract deliberately
        // uses literal ASCII paths; use the same deterministic encoding everywhere.
        StringBuilder canonical = new StringBuilder("{");
        for (Map.Entry<String, String> entry : new TreeMap<>(files).entrySet()) {
            require(entry.getKey().matches("[a-zA-Z0-9_./]+") && entry.getValue().matches("[0-9a-f]{64}"));
            if (canonical.length() > 1) canonical.append(',');
            canonical.append(Journal.quote(entry.getKey())).append(':').append(Journal.quote(entry.getValue()));
        }
        return sha(canonical.append('}').toString().getBytes(StandardCharsets.UTF_8));
    }
    static final class Progress {
        String phase = "request";
        boolean admitted;
        String captureCategory;
    }
    static final class Refusal {
        static final Set<String> PHASES = new HashSet<>(Arrays.asList("request", "metadata", "profile", "source", "source-digest", "request-identity", "libraries", "claim", "retained-export"));
        final String run, commit, source, provenance, phase, category, trust;
        final long time = System.nanoTime();
        boolean first, exportFailed;
        Refusal(Map<String, Object> extras, Progress progress, Throwable failure) throws Exception {
            require(PHASES.contains(progress.phase));
            String candidate = Request.token(extras.get("run_id"), "[a-zA-Z0-9_-]{1,80}");
            run = candidate == null ? "unbound" : candidate;
            commit = Request.token(extras.get("expected_source_commit"), "[0-9a-f]{40}");
            source = Request.token(extras.get("expected_source_sha256"), "[0-9a-f]{64}");
            provenance = Request.token(extras.get("expected_native_provenance"), "[0-9a-f]{64}");
            trust = progress.admitted ? "admitted" : candidate != null && commit != null && source != null && provenance != null ? "declared-only" : "unbound";
            phase = progress.phase; category = progress.captureCategory == null ? Journal.category(failure) : progress.captureCategory;
        }
        boolean matches(Request request) {
            return run.equals(request.run) && request.commit.equals(commit) && request.source.equals(source) && request.provenance.equals(provenance);
        }
        static String nullable(String value) { return value == null ? "null" : Journal.quote(value); }
        byte[] envelope() {
            return ("{\"schema\":1,\"kind\":\"boot-identity-refusal-v1\",\"run_id\":" + Journal.quote(run)
                + ",\"source_commit\":" + nullable(commit) + ",\"source_sha256\":" + nullable(source) + ",\"native_provenance_sha256\":" + nullable(provenance)
                + ",\"identity_trust\":" + Journal.quote(trust) + ",\"sequence\":1,\"clock\":\"java-nanoTime-process\",\"monotonic_ns\":" + time
                + ",\"phase\":" + Journal.quote(phase) + ",\"category\":" + Journal.quote(category)
                + ",\"outcome\":\"refused\",\"retention\":\"volatile-only\",\"retained_first\":" + first + ",\"prior_export_failure\":" + exportFailed
                + ",\"delivery\":\"unacknowledged\",\"observation_complete\":false,\"stack_acceptance\":false}").getBytes(StandardCharsets.UTF_8);
        }
        synchronized boolean emit(Journal.Sender sender) {
            try { sender.send(envelope(), run); return true; }
            catch (Throwable failure) {
                if (failure instanceof InterruptedException) Thread.currentThread().interrupt();
                exportFailed = true; return false; // Explicit retained loss, never auto-retry.
            }
        }
    }
    // End request/diagnostic helpers.
    private static JSONObject fact(String path, String kind) throws Exception {
        try {
            String text = new String(bounded(Files.newInputStream(Paths.get(path)), 16384), StandardCharsets.UTF_8).trim();
            Object value;
            if (kind.equals("birth")) {
                String[] fields = text.substring(text.lastIndexOf(')') + 2).split(" +");
                value = Long.parseLong(fields[19]);
                require((Long)value > 0);
            } else if (kind.equals("tracer")) {
                java.util.regex.Matcher m = java.util.regex.Pattern.compile("(?m)^TracerPid:\\s*(\\d+)$").matcher(text);
                require(m.find()); value = Long.parseLong(m.group(1));
            } else {
                require(text.matches("u:r:[a-zA-Z0-9_:,.-]{1,180}")); value = text;
            }
            return new JSONObject().put("status", "observed").put("value", value);
        } catch (Exception error) {
            return new JSONObject().put("status", error instanceof AccessDeniedException ? "AccessDenied" : "unknown").put("value", JSONObject.NULL);
        }
    }
    private static JSONObject preflight(Activity a, JSONObject p, byte[] raw, Request request, Progress progress) throws Exception {
        progress.phase = "profile";
        keys(p, "schema kind stack_acceptance source_commit adapter_dirty target_sdk min_sdk compile_sdk application_id abis peer_components debuggable diagnostic_log_export wrapper_controls_only wrapper_fixture diagnostic_profile native_files native_donor toolchain_lock_sha256 adapter_sources parity_helper host_tools pending boot_identity");
        require(PACKAGE.equals(a.getPackageName()) && PACKAGE.equals(p.get("application_id")) && PROFILE.equals(p.get("diagnostic_profile")));
        require(Integer.valueOf(1).equals(p.get("schema")) && "disposable-feasibility".equals(p.get("kind")));
        for (String k : new String[]{"adapter_dirty", "debuggable", "wrapper_controls_only", "stack_acceptance"}) require(Boolean.FALSE.equals(p.get(k)));
        require(Boolean.TRUE.equals(p.get("diagnostic_log_export")) && p.isNull("wrapper_fixture") && p.getJSONObject("peer_components").length() == 0 && !p.isNull("native_donor"));
        require(Integer.valueOf(36).equals(p.get("compile_sdk")) && Integer.valueOf(36).equals(p.get("target_sdk")) && Integer.valueOf(29).equals(p.get("min_sdk")));
        ApplicationInfo info = a.getApplicationInfo();
        require((info.flags & ApplicationInfo.FLAG_DEBUGGABLE) == 0 && info.targetSdkVersion == 36 && info.minSdkVersion == 29 && android.os.Build.VERSION.SDK_INT == 36);
        progress.phase = "source";
        JSONObject id = p.getJSONObject("boot_identity");
        keys(id, "source_commit source_sha256 files");
        require(id.getString("source_commit").matches("[0-9a-f]{40}") && id.getString("source_commit").equals(p.getString("source_commit")) && id.getString("source_sha256").matches("[0-9a-f]{64}"));
        String prefix = "platforms/android/";
        String java = "app/src/main/java/development/landmine/schemii/runtimeprobe/";
        JSONObject files = id.getJSONObject("files");
        String[] names = {prefix + "native/host/boot_identity.c", prefix + "native/host/boot_identity.h", prefix + "native/host/boot_identity_jni.c", prefix + java + "BootIdentityProbe.java", prefix + java + "ProbeActivity.java", prefix + "runtime/check_boot_identity_report.py"};
        keys(files, String.join(" ", names));
        require("854f90067b0838bb9d97108461b6d110426067534550d886a1f926900c95def3".equals(files.get(names[0])) && "b4621a7e4d66a4f2156f5b66943189a7d268f41f667bf4bf41c9f94cddab40a3".equals(files.get(names[1])));
        Map<String, String> sourceFiles = new HashMap<>();
        for (String name : names) {
            String digest = files.getString(name);
            require(digest.matches("[0-9a-f]{64}") && digest.equals(p.getJSONObject("adapter_sources").getString(name)));
            sourceFiles.put(name, digest);
        }
        progress.phase = "source-digest";
        require(sourceDigest(sourceFiles).equals(id.getString("source_sha256")));
        progress.phase = "request-identity";
        require(id.getString("source_commit").equals(request.commit) && id.getString("source_sha256").equals(request.source) && sha(raw).equals(request.provenance));
        progress.phase = "libraries";
        JSONArray abis = p.getJSONArray("abis");
        require(abis.length() > 0 && abis.length() <= 2);
        Set<String> nativeNames = new HashSet<>(), seen = new HashSet<>();
        for (int i = 0; i < abis.length(); i++) {
            String abi = abis.getString(i);
            require((abi.equals("x86_64") || abi.equals("arm64-v8a")) && seen.add(abi));
            for (String lib : new String[]{"libproot.so", "libproot_loader.so", "libruntime_probe.so", "libboot_identity_jni.so"}) nativeNames.add("lib/" + abi + "/" + lib);
        }
        keys(p.getJSONObject("native_files"), String.join(" ", nativeNames));
        // Check the installed native files before loading JNI; never execute donors.
        for (String lib : new String[]{"libproot.so", "libproot_loader.so", "libruntime_probe.so", "libboot_identity_jni.so"}) {
            File f = new File(info.nativeLibraryDir, lib);
            String abi = info.nativeLibraryDir.contains("arm64") ? "arm64-v8a" : "x86_64";
            require(seen.contains(abi));
            try (InputStream in = new FileInputStream(f)) {
                require(sha(bounded(in, 16 * 1024 * 1024)).equals(p.getJSONObject("native_files").getString("lib/" + abi + "/" + lib)));
            }
        }
        progress.admitted = true;
        return id;
    }

    /** Fixed-field records and original report only; no Android/native calls here. */
    static final class Journal {
        interface Work { byte[] observe(Journal journal) throws Exception; }
        interface Validator { void verify(byte[] report) throws Exception; }
        interface Sender { void send(byte[] envelope, String run) throws Exception; }
        static final String[] FIELDS = {"schema", "run_id", "source_commit", "source_sha256", "native_provenance_sha256", "sequence", "monotonic_ns", "phase", "outcome", "category"};
        static final Set<String> PHASES = new HashSet<>(Arrays.asList("start", "context", "native-load", "native-call", "report-build", "report-persist", "terminal-persist", "export"));
        static final Set<String> CATEGORIES = new HashSet<>(Arrays.asList("none", "native-linkage", "io", "interrupted", "refused", "java-error"));
        final File directory;
        final String run, commit, source, provenance;
        Map<String, String> start, terminal, loss;
        String startStorage = "absent", terminalStorage = "absent", lossStorage = "absent", reportStorage = "absent";
        byte[] report;
        String phase = "start";
        volatile boolean finished;

        Journal(File directory, String run, String commit, String source, String provenance) throws Exception {
            require(run.matches("[a-zA-Z0-9_-]{1,80}") && commit.matches("[0-9a-f]{40}") && source.matches("[0-9a-f]{64}") && provenance.matches("[0-9a-f]{64}"));
            this.directory = directory; this.run = run; this.commit = commit; this.source = source; this.provenance = provenance;
        }
        boolean matches(String run, String commit, String source, String provenance) {
            return this.run.equals(run) && this.commit.equals(commit) && this.source.equals(source) && this.provenance.equals(provenance);
        }
        File file(String name) { return new File(directory, "boot-identity-" + name); }
        void phase(String value) throws Exception { require(PHASES.contains(value)); phase = value; }
        static String category(Throwable error) {
            if (error instanceof LinkageError) return "native-linkage";
            if (error instanceof InterruptedException) return "interrupted";
            if (error instanceof IOException) return "io";
            return error instanceof Error ? "java-error" : "refused";
        }
        Map<String, String> event(int sequence, String outcome, String category) {
            Map<String, String> e = new LinkedHashMap<>();
            String[] values = {"1", run, commit, source, provenance, Integer.toString(sequence), Long.toString(System.nanoTime()), phase, outcome, category};
            for (int i = 0; i < FIELDS.length; i++) e.put(FIELDS[i], values[i]);
            return e;
        }
        void writeRecord(String name, Map<String, String> event) throws Exception {
            // Stable closed key=value encoding: every value is a safe fixed token,
            // identity or integer. No exception message or arbitrary text enters it.
            StringBuilder text = new StringBuilder();
            for (String key : FIELDS) text.append(key).append('=').append(event.get(key)).append('\n');
            persist(file(name), text.toString().getBytes(StandardCharsets.UTF_8));
        }
        String save(String name, Map<String, String> event) {
            try { writeRecord(name, event); return "file-synced"; }
            catch (Exception failure) { return "write-failed"; }
        }
        Map<String, String> readRecord(String name, int sequence) throws Exception {
            String text = new String(bounded(new FileInputStream(file(name)), 4096), StandardCharsets.UTF_8);
            String[] lines = text.split("\n", -1);
            require(lines.length == FIELDS.length + 1 && lines[FIELDS.length].isEmpty());
            Map<String, String> e = new LinkedHashMap<>();
            for (int i = 0; i < FIELDS.length; i++) {
                String prefix = FIELDS[i] + "=";
                require(lines[i].startsWith(prefix));
                e.put(FIELDS[i], lines[i].substring(prefix.length()));
            }
            require("1".equals(e.get("schema")) && Integer.toString(sequence).equals(e.get("sequence")));
            // A valid foreign identity is a refusal, never relabeled as this run.
            if (!matches(e.get("run_id"), e.get("source_commit"), e.get("source_sha256"), e.get("native_provenance_sha256"))) throw new IdentityRefusal();
            require(Long.toString(Long.parseLong(e.get("monotonic_ns"))).equals(e.get("monotonic_ns")));
            require(PHASES.contains(e.get("phase")) && CATEGORIES.contains(e.get("category")));
            require(sequence == 1 ? ("started".equals(e.get("outcome")) && "start".equals(e.get("phase")) && "none".equals(e.get("category")))
                : ("success".equals(e.get("outcome")) || "failure".equals(e.get("outcome"))));
            require(!"success".equals(e.get("outcome")) || (sequence == 2 && "terminal-persist".equals(e.get("phase")) && "none".equals(e.get("category"))));
            require(!"failure".equals(e.get("outcome")) || !"none".equals(e.get("category")));
            require(sequence != 3 || ("failure".equals(e.get("outcome")) && "export".equals(e.get("phase"))));
            return e;
        }
        static final class IdentityRefusal extends IOException { private static final long serialVersionUID = 1L; }
        Map<String, String> recoverRecord(String name, int sequence) throws Exception {
            if (!file(name).exists()) return null;
            try { return readRecord(name, sequence); }
            catch (IdentityRefusal foreign) { throw foreign; }
            catch (Exception invalid) { return null; }
        }
        void recover(Validator validator) throws Exception {
            require(file("started").exists() || file("terminal.record").exists() || file("report.json").exists() || file("export-loss.record").exists());
            start = recoverRecord("started", 1); startStorage = start != null ? "readback-valid" : file("started").exists() ? "invalid-or-unreadable" : "absent";
            terminal = recoverRecord("terminal.record", 2); terminalStorage = terminal != null ? "readback-valid" : file("terminal.record").exists() ? "invalid-or-unreadable" : "absent";
            loss = recoverRecord("export-loss.record", 3); lossStorage = loss != null ? "readback-valid" : file("export-loss.record").exists() ? "invalid-or-unreadable" : "absent";
            if (file("report.json").exists()) {
                try {
                    byte[] bytes = bounded(new FileInputStream(file("report.json")), 49152);
                    validator.verify(bytes); report = bytes; reportStorage = "readback-valid";
                } catch (Exception invalid) { report = null; reportStorage = "invalid-or-unreadable"; }
            }
            finished = true;
        }
        void run(Work work, Validator validator) throws Exception {
            // Atomic createNewFile remains the cross-Activity/process replay fence.
            require(!file("started").exists() && !file("report.json").exists() && !file("terminal.record").exists() && !file("export-loss.record").exists());
            start = event(1, "started", "none");
            try {
                startStorage = "write-failed"; writeRecord("started", start); startStorage = "file-synced";
                byte[] bytes = work.observe(this);
                require(bytes.length > 0 && bytes.length <= 49152); validator.verify(bytes);
                report = bytes; phase("report-persist"); reportStorage = "write-failed";
                persist(file("report.json"), bytes); reportStorage = "file-synced";
                phase("terminal-persist"); terminal = event(2, "success", "none");
                terminalStorage = "write-failed"; writeRecord("terminal.record", terminal); terminalStorage = "file-synced";
            } catch (Throwable failure) {
                if (failure instanceof InterruptedException) Thread.currentThread().interrupt();
                terminal = event(2, "failure", category(failure));
                terminalStorage = save("terminal.record", terminal);
            } finally { finished = true; }
        }
        static String quote(String value) {
            StringBuilder out = new StringBuilder("\"");
            for (int i = 0; i < value.length(); i++) {
                char c = value.charAt(i);
                if (c == '"' || c == '\\') out.append('\\').append(c);
                else if (c < 32) out.append(String.format(Locale.ROOT, "\\u%04x", (int)c));
                else out.append(c);
            }
            return out.append('"').toString();
        }
        static String recordJson(String storage, Map<String, String> event) {
            StringBuilder out = new StringBuilder("{\"storage\":").append(quote(storage)).append(",\"event\":");
            if (event == null) return out.append("null}").toString();
            out.append('{');
            for (String key : FIELDS) {
                if (out.charAt(out.length() - 1) != '{') out.append(',');
                out.append(quote(key)).append(':');
                String value = event.get(key);
                out.append(key.equals("schema") || key.equals("sequence") || key.equals("monotonic_ns") ? value : quote(value));
            }
            return out.append("}}").toString();
        }
        byte[] envelope() throws Exception {
            require(finished);
            String reportJson = report == null ? "null" : quote(new String(report, StandardCharsets.UTF_8));
            String digest = report == null ? "null" : quote(sha(report));
            String text = "{\"schema\":1,\"kind\":\"boot-identity-diagnostics-v1\",\"run_id\":" + quote(run)
                + ",\"source_commit\":" + quote(commit) + ",\"source_sha256\":" + quote(source) + ",\"native_provenance_sha256\":" + quote(provenance)
                + ",\"clock\":\"java-nanoTime-process\",\"start\":" + recordJson(startStorage, start) + ",\"terminal\":" + recordJson(terminalStorage, terminal)
                + ",\"export_loss\":" + recordJson(lossStorage, loss) + ",\"report\":{\"storage\":" + quote(reportStorage) + ",\"sha256\":" + digest + ",\"json\":" + reportJson
                + "},\"delivery\":\"unacknowledged\",\"stack_acceptance\":false}";
            byte[] bytes = text.getBytes(StandardCharsets.UTF_8); require(bytes.length <= 65536); return bytes;
        }
        synchronized boolean emit(Sender sender) {
            try { sender.send(envelope(), run); return true; }
            catch (Throwable failure) {
                if (failure instanceof InterruptedException) Thread.currentThread().interrupt();
                if (loss == null && "absent".equals(lossStorage)) {
                    phase = "export"; loss = event(3, "failure", category(failure));
                    lossStorage = save("export-loss.record", loss);
                }
                return false; // No automatic replay, even for failed transport.
            }
        }
    }
    // End diagnostic journal.

    private static void validateRetainedReport(byte[] bytes, JSONObject id, String run, String provenance) throws Exception {
        String text = new String(bytes, StandardCharsets.UTF_8);
        require(Arrays.equals(bytes, text.getBytes(StandardCharsets.UTF_8)));
        JSONObject r = new JSONObject(text);
        keys(r, "schema profile package run_id source_commit source_sha256 native_provenance_sha256 signer_sha256 packaged_abis abi device_api target_sdk min_sdk debuggable java_pid java_uid context native observation_complete runtime_gate stack_acceptance");
        require(run.equals(r.get("run_id")) && provenance.equals(r.get("native_provenance_sha256")) && id.get("source_commit").equals(r.get("source_commit")) && id.get("source_sha256").equals(r.get("source_sha256")));
        require(PACKAGE.equals(r.get("package")) && PROFILE.equals(r.get("profile")) && r.getString("signer_sha256").matches("[0-9a-f]{64}"));
        JSONObject n = r.getJSONObject("native");
        keys(n, "source_sha256 pid uid pages kernel knownness status stage method context untraced_owner_verified errno cleanup_errno failed_sample failed_node samples");
        require(id.get("source_sha256").equals(n.get("source_sha256")) && n.getString("kernel").matches("[a-zA-Z0-9.+_-]{1,128}") && Boolean.FALSE.equals(n.get("untraced_owner_verified")));
        for (String k : new String[]{"pid", "uid", "pages", "knownness", "status", "stage", "method", "context", "errno", "cleanup_errno", "failed_sample", "failed_node"}) require(n.get(k) instanceof Integer || n.get(k) instanceof Long);
        JSONArray samples = n.getJSONArray("samples"); require(samples.length() == 2);
        for (int i = 0; i < samples.length(); i++) {
            JSONObject sample = samples.getJSONObject(i); keys(sample, "origin opened_nodes procfs_checks type_checks mount_checks read_calls bytes_read");
            for (String k : new String[]{"origin", "opened_nodes", "procfs_checks", "type_checks", "mount_checks", "read_calls", "bytes_read"}) require(sample.get(k) instanceof Integer || sample.get(k) instanceof Long);
        }
        JSONObject context = r.getJSONObject("context"); keys(context, "birth_ticks tracer_pid selinux_domain");
        for (String k : new String[]{"birth_ticks", "tracer_pid", "selinux_domain"}) {
            JSONObject f = context.getJSONObject(k); keys(f, "status value");
            if ("observed".equals(f.get("status"))) require(k.equals("selinux_domain") ? f.getString("value").matches("u:r:[a-zA-Z0-9_:,.-]{1,180}") : (f.get("value") instanceof Integer || f.get("value") instanceof Long));
            else require(("unknown".equals(f.get("status")) || "AccessDenied".equals(f.get("status"))) && f.isNull("value"));
        }
        require(Boolean.FALSE.equals(r.get("stack_acceptance")) && Boolean.FALSE.equals(r.get("debuggable")) && Boolean.TRUE.equals(r.get("observation_complete")));
        for (String k : new String[]{"schema", "device_api", "target_sdk", "min_sdk", "java_pid", "java_uid"}) require(r.get(k) instanceof Integer || r.get(k) instanceof Long);
        require(r.getString("abi").matches("x86_64|arm64-v8a") && r.getString("runtime_gate").matches("caller-mount-observed-only|NO-GO-native-unknown"));
        JSONArray abis = r.getJSONArray("packaged_abis"); require(abis.length() > 0 && abis.length() <= 2);
        for (int i = 0; i < abis.length(); i++) require(abis.getString(i).matches("x86_64|arm64-v8a"));
    }

    static boolean dispatch(Activity a, TextView view) {
        return dispatch(a, view, a.getIntent(), false);
    }
    static void dispatchExport(Activity a, TextView view, Intent intent) {
        dispatch(a, view, intent, true);
    }
    private static void showRefusal(Activity a, TextView view, Refusal refusal) {
        boolean emitted = refusal.emit(BootIdentityProbe::export);
        a.runOnUiThread(() -> view.setText("Boot identity request refused at " + refusal.phase
            + (emitted ? "; diagnostic frames submitted (delivery unacknowledged)." : "; diagnostic export failed.")
            + " Volatile evidence only; no observation/recovery acceptance. Preserve this JVM; export-only can retrieve the first matching refusal."));
    }
    private static boolean dispatch(Activity a, TextView view, Intent intent, boolean newIntent) {
        boolean separatePackage = PACKAGE.equals(a.getPackageName());
        if (!separatePackage) {
            try {
                byte[] raw = bounded(a.getAssets().open("runtime/native-provenance.json"), 2 * 1024 * 1024);
                if (!PROFILE.equals(new JSONObject(new String(raw, StandardCharsets.UTF_8)).opt("diagnostic_profile"))) return false;
            } catch (Exception error) { view.setText("Diagnostic metadata unavailable; refused"); return true; }
        }
        // Snapshot extras on the UI thread. Never mutate Activity.intent or read it
        // from a worker: later onNewIntent delivery cannot change this request.
        Map<String, Object> values = new HashMap<>();
        String snapshotFailure = null;
        try {
            Bundle extras = intent == null ? null : intent.getExtras();
            require(extras != null);
            for (String key : extras.keySet()) values.put(key, extras.get(key));
        } catch (Throwable error) { snapshotFailure = Journal.category(error); }
        final Map<String, Object> snapshot = Collections.unmodifiableMap(new HashMap<>(values));
        final String captureFailure = snapshotFailure;
        view.setText(newIntent ? "Export-only request pending; native work is fenced" : "Boot identity observation pending; no recovery authority");
        new Thread(() -> {
            Journal claimed = null;
            Progress progress = new Progress();
            try {
                progress.captureCategory = captureFailure;
                require(captureFailure == null);
                final Request request = new Request(snapshot, newIntent);
                if (request.exportOnly) {
                    final Refusal first;
                    // An in-flight request refusal must never conceal the
                    // original attempt's later terminal/report diagnostics.
                    synchronized (BootIdentityProbe.class) { first = retained == null ? retainedRefusal : null; }
                    if (first != null && first.matches(request)) { showRefusal(a, view, first); return; }
                }
                progress.phase = "metadata";
                final byte[] raw = bounded(a.getAssets().open("runtime/native-provenance.json"), 2 * 1024 * 1024);
                final JSONObject p = new JSONObject(new String(raw, StandardCharsets.UTF_8));
                final JSONObject id = preflight(a, p, raw, request, progress);
                final String run = request.run;
                final String provenance = request.provenance;
                Journal.Validator validator = bytes -> validateRetainedReport(bytes, id, run, provenance);
                final Journal journal;
                if (request.exportOnly) {
                    progress.phase = "retained-export";
                    synchronized (BootIdentityProbe.class) {
                        if (retained == null) {
                            Journal restored = new Journal(a.getFilesDir(), run, id.getString("source_commit"), id.getString("source_sha256"), provenance);
                            restored.recover(validator); retained = restored;
                        }
                        require(retained.matches(run, id.getString("source_commit"), id.getString("source_sha256"), provenance) && retained.finished);
                        journal = retained;
                    }
                    boolean emitted = journal.emit(BootIdentityProbe::export);
                    a.runOnUiThread(() -> view.setText(emitted ? "Retained diagnostics exported; inspect storage-loss facts; no native replay" : "Diagnostic export failed; loss may be memory-only; preserve this JVM"));
                    return;
                }
                progress.phase = "claim";
                synchronized (BootIdentityProbe.class) {
                    require(retained == null);
                    journal = new Journal(a.getFilesDir(), run, id.getString("source_commit"), id.getString("source_sha256"), provenance);
                    retained = journal; claimed = journal;
                }
                journal.run(state -> {
                state.phase("context");
                JSONObject context = new JSONObject().put("birth_ticks", fact("/proc/self/stat", "birth")).put("tracer_pid", fact("/proc/self/status", "tracer")).put("selinux_domain", fact("/proc/self/attr/current", "domain"));
                state.phase("native-load");
                System.loadLibrary("boot_identity_jni");
                state.phase("native-call");
                JSONObject n = new JSONObject(observe());
                require(id.getString("source_sha256").equals(n.getString("source_sha256")));
                state.phase("report-build");
                PackageInfo info = a.getPackageManager().getPackageInfo(a.getPackageName(), PackageManager.GET_SIGNING_CERTIFICATES);
                require(info.signingInfo.getApkContentsSigners().length == 1);
                JSONObject r = new JSONObject().put("schema", 1).put("profile", PROFILE).put("package", a.getPackageName()).put("run_id", run)
                    .put("source_commit", id.get("source_commit")).put("source_sha256", id.get("source_sha256")).put("native_provenance_sha256", sha(raw))
                    .put("signer_sha256", sha(info.signingInfo.getApkContentsSigners()[0].toByteArray())).put("packaged_abis", p.getJSONArray("abis"))
                    .put("abi", a.getApplicationInfo().nativeLibraryDir.contains("arm64") ? "arm64-v8a" : "x86_64")
                    .put("device_api", android.os.Build.VERSION.SDK_INT).put("target_sdk", a.getApplicationInfo().targetSdkVersion).put("min_sdk", a.getApplicationInfo().minSdkVersion)
                    .put("debuggable", false).put("java_pid", android.os.Process.myPid()).put("java_uid", android.os.Process.myUid()).put("context", context).put("native", n)
                    .put("observation_complete", true).put("runtime_gate", n.getInt("status") == 0 ? "caller-mount-observed-only" : "NO-GO-native-unknown").put("stack_acceptance", false);
                return r.toString().getBytes(StandardCharsets.UTF_8);
                }, validator);
                boolean emitted = journal.emit(BootIdentityProbe::export);
                a.runOnUiThread(() -> view.setText(emitted ? "Attempt diagnostics exported; inspect terminal and storage facts; recovery pending" : "Diagnostic export failed; loss may be memory-only; preserve this JVM"));
            } catch (Throwable error) {
                // Preflight/replay/export-identity refusal cannot mutate the
                // original attempt. A failed claim with no start called no JNI.
                if (claimed != null && claimed.start == null) {
                    synchronized (BootIdentityProbe.class) { if (retained == claimed) retained = null; }
                }
                if (error instanceof InterruptedException) Thread.currentThread().interrupt();
                try {
                    Refusal refusal = new Refusal(snapshot, progress, error);
                    synchronized (BootIdentityProbe.class) {
                        if (retainedRefusal == null) { refusal.first = true; retainedRefusal = refusal; }
                    }
                    showRefusal(a, view, refusal);
                } catch (Throwable diagnosticFailure) {
                    if (diagnosticFailure instanceof InterruptedException) Thread.currentThread().interrupt();
                    a.runOnUiThread(() -> view.setText("Boot identity refusal diagnostics failed; no acceptance. Preserve this JVM and original evidence."));
                }
            }
        }, "boot-identity-once").start();
        return true;
    }
}
