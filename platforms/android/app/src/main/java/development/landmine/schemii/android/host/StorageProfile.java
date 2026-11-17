package development.landmine.schemii.android.host;

import android.content.Context;
import android.os.UserManager;
import android.system.Os;
import java.io.*;
import java.nio.file.*;

/** Noncreating CE-only selection. Native owns object acquisition and all
 * fresh/existing-state decisions. No DE consent producer is implemented. */
public final class StorageProfile {
    public final File state, control, sources;
    private StorageProfile(File data) {
        // File construction is lexical only. No getFilesDir(), lineage read or
        // namespace creation: native admission must acquire actual objects.
        File root = new File(new File(data, "files"), "connected-host-v1");
        state = new File(root, "state"); control = new File(root, "control"); sources = new File(root, "sources");
    }
    static void privateDir(File dir) throws Exception {
        for (File p = dir.getAbsoluteFile(); p != null; p = p.getParentFile())
            if (Files.isSymbolicLink(p.toPath())) throw new IOException("Selected storage linked");
        if (!dir.isDirectory() && !dir.mkdir()) throw new IOException("Selected storage unavailable");
        Os.chmod(dir.getAbsolutePath(),0700);
    }
    public static StorageProfile select(Context context, boolean explicitDeviceProtected) throws Exception {
        if (explicitDeviceProtected) throw new IOException("DE consent producer unavailable");
        if (context == null || context.getApplicationContext() != context)
            throw new IOException("Normal application context required");
        if (context.isDeviceProtectedStorage()) throw new IOException("Unexpected DE default");
        UserManager user = context.getSystemService(UserManager.class);
        if (user == null || !user.isUserUnlocked()) throw new IOException("Unlock to start");
        File data = context.getDataDir();
        if (data == null || !data.isAbsolute() || !data.isDirectory())
            throw new IOException("Selected data root unavailable");
        // This is a nonauthorizing path selection, not no-follow acquisition,
        // freshness, lineage, CE availability or historical custody proof.
        return new StorageProfile(data);
    }
}
