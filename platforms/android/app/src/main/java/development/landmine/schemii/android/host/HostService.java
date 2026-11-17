package development.landmine.schemii.android.host;

import android.app.*;
import android.content.*;
import android.content.pm.*;
import android.os.*;
import java.io.*;
import java.security.MessageDigest;
import java.util.concurrent.*;

/** Android lifecycle adapter only. C/A are unproved; every birth is refused. */
public final class HostService extends Service {
    private final ExecutorService work = Executors.newSingleThreadExecutor();
    // Protect only reference/state transitions. Never hold across SDK, native,
    // executor dispatch, filesystem calls or a blocking join.
    private static final Object lifecycle = new Object();
    private static HostService active;
    private static Operation outstanding;
    private Context application;
    private boolean created, destroyed, replaced;

    private static final class Acquisition {
        final Context application, selected;
        final Thread worker;
        Acquisition(Context application) {
            this.application = application; this.selected = application;
            this.worker = Thread.currentThread();
        }
    }
    private static final class Operation {
        final HostService service;
        final Context application;
        boolean cancelled, failed;
        Acquisition acquisition;
        String refusal = "Launch refused — private storage unavailable. Data preserved.";
        Operation(HostService service) {
            this.service = service; this.application = service.application;
        }
    }
    @Override public void onCreate() {
        super.onCreate();
        Context actual = getApplicationContext();
        synchronized (lifecycle) {
            if (created || destroyed) return; // never revive this incarnation
            created = true; application = actual;
            if (active != null) {
                active.replaced = true;
                if (outstanding != null) outstanding.cancelled = true;
            }
            active = this;
        }
    }
    private static boolean live(Operation operation) {
        // lifecycle must be held. The reservation survives cancellation/destroy
        // until its sole worker returns; another service cannot steal it.
        return outstanding == operation && active == operation.service
            && operation.service.created && !operation.service.destroyed
            && !operation.service.replaced && !operation.cancelled;
    }
    private static Acquisition callbackAcquisition() {
        Operation operation = outstanding;
        if (operation == null || !live(operation)) return null;
        Acquisition acquisition = operation.acquisition;
        return acquisition != null && acquisition.worker == Thread.currentThread() ? acquisition : null;
    }
    // Fixed JNI candidates only. No public registry/setter, Context argument,
    // digest, identity, trust field or receipt. Both lookups must occur on the
    // same acquiring worker, synchronously. No asynchronous/global JNI refs are
    // created here; a later native consumer must own/invalidate its own borrows.
    static Context frameworkOwnerApplication() {
        synchronized (lifecycle) {
            Acquisition acquisition = callbackAcquisition();
            return acquisition == null ? null : acquisition.application;
        }
    }
    static Context frameworkSelectedStorage() {
        synchronized (lifecycle) {
            Acquisition acquisition = callbackAcquisition();
            return acquisition == null ? null : acquisition.selected;
        }
    }
    public static volatile String statusText = "Launch unavailable — native admission unproved; data preserved";
    public static String status() { return statusText; }
    private static Object anchor(String field) throws Exception {
        return Class.forName("development.landmine.schemii.android.host.HostAnchors").getField(field).get(null);
    }
    public static boolean available() {
        try { anchor("COMMIT"); return true; } catch (Exception e) { return false; }
    }
    private void prepare(Operation operation) throws Exception {
        synchronized (lifecycle) { if (!live(operation)) return; }
        StorageProfile.select(operation.application, false);
        synchronized (lifecycle) {
            if (!live(operation)) return;
            operation.acquisition = new Acquisition(operation.application);
            operation.refusal = "Launch refused — installed source or signer unavailable. Data preserved.";
        }
        // Retain the existing preliminary signer/profile refusal checks. These
        // Java observations grant no C/A and are not exported to native.
        Context app = operation.application;
        String expected = (String)anchor("SIGNER");
        if (!expected.matches("[a-f0-9]{64}")) throw new IOException("Independent signer pin required");
        if ((app.getApplicationInfo().flags & ApplicationInfo.FLAG_DEBUGGABLE) != 0 || app.getApplicationInfo().targetSdkVersion != 36
            || !app.getPackageName().equals(anchor("PACKAGE"))) throw new IOException("Installed package refused");
        PackageInfo info = app.getPackageManager().getPackageInfo(app.getPackageName(),PackageManager.GET_SIGNING_CERTIFICATES);
        if (info.signingInfo == null || info.signingInfo.hasMultipleSigners()) throw new IOException("Installed signer refused");
        Signature[] signers = info.signingInfo.getApkContentsSigners();
        if (signers.length != 1 || !InstalledSource.hex(MessageDigest.getInstance("SHA-256").digest(signers[0].toByteArray())).equals(expected)) throw new IOException("Installed signer changed");
        synchronized (lifecycle) {
            if (!live(operation)) return;
            operation.refusal = "Launch refused — native acquisition disabled; caller and custody unproved. Data preserved.";
        }
        // Deliberately no NativeOwner.open(int)/request, Diagnostics, compose,
        // directory creation or lineage initialization. The no-argument native
        // consumer, held APK/module/root checks and admission are serialized
        // dependencies. Do not bridge them with a borrowed FD or Java claim.
    }
    private void finish(Operation operation) {
        synchronized (lifecycle) {
            if (outstanding != operation) return;
            // Only Java references were acquired. No native close/cleanup can
            // be inferred or attempted. Native integration must retain first
            // and cleanup errors separately and retain unresolved resources.
            operation.acquisition = null;
            outstanding = null;
            boolean cancelled = operation.cancelled || operation.service.destroyed || operation.service.replaced;
            statusText = operation.failed ? operation.refusal + (cancelled ? " Cancellation also observed." : "")
                : cancelled ? "Preparation cancelled — no native acquisition; ownership/recovery unproved. Data preserved."
                : operation.refusal;
        }
    }
    @Override public int onStartCommand(Intent intent,int flags,int id) {
        NotificationManager notifications = getSystemService(NotificationManager.class);
        notifications.createNotificationChannel(new NotificationChannel("schemii-host","Schemii server",NotificationManager.IMPORTANCE_LOW));
        Notification notification = new Notification.Builder(this,"schemii-host").setContentTitle("Schemii server")
            .setContentText(statusText).setSmallIcon(android.R.drawable.stat_notify_sync).build();
        startForeground(266,notification);
        String action = intent == null ? "status" : intent.getAction();
        Operation operation;
        synchronized (lifecycle) {
            if ("stop".equals(action)) {
                if (!created || destroyed || replaced || active != this) return START_NOT_STICKY;
                if (outstanding != null && outstanding.service == this) outstanding.cancelled = true;
                statusText = outstanding == null
                    ? "Stop observed — no native acquisition; ownership/recovery unproved"
                    : "Stop observed — preparation pending; reservation retained";
                return START_NOT_STICKY;
            }
            if (!"start".equals(action)) return START_NOT_STICKY;
            if (!created || destroyed || replaced || active != this || outstanding != null) {
                statusText = "Launch refused — stale service or preparation pending; reservation retained";
                return START_NOT_STICKY;
            }
            operation = new Operation(this);
            outstanding = operation;
            statusText = "Checking CE storage and installed profile — native acquisition disabled";
        }
        try {
            work.execute(() -> {
                try { prepare(operation); }
                catch (Exception | LinkageError error) {
                    // Keep the failed stage even if Stop/destroy also arrived.
                    // No raw exception/path export or cleanup retry.
                    synchronized (lifecycle) { operation.failed = true; }
                }
                finally { finish(operation); }
            });
        } catch (RejectedExecutionException error) {
            // Destroy may shut down the executor after reservation but before
            // dispatch. Nothing was acquired; release this exact reservation.
            synchronized (lifecycle) {
                operation.refusal = "Launch refused — preparation dispatch unavailable. Data preserved.";
            }
            finish(operation);
        }
        return START_NOT_STICKY;
    }
    @Override public void onDestroy() {
        synchronized (lifecycle) {
            destroyed = true;
            if (outstanding != null && outstanding.service == this) {
                outstanding.cancelled = true;
                statusText = "Service destroyed — preparation pending; reservation retained";
            }
            if (active == this) active = null;
        }
        // Do not interrupt/close pending I/O or join on the framework thread.
        work.shutdown(); super.onDestroy();
    }
    @Override public IBinder onBind(Intent intent) { return null; }
}
