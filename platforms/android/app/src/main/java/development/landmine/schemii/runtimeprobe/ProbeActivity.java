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
    private static final List<OwnedInvocation> owned = new ArrayList<>();
    private static final List<Process> retainedGuests = new ArrayList<>();
    private volatile boolean closing;
    private volatile Thread probeWorker;
    private File attempt;
    private JSONObject runIdentity;

    private static final class OwnedInvocation {
        final Process process;
        final File journal;
        final File cwd;
        final File output;
        final JSONObject result;
        final CollectorCompletion completion;
        OwnedInvocation(Process process, File journal, File cwd, File output, JSONObject result, CollectorCompletion completion) {
            this.process = process; this.journal = journal; this.cwd = cwd; this.output = output; this.result = result; this.completion = completion;
        }
    }

    private interface Command { List<String> in(File cwd); }

    private static void writeJson(File target, JSONObject value) throws Exception {
        File temporary = new File(target.getParentFile(), target.getName() + ".tmp");
        try (FileOutputStream stream = new FileOutputStream(temporary)) {
            stream.write((value.toString(2) + "\n").getBytes("UTF-8"));
            stream.getFD().sync();
        }
        Files.move(temporary.toPath(), target.toPath(), java.nio.file.StandardCopyOption.ATOMIC_MOVE,
            java.nio.file.StandardCopyOption.REPLACE_EXISTING);
    }

    private static String completeOutput(File output) throws IOException {
        return completeOutput(output, null);
    }

    private static String completeOutput(File output, Runnable duringRead) throws IOException {
        try (FileInputStream input = new FileInputStream(output); ByteArrayOutputStream bytes = new ByteArrayOutputStream()) {
            if (duringRead != null) duringRead.run();
            byte[] buffer = new byte[8192];
            for (int n; (n = input.read(buffer, 0, Math.min(buffer.length, 65537 - bytes.size()))) != -1;) {
                bytes.write(buffer, 0, n);
                if (bytes.size() > 65536) throw new IOException("Output exceeds 65536 bytes; original file retained");
            }
            return java.nio.charset.StandardCharsets.UTF_8.newDecoder()
                .onMalformedInput(java.nio.charset.CodingErrorAction.REPORT)
                .onUnmappableCharacter(java.nio.charset.CodingErrorAction.REPORT)
                .decode(java.nio.ByteBuffer.wrap(bytes.toByteArray())).toString();
        }
    }

    private static JSONObject singleObject(String text) throws org.json.JSONException {
        org.json.JSONTokener tokens = new org.json.JSONTokener(text);
        Object value = tokens.nextValue();
        if (!(value instanceof JSONObject)) throw new org.json.JSONException("Expected one JSON object");
        while (tokens.more()) {
            char c = tokens.next();
            if (c != ' ' && c != '\n' && c != '\r' && c != '\t') throw new org.json.JSONException("Trailing output after JSON object");
        }
        return (JSONObject)value;
    }

    private void requireNoRetainedOwner() throws IOException {
        synchronized (owned) {
            if (closing || Thread.currentThread().isInterrupted() || !owned.isEmpty() || !retainedGuests.isEmpty()) {
                throw new IOException("Owned process/uncertainty retained or Activity closing; refuse another launch");
            }
        }
    }

    private static String fileSha(File path) throws Exception {
        java.security.MessageDigest digest = java.security.MessageDigest.getInstance("SHA-256");
        try (FileInputStream input = new FileInputStream(path)) {
            byte[] buffer = new byte[65536];
            for (int n; (n = input.read(buffer)) != -1;) digest.update(buffer, 0, n);
        }
        return hex(digest.digest());
    }

    private JSONObject execute(String name, List<String> command, Map<String, String> env,
                               File dir) throws Exception {
        return collect(name, cwd -> command, env, "native", 45000);
    }

    private JSONObject executeProot(String name, List<String> guest, Map<String, String> env,
                                    String lib) throws Exception {
        return collect(name, cwd -> {
            List<String> command = new ArrayList<>(Arrays.asList(lib + "/libproot.so", "-v", "0",
                "-r", "/", "-w", cwd.getAbsolutePath()));
            command.addAll(guest);
            return command;
        }, env, "proot", 45000);
    }

    private JSONObject collect(String name, Command factory, Map<String, String> env,
                               String mode, long deadlineMs) throws Exception {
        return collect(name, factory, env, mode, deadlineMs, null);
    }

    private JSONObject collect(String name, Command factory, Map<String, String> env,
                               String mode, long deadlineMs, String cancellationBoundary) throws Exception {
        long started = System.nanoTime();
        requireNoRetainedOwner();
        if (!name.matches("[a-zA-Z0-9_-]+")) throw new IOException("Unsafe invocation name");
        File cwd = new File(new File(attempt, "work"), name);
        File reports = new File(attempt, "reports");
        if (!cwd.mkdir()) throw new IOException("Invocation cwd must be fresh and empty; preserving prior attempt");
        android.system.Os.chmod(cwd.getAbsolutePath(), 0700);
        File output = new File(reports, name + ".txt");
        File journal = new File(reports, name + ".json");
        if (!output.createNewFile() || journal.exists()) throw new IOException("Invocation report already exists");
        List<String> command = factory.in(cwd);
        android.system.StructStat cwdStat = android.system.Os.stat(cwd.getAbsolutePath());
        JSONObject result = new JSONObject().put("name", name).put("mode", mode)
            .put("invocation_id", UUID.randomUUID().toString()).put("run_identity", runIdentity)
            .put("cwd", cwd.getAbsolutePath()).put("cwd_fresh_empty", Objects.requireNonNull(cwd.list()).length == 0)
            .put("cwd_device", cwdStat.st_dev).put("cwd_inode", cwdStat.st_ino)
            .put("output_file", output.getAbsolutePath()).put("deadline_ms", deadlineMs)
            .put("started_at_ms", System.currentTimeMillis()).put("phase", "before-launch")
            .put("command", new JSONArray(command)).put("stack_acceptance", false)
            .put("descendant_cleanup", "unproved by this collector")
            .put("child_identity_authority", "captured Java Process object; Android public API exposes no PID; no PID/UID/group admission");
        writeJson(journal, result);
        ProcessBuilder pb = new ProcessBuilder(command).directory(cwd).redirectErrorStream(true)
            .redirectOutput(output);
        pb.environment().clear();
        pb.environment().put("PATH", "/system/bin");
        pb.environment().put("HOME", cwd.getAbsolutePath());
        pb.environment().put("TMPDIR", cwd.getAbsolutePath());
        pb.environment().putAll(env);
        Process p;
        OwnedInvocation entry;
        synchronized (owned) {
            if (closing || Thread.currentThread().isInterrupted()) throw new IOException("Activity closing or collector cancelled before launch");
            if (!owned.isEmpty() || !retainedGuests.isEmpty()) throw new IOException("Prior owned process/uncertainty retained; refuse another launch");
            p = pb.start();
            entry = new OwnedInvocation(p, journal, cwd, output, result, new CollectorCompletion(owned, () -> closing));
            owned.add(entry);
        }
        result.put("phase", "waiting").put("retained_ownership", true);
        writeJson(journal, result);
        boolean done = false, cancelled = false;
        long remaining = Math.max(0, deadlineMs - TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - started));
        try { done = p.waitFor(remaining, TimeUnit.MILLISECONDS); }
        catch (InterruptedException error) { cancelled = true; Thread.currentThread().interrupt(); }
        cancelled = cancelled || closing;
        result.put("exit", done ? p.exitValue() : -1).put("timeout", !done && !cancelled)
            .put("cancelled", cancelled).put("direct_child_exit_observed", done)
            .put("process_alive_after_wait", p.isAlive())
            .put("termination_attempted", false).put("output_accepted", false)
            .put("output_truncated", false).put("output_overflow", false)
            .put("output_bytes_observed", output.length()).put("phase", done && !cancelled ? "direct-child-exited" : "retained-uncertain");
        boolean committed = false;
        if (done && !cancelled) {
            JSONObject candidate = new JSONObject(result.toString());
            committed = entry.completion.finish(() -> {
                try {
                    String text = completeOutput(output, () -> cancelAtBoundary(cancellationBoundary, "read", entry.completion));
                    singleObject(text); // No valid prefix, malformed UTF8 or partial JSON.
                    candidate.put("output", text).put("output_accepted", true)
                        .put("output_sha256", hex(java.security.MessageDigest.getInstance("SHA-256").digest(text.getBytes("UTF-8"))));
                } catch (IOException | org.json.JSONException error) {
                    boolean overflow = output.length() > 65536;
                    candidate.put("output_error", error.toString()).put("output_overflow", overflow).put("output_truncated", overflow);
                }
            }, () -> {
                // A prepared journal is never evidence of accepted output or
                // owner release, including across a crash before memory commit.
                JSONObject prepared = new JSONObject(result.toString())
                    .put("phase", "completion-prepared").put("completion_state", "prepared")
                    .put("journal_outcome", "provisional-no-output-acceptance")
                    .put("elapsed_ms", TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - started));
                writeJson(journal, prepared);
                cancelAtBoundary(cancellationBoundary, "journal", entry.completion);
            }, () -> {
                Iterator<String> keys = candidate.keys();
                while (keys.hasNext()) { String key = keys.next(); result.put(key, candidate.get(key)); }
                result.put("retained_ownership", false).put("completion_state", "committed")
                    .put("journal_outcome", "provisional-no-output-acceptance")
                    .put("elapsed_ms", TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - started));
                owned.remove(entry);
            });
        }
        if (!committed) {
            cancelled = cancelled || entry.completion.cancelled();
            result.remove("output"); result.remove("output_sha256");
            result.put("cancelled", cancelled).put("timeout", !done && !cancelled)
                .put("phase", "retained-uncertain").put("completion_state", "retained")
                .put("retained_ownership", true).put("output_accepted", false)
                .put("output_error", "Unfinished/cancelled owner: output not accepted; process reference, files and uncertainty retained")
                .put("next_action", "Stop launching; independently reconcile retained owner and descendants. No automatic signal, restart or cleanup.")
                .put("elapsed_ms", TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - started));
            writeJson(journal, result);
        }
        return result;
    }

    /** Wrapper-only latch control: cancel while real read/journal handling is in flight. */
    private static void cancelAtBoundary(String selected, String stage, CollectorCompletion completion) {
        if (!stage.equals(selected)) return;
        java.util.concurrent.CountDownLatch entered = new java.util.concurrent.CountDownLatch(1);
        java.util.concurrent.CountDownLatch cancelled = new java.util.concurrent.CountDownLatch(1);
        Thread collector = Thread.currentThread();
        Thread controller = new Thread(() -> {
            try {
                entered.await();
                completion.cancel();
                collector.interrupt();
            } catch (InterruptedException error) { Thread.currentThread().interrupt(); }
            finally { cancelled.countDown(); }
        }, "finite-late-cancel-" + stage);
        controller.start();
        entered.countDown();
        boolean interrupted = false;
        // Simulate ordinary file I/O that may complete despite interruption.
        for (;;) {
            try { cancelled.await(); controller.join(); break; }
            catch (InterruptedException error) { interrupted = true; }
        }
        if (interrupted) collector.interrupt();
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
        requireNoRetainedOwner();
        Process process = pb.start();
        // Never SIGKILL PRoot/PG on a diagnostic deadline. Preserve retained runtime for inspection.
        // This short diagnostic is not a foreground service or owner-loss acceptance.
        boolean done = false;
        try { done = process.waitFor(300, TimeUnit.SECONDS); }
        catch (InterruptedException error) { Thread.currentThread().interrupt(); }
        if (!done) synchronized (owned) { retainedGuests.add(process); }
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

    private void finiteFixtureOracle(JSONObject result) throws Exception {
        OwnedInvocation entry = null;
        synchronized (owned) {
            for (OwnedInvocation candidate : owned) {
                if (candidate.result == result) entry = candidate;
            }
        }
        if (entry == null) throw new IOException("Finite fixture owner reference missing");
        // Only this test harness clears its deliberately injected interruption.
        // The collector's cancelled/deadline result is never relabeled successful.
        boolean interrupted = Thread.interrupted();
        JSONObject oracle = new JSONObject().put("scope", "known finite one-child fixture only")
            .put("cancel_flag_observed", interrupted).put("signals_sent", false);
        result.put("fixture_oracle", oracle);
        boolean done = entry.process.waitFor(5, TimeUnit.SECONDS);
        oracle.put("direct_owner_exit_observed", done);
        if (done) oracle.put("direct_owner_exit", entry.process.exitValue());
        if (!done) {
            writeJson(entry.journal, result);
            throw new IOException("Finite fixture did not finish; owner/uncertainty retained, stop controls");
        }
        JSONObject parent = singleObject(completeOutput(entry.output));
        JSONObject child = singleObject(completeOutput(new File(entry.cwd, "child.done")));
        boolean matched = entry.process.exitValue() == 0 && parent.getString("fixture").equals("family")
            && parent.getInt("owned_children") == 1 && parent.getBoolean("specific_child_reaped")
            && parent.getInt("child_status") == 0 && child.getBoolean("finite_child_done")
            && parent.getString("source_id").equals(runIdentity.getString("fixture_source_sha256"))
            && parent.getLong("child_pid") == child.getLong("pid")
            && parent.getLong("child_start_ticks") == child.getLong("start_ticks")
            && child.getLong("start_ticks") > 0;
        oracle.put("parent", parent).put("child", child).put("exact_finite_fixture_cleanup", matched)
            .put("general_descendant_completeness", false);
        result.put("fixture_reference_released_after_oracle", matched);
        writeJson(entry.journal, result);
        if (!matched) throw new IOException("Finite fixture cleanup mismatch; retain owner/uncertainty");
        synchronized (owned) { owned.remove(entry); }
    }

    private void wrapperControls(JSONArray checks, JSONObject report, String lib, JSONObject build) throws Exception {
        String fixture = lib + "/libcollector_fixture.so";
        Set<String> cwds = new HashSet<>();
        for (String name : Arrays.asList("wrapper-cwd-one", "wrapper-cwd-two")) {
            JSONObject value = execute(name, Arrays.asList(fixture, "cwd"), Collections.emptyMap(), attempt);
            checks.put(value);
            JSONObject observed = singleObject(value.getString("output"));
            boolean ok = value.getBoolean("cwd_fresh_empty") && observed.getBoolean("empty_before_launch")
                && value.getLong("cwd_device") == observed.getLong("cwd_device")
                && value.getLong("cwd_inode") == observed.getLong("cwd_inode") && cwds.add(observed.getString("cwd"))
                && observed.getString("source_id").equals(runIdentity.getString("fixture_source_sha256"));
            String installed = fileSha(new File(fixture));
            ok &= installed.equals(build.getJSONObject("native_files").getString("lib/" + observed.getString("abi") + "/libcollector_fixture.so"));
            report.put("fixture_executable_sha256", installed);
            value.put("fixture_control_pass", ok);
            if (!ok) throw new IOException("Fresh cwd control failed");
        }
        Map<String, String> env = new HashMap<>();
        env.put("PROOT_LOADER", lib + "/libproot_loader.so");
        env.put("PROOT_TMP_DIR", new File(attempt, "proot-tmp").getAbsolutePath());
        env.put("PROOT_NO_SECCOMP", "1"); env.put("PROOT_DONT_SHARE_LIBANDROID_SHMEM", "1");
        JSONObject proot = executeProot("wrapper-proot-cwd", Arrays.asList(fixture, "cwd"), env, lib);
        checks.put(proot);
        JSONObject observed = singleObject(proot.getString("output"));
        boolean fresh = proot.getBoolean("cwd_fresh_empty") && observed.getBoolean("empty_before_launch")
            && proot.getLong("cwd_device") == observed.getLong("cwd_device")
            && proot.getLong("cwd_inode") == observed.getLong("cwd_inode") && cwds.add(observed.getString("cwd"));
        proot.put("fixture_control_pass", fresh);
        if (!fresh) throw new IOException("PRoot cwd control failed");
        JSONObject exact = execute("wrapper-exact-64k", Arrays.asList(fixture, "bytes", "65536"), Collections.emptyMap(), attempt);
        checks.put(exact);
        exact.put("fixture_control_pass", exact.getBoolean("output_accepted") && exact.getLong("output_bytes_observed") == 65536
            && exact.getString("output").getBytes("UTF-8").length == 65536);
        for (String kind : Arrays.asList("overflow", "partial", "trailing")) {
            List<String> args = kind.equals("overflow") ? Arrays.asList(fixture, "bytes", "65537") : Arrays.asList(fixture, kind);
            JSONObject value = execute("wrapper-" + kind, args, Collections.emptyMap(), attempt);
            checks.put(value);
            value.put("fixture_control_pass", !value.getBoolean("output_accepted") && !value.has("output")
                && (kind.equals("overflow") == value.getBoolean("output_overflow")));
        }
        JSONObject deadline = collect("wrapper-deadline", cwd -> Arrays.asList(fixture, "family"), Collections.emptyMap(), "native-fixture", 250);
        checks.put(deadline);
        boolean timed = deadline.getBoolean("timeout") && deadline.getBoolean("retained_ownership")
            && !deadline.getBoolean("output_accepted") && !deadline.getBoolean("termination_attempted")
            && deadline.getLong("elapsed_ms") >= 250;
        boolean blocked = false;
        try { execute("wrapper-must-not-launch", Arrays.asList(fixture, "cwd"), Collections.emptyMap(), attempt); }
        catch (IOException expected) { blocked = true; }
        deadline.put("new_launch_refused_while_retained", blocked);
        timed &= blocked;
        finiteFixtureOracle(deadline);
        deadline.put("fixture_control_pass", timed);
        Thread collector = Thread.currentThread();
        Thread cancellation = new Thread(() -> {
            try { Thread.sleep(250); collector.interrupt(); }
            catch (InterruptedException error) { Thread.currentThread().interrupt(); }
        }, "finite-fixture-cancel");
        cancellation.start();
        JSONObject cancelled;
        try {
            cancelled = collect("wrapper-cancel", cwd -> Arrays.asList(fixture, "family"), Collections.emptyMap(), "native-fixture", 45000);
            checks.put(cancelled);
        } finally {
            boolean interrupted = Thread.interrupted();
            cancellation.join();
            if (interrupted) Thread.currentThread().interrupt();
        }
        boolean stopped = cancelled.getBoolean("cancelled") && cancelled.getBoolean("retained_ownership")
            && !cancelled.getBoolean("output_accepted") && !cancelled.getBoolean("termination_attempted");
        boolean cancelBlocked = false;
        try { execute("wrapper-must-not-launch-cancel", Arrays.asList(fixture, "cwd"), Collections.emptyMap(), attempt); }
        catch (IOException expected) { cancelBlocked = true; }
        cancelled.put("new_launch_refused_while_retained", cancelBlocked);
        stopped &= cancelBlocked;
        finiteFixtureOracle(cancelled);
        cancelled.put("fixture_control_pass", stopped);
        report.put("completion_protocol", "provisional-journal-v1");
        for (String stage : Arrays.asList("read", "journal")) {
            JSONObject late = collect("wrapper-late-" + stage, cwd -> Arrays.asList(fixture, "family"),
                Collections.emptyMap(), "native-fixture", 45000, stage);
            checks.put(late);
            boolean refused = late.getBoolean("cancelled") && !late.getBoolean("timeout")
                && late.getBoolean("direct_child_exit_observed") && late.getInt("exit") == 0
                && !late.getBoolean("process_alive_after_wait") && late.getBoolean("retained_ownership")
                && !late.getBoolean("output_accepted") && !late.has("output");
            boolean fenced = false;
            try { execute("wrapper-late-must-not-launch-" + stage, Arrays.asList(fixture, "cwd"), Collections.emptyMap(), attempt); }
            catch (IOException expected) { fenced = true; }
            late.put("new_launch_refused_while_retained", fenced).put("cancellation_boundary", stage);
            finiteFixtureOracle(late);
            late.put("fixture_control_pass", refused && fenced);
        }
        boolean all = true;
        for (int i = 0; i < checks.length(); ++i) all &= checks.getJSONObject(i).getBoolean("fixture_control_pass");
        report.put("wrapper_fixture_controls_pass", all).put("production_ownership", false)
            .put("owner_death_solution", false);
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
        probeWorker = new Thread(() -> {
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
                File previous = new File(dir, "report.json");
                if (previous.isFile()) {
                    JSONObject saved = singleObject(new String(Files.readAllBytes(previous.toPath()), "UTF-8"));
                    String id = saved.getString("run_id");
                    if (!id.matches("[0-9a-f-]{36}")) throw new IOException("Prior report identity invalid; preserving it");
                    File history = new File(dir, "history");
                    if (!history.isDirectory() && !history.mkdir()) throw new IOException("Cannot preserve prior report");
                    File target = new File(history, id + ".json");
                    if (!target.exists()) Files.copy(previous.toPath(), target.toPath());
                }
                report.put("debuggable", (getApplicationInfo().flags & android.content.pm.ApplicationInfo.FLAG_DEBUGGABLE) != 0)
                    .put("build_fingerprint", android.os.Build.FINGERPRINT).put("schema", 1)
                    .put("target_sdk", getApplicationInfo().targetSdkVersion).put("device_api", android.os.Build.VERSION.SDK_INT)
                    .put("abis", new JSONArray(Arrays.asList(android.os.Build.SUPPORTED_ABIS)));
                try { report.put("selinux_domain", new String(Files.readAllBytes(java.nio.file.Paths.get("/proc/self/attr/current")), "UTF-8").trim()); }
                catch (IOException error) { report.put("selinux_domain", "unknown: " + error.getClass().getSimpleName()); }
                JSONArray signatures = new JSONArray();
                android.content.pm.PackageInfo packageInfo = getPackageManager().getPackageInfo(getPackageName(), android.content.pm.PackageManager.GET_SIGNING_CERTIFICATES);
                for (android.content.pm.Signature signature : packageInfo.signingInfo.getApkContentsSigners()) {
                    signatures.put(hex(java.security.MessageDigest.getInstance("SHA-256").digest(signature.toByteArray())));
                }
                report.put("package_signer_sha256", signatures);
                report.put("installed_apk_sha256", fileSha(new File(getApplicationInfo().sourceDir)));
                runIdentity = new JSONObject().put("run_id", report.getString("run_id"))
                    .put("source_commit", report.getString("source_commit"))
                    .put("native_provenance_sha256", report.getString("native_provenance_sha256"))
                    .put("debuggable", report.getBoolean("debuggable")).put("target_sdk", getApplicationInfo().targetSdkVersion)
                    .put("device_api", android.os.Build.VERSION.SDK_INT).put("java_owner_pid", android.os.Process.myPid())
                    .put("java_owner_uid", android.os.Process.myUid()).put("package_signer_sha256", signatures)
                    .put("installed_apk_sha256", report.getString("installed_apk_sha256"))
                    .put("build_fingerprint", report.getString("build_fingerprint")).put("selinux_domain", report.getString("selinux_domain"));
                String selfStat = new String(Files.readAllBytes(java.nio.file.Paths.get("/proc/self/stat")), "UTF-8");
                String[] selfFields = selfStat.substring(selfStat.lastIndexOf(')') + 2).trim().split("\\s+");
                runIdentity.put("java_owner_start_ticks", Long.parseLong(selfFields[19]))
                    .put("boot_id", new String(Files.readAllBytes(java.nio.file.Paths.get("/proc/sys/kernel/random/boot_id")), "UTF-8").trim());
                attempt = new File(dir, "attempt-" + (report.getBoolean("debuggable") ? "debug-" : "nondebug-") + report.getString("run_id"));
                if (!attempt.mkdir()) throw new IOException("Attempt must be fresh; preserving prior state");
                android.system.Os.chmod(attempt.getAbsolutePath(), 0700);
                for (String name : Arrays.asList("work", "reports", "proot-tmp")) {
                    File child = new File(attempt, name);
                    if (!child.mkdir()) throw new IOException("Cannot create attempt root");
                    android.system.Os.chmod(child.getAbsolutePath(), 0700);
                }
                report.put("attempt", attempt.getAbsolutePath());
                String lib = getApplicationInfo().nativeLibraryDir;
                String probe = lib + "/libruntime_probe.so";
                JSONArray checks = new JSONArray();
                report.put("checks", checks);
                if (build.optBoolean("wrapper_controls_only", false)) {
                    runIdentity.put("fixture_source_sha256", build.getJSONObject("wrapper_fixture").getString("source_sha256"));
                    wrapperControls(checks, report, lib, build);
                } else {
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
                checks.put(executeProot("packaged-proot", Arrays.asList(probe, "identity"), env, lib));
                checks.put(executeProot("proot-owner-control", Arrays.asList(probe, "owner-control"), env, lib));
                // A PRoot-loaded writable ELF is a separate control from direct execve.
                checks.put(executeProot("proot-writable-guest", Arrays.asList(writable.getPath(), "identity"), env, lib));
                JSONObject components = build.optJSONObject("peer_components");
                if (components != null && components.has("process")) {
                    JSONArray cases = components.getJSONObject("process").getJSONArray("cases");
                    for (int index = 0; index < cases.length(); ++index) {
                        String candidate = cases.getString(index);
                        String executable = lib + "/libschemii_proc_discovery.so";
                        checks.put(execute("process-native-" + candidate,
                            Arrays.asList(executable, "proc-discovery-control", "--case", candidate), Collections.emptyMap(), dir));
                        checks.put(executeProot("process-proot-" + candidate,
                            Arrays.asList(executable, "proc-discovery-control", "--case", candidate), env, lib));
                    }
                }
                requireNoRetainedOwner();
                String executedAbi = new JSONObject(checks.getJSONObject(0).getString("output").trim()).getString("abi");
                JSONObject guest = runGuest(dir, lib, executedAbi, build, report);
                if (guest != null) checks.put(guest);
                if (components != null && components.has("network")) {
                    JSONObject network = new JSONObject().put("name", "network-callbacks");
                    try {
                        Class<?> entry = Class.forName("development.landmine.schemii.android.network.NetworkProbe");
                        JSONObject observed = (JSONObject)entry.getMethod("run", android.content.Context.class)
                            .invoke(null, getApplicationContext());
                        network.put("report", observed); // Preserve a mismatching report as failed evidence.
                        String expected = components.getJSONObject("network").getString("bridge_sha256");
                        if (!expected.equals(observed.getString("native_bridge_sha256"))) {
                            throw new IOException("Network runtime bridge identity mismatch");
                        }
                        network.put("bridge_identity_verified", true);
                    } catch (Exception | LinkageError error) {
                        network.put("failed", true).put("error", error.toString())
                            .put("cause", error.getCause() == null ? "" : error.getCause().toString());
                    }
                    checks.put(network);
                }
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
                JSONArray retained = new JSONArray();
                synchronized (owned) {
                    for (OwnedInvocation entry : owned) retained.put(entry.result);
                    report.put("retained_guest_owners", retainedGuests.size());
                }
                report.put("retained_invocations", retained);
                if (attempt != null) writeJson(new File(attempt, "report.json"), report);
                writeJson(new File(dir, "report.json"), report);
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
        }, "runtime-probes");
        probeWorker.start();
    }

    @Override protected void onDestroy() {
        synchronized (owned) {
            closing = true;
            for (OwnedInvocation entry : owned) entry.completion.cancel();
        }
        // Interrupt collection, never kill an owner or discard its descendants.
        Thread worker = probeWorker;
        if (worker != null) worker.interrupt();
        super.onDestroy();
    }
}
