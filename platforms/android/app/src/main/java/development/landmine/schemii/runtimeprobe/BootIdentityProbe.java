package development.landmine.schemii.runtimeprobe;

import android.app.Activity;
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
    private static void export(byte[] raw, String run) throws Exception {
        require(raw.length > 0 && raw.length <= 65536);
        String digest = sha(raw);
        int count = (raw.length + 1199) / 1200;
        for (int i = 0; i < count; i++) {
            String chunk = android.util.Base64.encodeToString(Arrays.copyOfRange(raw, i * 1200, Math.min(raw.length, (i + 1) * 1200)), android.util.Base64.NO_WRAP);
            android.util.Log.i("SchemiiBootIdentity", run + ":" + i + "/" + count + ":" + raw.length + ":" + digest + ":" + chunk);
        }
    }
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
    private static JSONObject preflight(Activity a, JSONObject p, byte[] raw) throws Exception {
        keys(p, "schema kind stack_acceptance source_commit adapter_dirty target_sdk min_sdk compile_sdk application_id abis peer_components debuggable diagnostic_log_export wrapper_controls_only wrapper_fixture diagnostic_profile native_files native_donor toolchain_lock_sha256 adapter_sources parity_helper host_tools pending boot_identity");
        require(PACKAGE.equals(a.getPackageName()) && PACKAGE.equals(p.get("application_id")) && PROFILE.equals(p.get("diagnostic_profile")));
        require(Integer.valueOf(1).equals(p.get("schema")) && "disposable-feasibility".equals(p.get("kind")));
        for (String k : new String[]{"adapter_dirty", "debuggable", "wrapper_controls_only", "stack_acceptance"}) require(Boolean.FALSE.equals(p.get(k)));
        require(Boolean.TRUE.equals(p.get("diagnostic_log_export")) && p.isNull("wrapper_fixture") && p.getJSONObject("peer_components").length() == 0 && !p.isNull("native_donor"));
        require(Integer.valueOf(36).equals(p.get("compile_sdk")) && Integer.valueOf(36).equals(p.get("target_sdk")) && Integer.valueOf(29).equals(p.get("min_sdk")));
        ApplicationInfo info = a.getApplicationInfo();
        require((info.flags & ApplicationInfo.FLAG_DEBUGGABLE) == 0 && info.targetSdkVersion == 36 && info.minSdkVersion == 29 && android.os.Build.VERSION.SDK_INT == 36);
        JSONObject id = p.getJSONObject("boot_identity");
        keys(id, "source_commit source_sha256 files");
        require(id.getString("source_commit").matches("[0-9a-f]{40}") && id.getString("source_commit").equals(p.getString("source_commit")) && id.getString("source_sha256").matches("[0-9a-f]{64}"));
        String prefix = "platforms/android/";
        String java = "app/src/main/java/development/landmine/schemii/runtimeprobe/";
        JSONObject files = id.getJSONObject("files");
        String[] names = {prefix + "native/host/boot_identity.c", prefix + "native/host/boot_identity.h", prefix + "native/host/boot_identity_jni.c", prefix + java + "BootIdentityProbe.java", prefix + java + "ProbeActivity.java", prefix + "runtime/check_boot_identity_report.py"};
        keys(files, String.join(" ", names));
        require("854f90067b0838bb9d97108461b6d110426067534550d886a1f926900c95def3".equals(files.get(names[0])) && "b4621a7e4d66a4f2156f5b66943189a7d268f41f667bf4bf41c9f94cddab40a3".equals(files.get(names[1])));
        Arrays.sort(names);
        // Exact canonical source-digest encoding shared with the build checker.
        StringBuilder canonical = new StringBuilder("{");
        for (String name : names) {
            String digest = files.getString(name);
            require(digest.matches("[0-9a-f]{64}") && digest.equals(p.getJSONObject("adapter_sources").getString(name)));
            if (canonical.length() > 1) canonical.append(',');
            canonical.append(JSONObject.quote(name)).append(':').append(JSONObject.quote(digest));
        }
        canonical.append('}');
        require(sha(canonical.toString().getBytes(StandardCharsets.UTF_8)).equals(id.getString("source_sha256")));
        Bundle extras = a.getIntent().getExtras();
        Set<String> allowed = new HashSet<>(Arrays.asList("run_id", "expected_source_commit", "expected_source_sha256", "expected_native_provenance", "export_report_only"));
        require(extras != null && allowed.containsAll(extras.keySet()));
        require(id.getString("source_commit").equals(extras.get("expected_source_commit")) && id.getString("source_sha256").equals(extras.get("expected_source_sha256")) && sha(raw).equals(extras.get("expected_native_provenance")));
        Object run = extras.get("run_id");
        require(run instanceof String && ((String)run).matches("[a-zA-Z0-9_-]{1,80}"));
        if (extras.containsKey("export_report_only")) require(extras.get("export_report_only") instanceof Boolean);
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
        return id;
    }

    static boolean dispatch(Activity a, TextView view) {
        boolean separatePackage = PACKAGE.equals(a.getPackageName());
        byte[] raw;
        JSONObject p;
        try {
            raw = bounded(a.getAssets().open("runtime/native-provenance.json"), 2 * 1024 * 1024);
            p = new JSONObject(new String(raw, StandardCharsets.UTF_8));
        } catch (Exception error) {
            if (!separatePackage) { view.setText("Diagnostic metadata unavailable; refused"); return true; }
            view.setText("Boot identity metadata unavailable; refused"); return true;
        }
        if (!separatePackage && !PROFILE.equals(p.opt("diagnostic_profile"))) return false;
        view.setText("Boot identity observation pending; no recovery authority");
        new Thread(() -> {
            String phase = "preflight";
            try {
                JSONObject id = preflight(a, p, raw);
                String run = a.getIntent().getStringExtra("run_id");
                File saved = new File(a.getFilesDir(), "boot-identity-report.json");
                if (a.getIntent().getBooleanExtra("export_report_only", false)) {
                    byte[] retained = bounded(new FileInputStream(saved), 65536);
                    JSONObject r = new JSONObject(new String(retained, StandardCharsets.UTF_8));
                    require(run.equals(r.get("run_id")) && sha(raw).equals(r.get("native_provenance_sha256")) && id.get("source_commit").equals(r.get("source_commit")));
                    export(retained, run);
                    a.runOnUiThread(() -> view.setText("Retained boot identity report exported"));
                    return;
                }
                // Persistent one-shot fence: Activity recreation cannot replay a campaign.
                require(!saved.exists());
                persist(new File(a.getFilesDir(), "boot-identity-started"),
                    new JSONObject().put("run_id", run).put("source_commit", id.get("source_commit"))
                        .put("source_sha256", id.get("source_sha256")).put("native_provenance_sha256", sha(raw))
                        .put("phase", "before-native").put("sequence", 1).put("monotonic_ns", System.nanoTime())
                        .toString().getBytes(StandardCharsets.UTF_8));
                phase = "context";
                JSONObject context = new JSONObject().put("birth_ticks", fact("/proc/self/stat", "birth")).put("tracer_pid", fact("/proc/self/status", "tracer")).put("selinux_domain", fact("/proc/self/attr/current", "domain"));
                phase = "native-load";
                System.loadLibrary("boot_identity_jni");
                phase = "native-call";
                JSONObject n = new JSONObject(observe());
                require(id.getString("source_sha256").equals(n.getString("source_sha256")));
                PackageInfo info = a.getPackageManager().getPackageInfo(a.getPackageName(), PackageManager.GET_SIGNING_CERTIFICATES);
                require(info.signingInfo.getApkContentsSigners().length == 1);
                JSONObject r = new JSONObject().put("schema", 1).put("profile", PROFILE).put("package", a.getPackageName()).put("run_id", run)
                    .put("source_commit", id.get("source_commit")).put("source_sha256", id.get("source_sha256")).put("native_provenance_sha256", sha(raw))
                    .put("signer_sha256", sha(info.signingInfo.getApkContentsSigners()[0].toByteArray())).put("packaged_abis", p.getJSONArray("abis"))
                    .put("abi", a.getApplicationInfo().nativeLibraryDir.contains("arm64") ? "arm64-v8a" : "x86_64")
                    .put("device_api", android.os.Build.VERSION.SDK_INT).put("target_sdk", a.getApplicationInfo().targetSdkVersion).put("min_sdk", a.getApplicationInfo().minSdkVersion)
                    .put("debuggable", false).put("java_pid", android.os.Process.myPid()).put("java_uid", android.os.Process.myUid()).put("context", context).put("native", n)
                    .put("observation_complete", true).put("runtime_gate", n.getInt("status") == 0 ? "caller-mount-observed-only" : "NO-GO-native-unknown").put("stack_acceptance", false);
                byte[] report = r.toString().getBytes(StandardCharsets.UTF_8);
                phase = "persist";
                persist(saved, report);
                phase = "export";
                export(report, run);
                a.runOnUiThread(() -> view.setText("Boot identity observation captured; owner and recovery gates pending"));
            } catch (Throwable error) {
                // Fixed redacted failure categories; preserve the original fence/report.
                // Even export failure cannot rerun native observation.
                try {
                    persist(new File(a.getFilesDir(), "boot-identity-failure.json"),
                        new JSONObject().put("phase", phase).put("sequence", 2)
                            .put("native_provenance_sha256", sha(raw)).put("monotonic_ns", System.nanoTime())
                            .put("category", error instanceof LinkageError ? "native-linkage" : "refused-or-incomplete")
                            .toString().getBytes(StandardCharsets.UTF_8));
                } catch (Exception storageFailure) { /* UI explicitly retains incomplete status. */ }
                a.runOnUiThread(() -> view.setText("Boot identity refused or incomplete; preserve this JVM and private one-shot evidence"));
            }
        }, "boot-identity-once").start();
        return true;
    }
}
