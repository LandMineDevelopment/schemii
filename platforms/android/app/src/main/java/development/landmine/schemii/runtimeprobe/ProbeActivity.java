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
        result.put("name", name).put("exit", done ? p.exitValue() : -1).put("timeout", !done);
        try (FileInputStream stream = new FileInputStream(output)) {
            byte[] bytes = new byte[32768];
            int length = stream.read(bytes);
            result.put("output", new String(bytes, 0, Math.max(length, 0), "UTF-8"));
        }
        return result;
    }

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        TextView view = new TextView(this);
        view.setPadding(24, 40, 24, 24);
        view.setText("Running disposable native execution controls…");
        setContentView(view);
        new Thread(() -> {
            JSONObject report = new JSONObject();
            File dir = new File(getFilesDir(), "probe-v1");
            try {
                report.put("run_id", UUID.randomUUID().toString())
                    .put("started_at_ms", System.currentTimeMillis()).put("stack_acceptance", false);
                try (InputStream input = getAssets().open("runtime/native-provenance.json")) {
                    ByteArrayOutputStream bytes = new ByteArrayOutputStream();
                    byte[] buffer = new byte[4096];
                    for (int n; (n = input.read(buffer)) != -1;) bytes.write(buffer, 0, n);
                    JSONObject build = new JSONObject(bytes.toString("UTF-8"));
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
            runOnUiThread(() -> view.setText(report.toString()));
        }, "runtime-probes").start();
    }

    @Override protected void onDestroy() {
        closing = true;
        synchronized (children) { for (Process p : children) p.destroyForcibly(); }
        super.onDestroy();
    }
}
