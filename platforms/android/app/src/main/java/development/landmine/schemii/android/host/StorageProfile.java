package development.landmine.schemii.android.host;

import android.content.Context;
import android.os.UserManager;
import android.system.Os;
import java.io.*;
import java.nio.file.*;
import java.util.UUID;

/** CE is the only UI-selected profile. DE selection is an explicit future
 * caller contract, with no receiver, migration or pre-PIN acceptance here. */
public final class StorageProfile {
    public final File state, control, sources;
    public final String identity;
    private StorageProfile(File root) throws Exception {
        privateDir(root);
        state = new File(root, "state"); control = new File(root, "control"); sources = new File(root, "sources");
        privateDir(state); privateDir(control); privateDir(sources);
        File id = new File(control, "lineage-v1");
        if (!id.exists()) {
            // O_EXCL preserves partial failures rather than declaring them new.
            String value = UUID.randomUUID()+"\n"+UUID.randomUUID()+"\n"+UUID.randomUUID()+"\n";
            try (OutputStream out = Files.newOutputStream(id.toPath(), StandardOpenOption.CREATE_NEW)) { out.write(value.getBytes("US-ASCII")); }
            Os.chmod(id.getAbsolutePath(),0600);
            try (FileOutputStream out = new FileOutputStream(id, true)) { out.getFD().sync(); }
        }
        if (Files.isSymbolicLink(id.toPath()) || id.length() != 111) throw new IOException("Selected lineage unavailable");
        identity = new String(Files.readAllBytes(id.toPath()), "US-ASCII");
        if (!identity.matches("(?:[a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12}\\n){3}")) throw new IOException("Selected lineage invalid");
    }
    static void privateDir(File dir) throws Exception {
        for (File p = dir.getAbsoluteFile(); p != null; p = p.getParentFile())
            if (Files.isSymbolicLink(p.toPath())) throw new IOException("Selected storage linked");
        if (!dir.isDirectory() && !dir.mkdir()) throw new IOException("Selected storage unavailable");
        Os.chmod(dir.getAbsolutePath(),0700);
    }
    public static StorageProfile select(Context context, boolean explicitDeviceProtected) throws Exception {
        Context selected = context;
        if (explicitDeviceProtected) selected = context.createDeviceProtectedStorageContext();
        else if (!context.getSystemService(UserManager.class).isUserUnlocked()) throw new IOException("Unlock to start");
        return new StorageProfile(new File(selected.getFilesDir(), "connected-host-v1"));
    }
}
