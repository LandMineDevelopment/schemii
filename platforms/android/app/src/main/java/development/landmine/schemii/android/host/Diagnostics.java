package development.landmine.schemii.android.host;

import java.io.*;
import java.nio.file.*;
import java.security.MessageDigest;
import org.json.*;

/** Closed event vocabulary; no command/stdout/path/DSN/identity input. Essential
 * failures never rotate. Pressure or IO failure prevents further Start work. */
final class Diagnostics {
    private final File events;
    private long sequence;
    Diagnostics(File control) { events = new File(control, "owner-events-v1.jsonl"); }
    synchronized void event(int phase, int reason, long[] scalars) throws Exception {
        if (events.exists() && (Files.isSymbolicLink(events.toPath()) || events.length() > 16*1024*1024)) throw new IOException("Diagnostic pressure");
        JSONObject event = new JSONObject().put("schema",1).put("actor","native-owner-control")
            .put("incarnation_sequence",++sequence).put("clock","android-elapsed-realtime-ns")
            .put("monotonic_ns",android.os.SystemClock.elapsedRealtimeNanos())
            .put("phase",phase).put("reason",reason).put("facts","observed-control-result")
            .put("births_observed",0).put("ownership_complete",false).put("loss_known",false)
            .put("stack_ran",false).put("ready",false);
        if (scalars != null) {
            if (scalars.length != 12) throw new IOException("Diagnostic shape refused");
            event.put("native",new JSONArray(scalars));
        }
        try (FileOutputStream out = new FileOutputStream(events,true)) {
            android.system.Os.chmod(events.getAbsolutePath(),0600);
            out.write((event.toString()+"\n").getBytes("UTF-8")); out.getFD().sync();
        }
    }
    synchronized File export(File parent) throws Exception {
        File dest = new File(parent,"diagnostics-"+java.util.UUID.randomUUID());
        StorageProfile.privateDir(dest);
        byte[] raw = events.exists() ? Files.readAllBytes(events.toPath()) : new byte[0];
        if (raw.length > 16*1024*1024) throw new IOException("Diagnostic export pressure");
        File data = new File(dest,"events.jsonl");
        Files.write(data.toPath(),raw,StandardOpenOption.CREATE_NEW); android.system.Os.chmod(data.getAbsolutePath(),0600);
        String sha = InstalledSource.hex(MessageDigest.getInstance("SHA-256").digest(raw));
        JSONObject index = new JSONObject().put("schema",1).put("complete",true).put("live_snapshot",true)
            .put("run_closure",false).put("loss_known",false).put("bytes",raw.length).put("sha256",sha)
            .put("file","events.jsonl").put("scope","source-control-refusal; no runtime logs or boot identities");
        File manifest = new File(dest,"manifest.json");
        Files.write(manifest.toPath(),(index.toString()+"\n").getBytes("UTF-8"),StandardOpenOption.CREATE_NEW);
        android.system.Os.chmod(manifest.getAbsolutePath(),0600);
        if (!java.util.Arrays.equals(raw,Files.readAllBytes(data.toPath()))) throw new IOException("Export readback changed");
        return dest;
    }
}
