package development.landmine.schemii.android.host;

import android.app.*;
import android.content.*;
import android.content.pm.*;
import android.os.*;
import android.system.*;
import java.io.*;
import java.lang.reflect.Field;
import java.security.MessageDigest;
import java.util.*;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicBoolean;

/** Android lifecycle adapter only. All service/SQL/readiness policy remains in
 * /shared/dev/android/runtime.py. Source gates currently refuse every birth. */
public final class HostService extends Service {
    private final ExecutorService work = Executors.newSingleThreadExecutor();
    private final AtomicBoolean pending = new AtomicBoolean(), stop = new AtomicBoolean();
    private volatile boolean opened;
    public static volatile String statusText = "Stopped — connected runtime acceptance pending";
    private static Object anchor(String field) throws Exception {
        return Class.forName("development.landmine.schemii.android.host.HostAnchors").getField(field).get(null);
    }
    public static boolean available() {
        try { anchor("COMMIT"); return true; } catch (Exception e) { return false; }
    }
    @SuppressWarnings("unchecked") private void prepare() throws Exception {
        String expected = (String)anchor("SIGNER");
        if (!expected.matches("[a-f0-9]{64}")) throw new IOException("Independent signer pin required");
        if ((getApplicationInfo().flags & ApplicationInfo.FLAG_DEBUGGABLE) != 0 || getApplicationInfo().targetSdkVersion != 36
            || !getPackageName().equals(anchor("PACKAGE"))) throw new IOException("Installed package refused");
        PackageInfo info = getPackageManager().getPackageInfo(getPackageName(),PackageManager.GET_SIGNING_CERTIFICATES);
        if (info.signingInfo == null || info.signingInfo.hasMultipleSigners()) throw new IOException("Installed signer refused");
        Signature[] signers = info.signingInfo.getApkContentsSigners();
        if (signers.length != 1 || !InstalledSource.hex(MessageDigest.getInstance("SHA-256").digest(signers[0].toByteArray())).equals(expected)) throw new IOException("Installed signer changed");
        StorageProfile selected = StorageProfile.select(this,false);
        Diagnostics diagnostics = new Diagnostics(selected.control);
        diagnostics.event(1,0,null);
        // Every attempt is fresh; failed compositions stay retained. Never
        // silently reuse an old tree or put control/private state under /shared.
        File source = new File(selected.sources,"source-"+UUID.randomUUID());
        InstalledSource.compose(new File(getApplicationInfo().sourceDir),source,
            (Map<String,String>)anchor("SOURCE"),(Map<String,String>)anchor("ASSETS"));
        if (stop.get()) { statusText = "Stopped before launch — no runtime processes started"; diagnostics.event(3,5,null); return; }
        try (ParcelFileDescriptor borrowed = ParcelFileDescriptor.open(selected.control, ParcelFileDescriptor.MODE_READ_ONLY)) {
            int error = NativeOwner.open(borrowed.getFd());
            if (error != 0) throw new IOException("Native lease unavailable");
            opened = true;
        }
        int result = NativeOwner.request(1);
        diagnostics.event(1,result,NativeOwner.status());
        statusText = "Launch refused — boot caller/custody and PRoot helper closure remain unproved. Data preserved.";
        // No Ready, CLEAN_STOPPED, reboot/recovery permission or grant inferred
        // from a parsed journal, direct wait, live export or private directory.
    }
    @Override public int onStartCommand(Intent intent,int flags,int id) {
        NotificationManager notifications = getSystemService(NotificationManager.class);
        notifications.createNotificationChannel(new NotificationChannel("schemii-host","Schemii server",NotificationManager.IMPORTANCE_LOW));
        Notification notification = new Notification.Builder(this,"schemii-host").setContentTitle("Schemii server")
            .setContentText(statusText).setSmallIcon(android.R.drawable.stat_notify_sync).build();
        startForeground(266,notification);
        String action = intent == null ? "status" : intent.getAction();
        if ("stop".equals(action)) {
            stop.set(true);
            if (opened) NativeOwner.request(2);
            statusText = pending.get() ? "Stopping — source admission pending; no competing launch" : "Stopped — no managed birth admitted";
        } else if ("start".equals(action) && pending.compareAndSet(false,true)) {
            stop.set(false); statusText = "Checking installed source and private storage";
            work.execute(() -> {
                try { if (!opened) prepare(); else NativeOwner.request(1); }
                catch (Exception | LinkageError error) { statusText = "Launch refused — installed source, signer, storage or native admission unavailable. Data preserved."; }
                finally { pending.set(false); }
            });
        }
        return START_NOT_STICKY;
    }
    @Override public void onDestroy() {
        stop.set(true);
        if (opened) {
            int result = NativeOwner.close();
            if (result != 0) statusText = "Owner operation pending — lease retained; restart refused";
        }
        work.shutdown(); super.onDestroy();
    }
    @Override public IBinder onBind(Intent intent) { return null; }
}
