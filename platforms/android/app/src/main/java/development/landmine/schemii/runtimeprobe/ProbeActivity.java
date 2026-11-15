package development.landmine.schemii.runtimeprobe;

import android.app.Activity;
import android.os.Bundle;
import android.widget.TextView;
import org.json.JSONArray;
import org.json.JSONObject;
import java.io.*;
import java.nio.file.Files;
import java.util.*;
import java.util.concurrent.TimeUnit;

/** Disposable feasibility entrypoint, deliberately no server/boot service. */
public final class ProbeActivity extends Activity {
    private final List<Process> children = Collections.synchronizedList(new ArrayList<>());
    private volatile boolean closing;

    private JSONObject execute(String name, List<String> command, Map<String, String> env,
                               File dir) throws Exception {
        File output = new File(dir, name + ".txt");
        ProcessBuilder pb = new ProcessBuilder(command).directory(dir).redirectErrorStream(true)
            .redirectOutput(output);
        pb.environment().clear();
        pb.environment().put("PATH", "/system/bin");
        pb.environment().put("HOME", dir.getAbsolutePath());
        pb.environment().put("TMPDIR", dir.getAbsolutePath());
        pb.environment().putAll(env);
        Process p;
        synchronized (children) {
            if (closing) throw new IOException("Activity closing");
            p = pb.start();
            children.add(p);
        }
        boolean done = p.waitFor(45, TimeUnit.SECONDS);
        if (!done) { p.destroyForcibly(); p.waitFor(5, TimeUnit.SECONDS); }
        children.remove(p);
        JSONObject result = new JSONObject();
        result.put("name", name).put("exit", done ? p.exitValue() : -1).put("timeout", !done)
            .put("output_truncated", output.length() > 32768);
        try (FileInputStream stream = new FileInputStream(output)) {
            byte[] bytes = new byte[32768];
            int length = stream.read(bytes);
            result.put("output", new String(bytes, 0, Math.max(length, 0), "UTF-8"));
        }
        return result;
    }

    private static String hex(byte[] bytes) {
        char[] alphabet = "0123456789abcdef".toCharArray();
        StringBuilder result = new StringBuilder(bytes.length * 2);
        for (byte value : bytes) { result.append(alphabet[(value & 255) >>> 4]); result.append(alphabet[value & 15]); }
        return result.toString();
    }

    private static File relative(File root, String name) throws IOException {
        if (name.isEmpty() || name.startsWith("/")) throw new IOException("Absolute/empty payload path");
        for (String part : name.split("/", -1)) {
            if (part.isEmpty() || part.equals(".") || part.equals("..")) throw new IOException("Unsafe payload path");
        }
        return new File(root, name);
    }

    private static void copyChecked(InputStream input, File target, String expected) throws Exception {
        java.security.MessageDigest digest = java.security.MessageDigest.getInstance("SHA-256");
        try (FileOutputStream output = new FileOutputStream(target)) {
            byte[] buffer = new byte[65536];
            for (int n; (n = input.read(buffer)) != -1;) { output.write(buffer, 0, n); digest.update(buffer, 0, n); }
        }
        if (!hex(digest.digest()).equals(expected)) throw new IOException("Packaged payload hash mismatch");
    }

    private JSONObject assetJson(String name) throws Exception {
        try (InputStream input = getAssets().open(name)) {
            ByteArrayOutputStream output = new ByteArrayOutputStream();
            byte[] buffer = new byte[65536];
            for (int n; (n = input.read(buffer)) != -1;) output.write(buffer, 0, n);
            return new JSONObject(output.toString("UTF-8"));
        }
    }

    private void exportReport(JSONObject report) throws Exception {
        if (!assetJson("runtime/native-provenance.json").optBoolean("diagnostic_log_export", false)) {
            throw new IOException("Diagnostic export is disabled in this build");
        }
        String encoded = android.util.Base64.encodeToString(report.toString().getBytes("UTF-8"), android.util.Base64.NO_WRAP);
        int count = (encoded.length() + 1999) / 2000;
        for (int index = 0; index < count; ++index) {
            android.util.Log.i("SchemiiProbe", report.getString("run_id") + ":" + index + "/" + count + ":"
                + encoded.substring(index * 2000, Math.min(encoded.length(), (index + 1) * 2000)));
            // liblog's nonblocking socket drops a burst larger than its send buffer.
            // This explicit emulator diagnostic export is bounded and never retries services.
            Thread.sleep(50);
        }
    }

    private void extractGuest(File root, String abi, JSONObject identity) throws Exception {
        if (!root.mkdir()) throw new IOException("Guest root must be a fresh disposable directory");
        JSONObject manifest = assetJson("runtime/guest-" + abi + "-manifest.json");
        if (!manifest.getString("abi").equals(abi)) throw new IOException("Guest ABI mismatch");
        JSONObject files = manifest.getJSONObject("files");
        // Fresh tree: finish all regular-file writes before creating any guest symlinks.
        Iterator<String> names = files.keys();
        while (names.hasNext()) {
            String name = names.next();
            File target = relative(root, name);
            if (files.getJSONObject(name).getString("kind").equals("directory") && !target.isDirectory() && !target.mkdirs()) {
                throw new IOException("Cannot create guest directory");
            }
        }
        java.security.MessageDigest archiveDigest = java.security.MessageDigest.getInstance("SHA-256");
        Set<String> seen = new HashSet<>();
        try (java.security.DigestInputStream input = new java.security.DigestInputStream(
                getAssets().open("runtime/guest-" + abi + ".zip"), archiveDigest);
             java.util.zip.ZipInputStream zip = new java.util.zip.ZipInputStream(input)) {
            for (java.util.zip.ZipEntry entry; (entry = zip.getNextEntry()) != null;) {
                String name = entry.getName();
                JSONObject record = files.getJSONObject(name);
                if (!record.getString("kind").equals("file") || !seen.add(name)) throw new IOException("Guest inventory drift");
                File target = relative(root, name);
                File parent = target.getParentFile();
                if (!parent.isDirectory() && !parent.mkdirs()) throw new IOException("Cannot create guest file parent");
                // ZipInputStream must stay open between entries; copyChecked does not close input.
                copyChecked(zip, target, record.getString("sha256"));
                if (target.length() != record.getLong("size")) throw new IOException("Guest length drift");
                android.system.Os.chmod(target.getAbsolutePath(), record.getInt("mode"));
                zip.closeEntry();
            }
            byte[] tail = new byte[65536];
            while (input.read(tail) != -1) { /* Include the ZIP central directory in archive identity. */ }
        }
        if (!hex(archiveDigest.digest()).equals(identity.getString("archive_sha256"))) throw new IOException("Guest ZIP drift");
        names = files.keys();
        while (names.hasNext()) {
            String name = names.next();
            JSONObject record = files.getJSONObject(name);
            String kind = record.getString("kind");
            if (kind.equals("file") && !seen.contains(name)) throw new IOException("Guest file missing");
            if (kind.equals("symlink")) {
                File target = relative(root, name);
                File parent = target.getParentFile();
                if (!parent.isDirectory() && !parent.mkdirs()) throw new IOException("Cannot create guest link parent");
                // Absolute targets are guest paths interpreted by PRoot; never follow them for extraction writes.
                Files.createSymbolicLink(target.toPath(), java.nio.file.Paths.get(record.getString("target")));
            } else if (!kind.equals("file") && !kind.equals("directory")) throw new IOException("Unknown guest entry type");
        }
    }

    private JSONObject runGuest(File directory, String lib, String abi, JSONObject build, JSONObject report) throws Exception {
        JSONObject payloads = build.optJSONObject("guest_payloads");
        if (payloads == null || !payloads.has(abi)) return null;
        File run = new File(directory, "g-" + report.getString("run_id").substring(0, 8));
        if (!run.mkdir()) throw new IOException("Guest run must be fresh; preserving prior state");
        File root = new File(run, "r");
        extractGuest(root, abi, payloads.getJSONObject(abi));
        File shared = new File(run, "c");
        if (!shared.mkdir()) throw new IOException("Cannot create shared source root");
        JSONObject source = assetJson("runtime/shared-source.json").getJSONObject("source_files");
        Iterator<String> names = source.keys();
        while (names.hasNext()) {
            String name = names.next();
            File target = relative(shared, name);
            if (!target.getParentFile().isDirectory() && !target.getParentFile().mkdirs()) throw new IOException("Shared extraction failed");
            try (InputStream input = getAssets().open("shared/" + name)) { copyChecked(input, target, source.getString(name)); }
        }
        File state = new File(run, "s");
        if (!state.mkdir()) throw new IOException("Cannot create disposable guest state");
        for (String name : Arrays.asList("tmp", "shm")) if (!new File(state, name).mkdir()) throw new IOException("Cannot create guest temporary directory");
        File script = new File(state, "guest_probe.py");
        try (InputStream input = getAssets().open("runtime/guest_probe.py")) {
            copyChecked(input, script, build.getString("guest_probe_sha256"));
        }
        try (InputStream input = getAssets().open("runtime/runtime-interface.json")) {
            copyChecked(input, new File(state, "runtime-interface.json"), build.getString("runtime_interface_sha256"));
        }
        File temporary = new File(getCacheDir(), "p");
        if (!temporary.isDirectory() && !temporary.mkdir()) throw new IOException("Cannot create PRoot temporary directory");
        List<String> args = new ArrayList<>(Arrays.asList(lib + "/libproot.so", "--sysvipc", "-i", "1000:1000", "-r", root.getAbsolutePath(),
            "-b", "/dev", "-b", "/proc", "-b", state.getAbsolutePath() + ":/state", "-b", shared.getAbsolutePath() + ":/shared",
            "-b", new File(state, "tmp").getAbsolutePath() + ":/tmp", "-b", new File(state, "shm").getAbsolutePath() + ":/dev/shm",
            "-w", "/state", "/usr/bin/env"));
        // Scope the glibc-only adapter to guest processes, never the bionic PRoot/JVM.
        if (abi.equals("x86_64")) args.add("LD_PRELOAD=/opt/libschemii_guest_compat.so");
        args.add("/usr/bin/python3");
        args.add("/state/guest_probe.py");
        File output = new File(state, "guest-private.txt");
        ProcessBuilder pb = new ProcessBuilder(args).directory(state).redirectErrorStream(true).redirectOutput(output);
        Map<String, String> env = pb.environment();
        env.clear();
        env.put("PROOT_LOADER", lib + "/libproot_loader.so");
        env.put("PROOT_TMP_DIR", temporary.getAbsolutePath());
        env.put("PROOT_NO_SECCOMP", "1");
        env.put("PROOT_DONT_SHARE_LIBANDROID_SHMEM", "1");
        env.put("PYTHONPATH", "/opt/python-packages:/shared/src");
        env.put("PYTHONDONTWRITEBYTECODE", "1");
        env.put("PATH", "/opt/node/bin:/usr/lib/postgresql/17/bin:/usr/bin:/bin");
        env.put("HOME", "/state");
        env.put("LANG", "C.UTF-8");
        env.put("LC_ALL", "C.UTF-8");
        env.put("APK_PROBE_RUN_ID", report.getString("run_id"));
        env.put("APK_SOURCE_COMMIT", report.getString("source_commit"));
        env.put("APK_NATIVE_PROVENANCE_SHA256", report.getString("native_provenance_sha256"));
        Process process = pb.start();
        // Never SIGKILL PRoot/PG on a diagnostic deadline. Preserve retained runtime for inspection.
        // This short diagnostic is not a foreground service or owner-loss acceptance.
        boolean done = process.waitFor(300, TimeUnit.SECONDS);
        JSONObject result = new JSONObject().put("name", "real-guest-runtime").put("timeout", !done)
            .put("exit", done ? process.exitValue() : -1).put("state_relative", "g-" + report.getString("run_id").substring(0, 8) + "/s")
            .put("retained_runtime", !done).put("stack_acceptance", false);
        File guestReport = new File(state, "guest-report.json");
        if (guestReport.isFile()) {
            byte[] bytes = Files.readAllBytes(guestReport.toPath());
            if (bytes.length > 65536) throw new IOException("Oversized guest report");
            JSONObject guest = new JSONObject(new String(bytes, "UTF-8"));
            if (!guest.getString("run_id").equals(report.getString("run_id")) ||
                !guest.getString("source_commit").equals(report.getString("source_commit"))) throw new IOException("Stale guest report");
            result.put("report", guest);
        }
        return result;
    }

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        TextView view = new TextView(this);
        view.setPadding(24, 40, 24, 24);
        view.setText("Running disposable native execution controls…");
        setContentView(view);
        if (getIntent().getBooleanExtra("export_report_only", false)) {
            new Thread(() -> {
                try {
                    File saved = new File(getFilesDir(), "probe-v1/report.json");
                    if (!saved.isFile() || saved.length() > 262144) throw new IOException("Stored report absent/oversized");
                    JSONObject report = new JSONObject(new String(Files.readAllBytes(saved.toPath()), "UTF-8"));
                    String expected = getIntent().getStringExtra("expected_native_provenance");
                    if (expected == null || !expected.matches("[0-9a-f]{64}") ||
                        !expected.equals(report.getString("native_provenance_sha256"))) {
                        throw new IOException("Stored report identity mismatch");
                    }
                    exportReport(report);
                    runOnUiThread(() -> view.setText("Exported retained report " + report.optString("run_id")));
                } catch (Exception error) {
                    runOnUiThread(() -> view.setText("Retained report export failed: " + error.toString()));
                }
            }, "retained-report-export").start();
            return; // No probes, state writes or process lifecycle actions in this mode.
        }
        new Thread(() -> {
            JSONObject report = new JSONObject();
            File dir = new File(getFilesDir(), "probe-v1");
            try {
                report.put("run_id", UUID.randomUUID().toString())
                    .put("started_at_ms", System.currentTimeMillis()).put("stack_acceptance", false);
                JSONObject build;
                try (InputStream input = getAssets().open("runtime/native-provenance.json")) {
                    ByteArrayOutputStream bytes = new ByteArrayOutputStream();
                    byte[] buffer = new byte[4096];
                    for (int n; (n = input.read(buffer)) != -1;) bytes.write(buffer, 0, n);
                    build = new JSONObject(bytes.toString("UTF-8"));
                    report.put("native_provenance_sha256", hex(java.security.MessageDigest.getInstance("SHA-256").digest(bytes.toByteArray())));
                    report.put("source_commit", build.getString("source_commit"))
                        .put("packaged_abis", build.getJSONArray("abis"));
                }
                if (!dir.isDirectory() && !dir.mkdir()) throw new IOException("Cannot create probe state");
                Files.deleteIfExists(new File(dir, "report.json").toPath());
                String lib = getApplicationInfo().nativeLibraryDir;
                String probe = lib + "/libruntime_probe.so";
                JSONArray checks = new JSONArray();
                checks.put(execute("packaged-native", Arrays.asList(probe, "identity"),
                                   Collections.emptyMap(), dir));
                checks.put(execute("filesystem-syscalls", Arrays.asList(probe, "filesystem-syscalls"),
                                   Collections.emptyMap(), dir));
                File writable = new File(dir, "writable-probe");
                Files.copy(new File(probe).toPath(), writable.toPath(),
                           java.nio.file.StandardCopyOption.REPLACE_EXISTING);
                if (!writable.setExecutable(true, true)) throw new IOException("Cannot chmod control");
                checks.put(execute("writable-exec", Arrays.asList(probe, "writable-exec", writable.getPath()),
                                   Collections.emptyMap(), dir));
                checks.put(execute("owner-control", Arrays.asList(probe, "owner-control"),
                                   Collections.emptyMap(), dir));
                Map<String, String> env = new HashMap<>();
                env.put("PROOT_LOADER", lib + "/libproot_loader.so");
                env.put("PROOT_TMP_DIR", dir.getAbsolutePath());
                env.put("PROOT_NO_SECCOMP", "1");
                env.put("PROOT_DONT_SHARE_LIBANDROID_SHMEM", "1");
                checks.put(execute("packaged-proot", Arrays.asList(lib + "/libproot.so", "-v", "0",
                    "-r", "/", "-w", dir.getAbsolutePath(), probe, "identity"), env, dir));
                checks.put(execute("proot-owner-control", Arrays.asList(lib + "/libproot.so", "-v", "0",
                    "-r", "/", "-w", dir.getAbsolutePath(), probe, "owner-control"), env, dir));
                // A PRoot-loaded writable ELF is a separate control from direct execve.
                checks.put(execute("proot-writable-guest", Arrays.asList(lib + "/libproot.so", "-v", "0",
                    "-r", "/", "-w", dir.getAbsolutePath(), writable.getPath(), "identity"), env, dir));
                JSONObject components = build.optJSONObject("peer_components");
                if (components != null && components.has("process")) {
                    JSONArray cases = components.getJSONObject("process").getJSONArray("cases");
                    for (int index = 0; index < cases.length(); ++index) {
                        String candidate = cases.getString(index);
                        String executable = lib + "/libschemii_proc_discovery.so";
                        checks.put(execute("process-native-" + candidate,
                            Arrays.asList(executable, "proc-discovery-control", "--case", candidate), Collections.emptyMap(), dir));
                        checks.put(execute("process-proot-" + candidate,
                            Arrays.asList(lib + "/libproot.so", "-v", "0", "-r", "/", "-w", dir.getAbsolutePath(),
                                executable, "proc-discovery-control", "--case", candidate), env, dir));
                    }
                }
                String executedAbi = new JSONObject(checks.getJSONObject(0).getString("output").trim()).getString("abi");
                JSONObject guest = runGuest(dir, lib, executedAbi, build, report);
                if (guest != null) checks.put(guest);
                if (components != null && components.has("network")) {
                    JSONObject network = new JSONObject().put("name", "network-callbacks");
                    try {
                        Class<?> entry = Class.forName("development.landmine.schemii.android.network.NetworkProbe");
                        network.put("report", (JSONObject)entry.getMethod("run", android.content.Context.class)
                            .invoke(null, getApplicationContext()));
                    } catch (Exception | LinkageError error) {
                        network.put("failed", true).put("error", error.toString())
                            .put("cause", error.getCause() == null ? "" : error.getCause().toString());
                    }
                    checks.put(network);
                }
                report.put("debuggable", (getApplicationInfo().flags & android.content.pm.ApplicationInfo.FLAG_DEBUGGABLE) != 0)
                    .put("build_fingerprint", android.os.Build.FINGERPRINT);
                try {
                    report.put("selinux_domain", new String(Files.readAllBytes(java.nio.file.Paths.get("/proc/self/attr/current")), "UTF-8").trim());
                } catch (IOException error) { report.put("selinux_domain", "unknown: " + error.getClass().getSimpleName()); }
                report.put("schema", 1).put("target_sdk", getApplicationInfo().targetSdkVersion)
                    .put("device_api", android.os.Build.VERSION.SDK_INT)
                    .put("abis", new JSONArray(Arrays.asList(android.os.Build.SUPPORTED_ABIS)))
                    .put("checks", checks).put("stack_acceptance", false)
                    .put("pending", "PG17 two clusters, Python extensions, Node/Pi, Go, Moto, pre-PIN tailnet");
            } catch (Exception error) {
                try { report.put("error", error.toString()).put("stack_acceptance", false); }
                catch (Exception ignored) { /* JSONObject keys above are constants. */ }
            }
            try {
                File temporary = new File(dir, "report.tmp");
                try (FileOutputStream out = new FileOutputStream(temporary)) {
                    out.write((report.toString(2) + "\n").getBytes("UTF-8"));
                    out.getFD().sync();
                }
                Files.move(temporary.toPath(), new File(dir, "report.json").toPath(),
                           java.nio.file.StandardCopyOption.ATOMIC_MOVE,
                           java.nio.file.StandardCopyOption.REPLACE_EXISTING);
            } catch (Exception error) {
                try { report.put("report_write_error", error.toString()); }
                catch (Exception ignored) { /* Fixed JSON key. */ }
            }
            try {
                if (assetJson("runtime/native-provenance.json").optBoolean("diagnostic_log_export", false)) {
                    exportReport(report);
                }
            } catch (Exception error) { /* Private report remains authoritative; absent log chunks are incomplete evidence. */ }
            runOnUiThread(() -> view.setText(report.toString()));
        }, "runtime-probes").start();
    }

    @Override protected void onDestroy() {
        closing = true;
        synchronized (children) { for (Process p : children) p.destroyForcibly(); }
        super.onDestroy();
    }
}
