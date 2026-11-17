package development.landmine.schemii.android.host;

import android.app.*;
import android.content.*;
import android.os.*;
import java.io.*;
import java.util.concurrent.*;

/** Android lifecycle adapter only. C/A are unproved; every birth is refused. */
public final class HostService extends Service {
    private final ExecutorService work = Executors.newSingleThreadExecutor();
    // Protect only reference/state transitions. Never hold across SDK, native,
    // executor dispatch, filesystem calls or a blocking join.
    private static final Object lifecycle = new Object();
    private static HostService active;
    private static Operation outstanding;
    private static long serviceSequence, operationSequence;
    private final long incarnation;
    public HostService() {
        synchronized (lifecycle) {
            incarnation = serviceSequence == Long.MAX_VALUE ? -1 : ++serviceSequence;
        }
    }
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
        boolean cancelled, failed, nativeEntered, unresolved;
        int firstError, cleanupError;
        final long sequence;
        long begin = -1, end = -1;
        boolean completed, workerStarted, observationAttempted;
        int javaStage = 1, javaFailure, logSequence, logFailures;
        String source = "unknown";
        NativeOwner.Observation beforeClose, afterClose;
        Acquisition acquisition;
        String refusal = "Launch refused — private storage unavailable. Data preserved.";
        Operation(HostService service) {
            this.service = service; this.application = service.application;
            this.sequence = operationSequence == Long.MAX_VALUE ? -1 : ++operationSequence;
        }
    }
    @Override public void onCreate() {
        super.onCreate();
        Context actual = getApplicationContext();
        Operation cancellation = null;
        synchronized (lifecycle) {
            if (created || destroyed) return; // never revive this incarnation
            created = true; application = actual;
            if (active != null) {
                active.replaced = true;
                if (outstanding != null) {
                    if (!outstanding.cancelled) cancellation = outstanding;
                    outstanding.cancelled = true;
                }
            }
            active = this;
        }
        if (cancellation != null) observe(cancellation, 4);
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
    public static boolean available() { return true; }
    // At most begin/request/preclose/result records per operation. A positive
    // Log return is NOT delivery: finite capture gaps/rotation remain unknown.
    private static NativeOwner.Observation readObservation() {
        try { return NativeOwner.Observation.decode(NativeOwner.observation()); }
        catch (Exception | LinkageError | OutOfMemoryError error) { return null; }
    }
    private static long observationTime() {
        try { return android.os.SystemClock.elapsedRealtimeNanos(); }
        catch (RuntimeException | LinkageError | OutOfMemoryError error) { return -1; }
    }
    private static boolean fixedSource(String source) {
        if (source == null || source.length() != 40) return false;
        for (int i = 0; i < 40; i++) {
            char c = source.charAt(i);
            if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'))) return false;
        }
        return true;
    }
    private static void observe(Operation operation, int phase) {
        try { emitObservation(operation, phase); }
        catch (RuntimeException | LinkageError | OutOfMemoryError error) {
            synchronized (lifecycle) { ++operation.logFailures; }
        }
    }
    private static void emitObservation(Operation operation, int phase) {
        String source, pre, post;
        long begin, end; int sequence, losses, stage, failure; boolean cancel, completed;
        synchronized (lifecycle) {
            sequence = ++operation.logSequence; losses = operation.logFailures;
            source = operation.source; begin = operation.begin; end = operation.end;
            stage = operation.javaStage; failure = operation.javaFailure;
            cancel = operation.cancelled || operation.service.destroyed || operation.service.replaced;
            completed = operation.completed;
            pre = operation.beforeClose == null ? "unknown" : operation.beforeClose.scalars();
            post = operation.afterClose == null ? "unknown" : operation.afterClose.scalars();
        }
        String record = "v=1 profile=closed-ce-acquisition source=" + source
            + " incarnation=" + operation.service.incarnation + " operation=" + operation.sequence
            + " record=" + sequence + " phase=" + phase + " begin=" + begin + " end=" + end
            + " javaStage=" + stage + " javaFailure=" + failure + " cancelRequested=" + (cancel ? 1 : 0)
            + " completionObserved=" + (completed ? 1 : 0) + " lost=" + losses
            + " delivery=unverified pre=" + pre + " post=" + post;
        int written = -1;
        try {
            if (record.length() <= 2048) written = android.util.Log.i("SchemiiAcquire", record);
        } catch (RuntimeException | LinkageError error) { /* finite observation loss only */ }
        if (written < record.length()) synchronized (lifecycle) {
            ++operation.logFailures;
            if (operation.completed && active == operation.service && !statusText.contains("Observation loss"))
                statusText += " Observation loss detected.";
        }
    }
    private void prepare(Operation operation) throws Exception {
        synchronized (lifecycle) { if (!live(operation)) return; }
        synchronized (lifecycle) { operation.javaStage = 2; }
        StorageProfile.select(operation.application, false);
        synchronized (lifecycle) {
            if (!live(operation)) return;
            operation.acquisition = new Acquisition(operation.application);
            operation.javaStage = 3;
            operation.nativeEntered = true;
            operation.unresolved = true;
            operation.refusal = "Launch refused — native acquisition or custody unavailable. Data preserved.";
        }
        String source = null;
        try { source = NativeOwner.observationSource(); }
        catch (Exception | LinkageError | OutOfMemoryError error) { /* unbound observation, no guard change */ }
        synchronized (lifecycle) {
            operation.source = fixedSource(source) ? source : "unknown";
        }
        int result = NativeOwner.open();
        synchronized (lifecycle) {
            operation.firstError = result;
            operation.failed = result != 0;
            operation.refusal = result == 0
                ? "Launch refused — current caller and historical admission unproved. Data preserved."
                : "Launch refused — native acquisition failed (" + result + "). Data preserved.";
        }
        NativeOwner.Observation observed = readObservation();
        synchronized (lifecycle) { operation.beforeClose = observed; operation.observationAttempted = true; }
    }
    private void finish(Operation operation) {
        // Sole reserving worker owns native cleanup. No lifecycle monitor across
        // JNI or I/O. A failed/EBUSY close never releases the Java reservation.
        if (operation.nativeEntered) {
            try {
                if (!operation.observationAttempted) {
                    NativeOwner.Observation before = readObservation();
                    synchronized (lifecycle) { operation.beforeClose = before; operation.observationAttempted = true; }
                }
                synchronized (lifecycle) { operation.javaStage = 4; }
                observe(operation, 2);
                int cleanup = NativeOwner.close();
                long[] state = NativeOwner.status();
                NativeOwner.Observation observed = readObservation();
                synchronized (lifecycle) {
                    // close can return a retained operational error after safe disposal.
                    // The scalar report distinguishes it from actual cleanup failure.
                    operation.afterClose = observed;
                    operation.cleanupError = state != null && state.length == 16 ? (int)state[2] : cleanup;
                    operation.unresolved = state == null || state.length != 16 || state[4] != 0 || state[5] != 0
                        || observed == null || !observed.disposed() || !observed.follows(operation.beforeClose)
                        || operation.source.equals("unknown") || operation.sequence < 0 || operation.service.incarnation < 0;
                    if (state != null && state.length == 16 && operation.firstError == 0) operation.firstError = (int)state[0];
                }
            } catch (Exception | LinkageError error) {
                synchronized (lifecycle) { operation.unresolved = true; }
            }
        }
        long end = observationTime();
        synchronized (lifecycle) {
            if (outstanding != operation) return;
            operation.completed = operation.workerStarted; // observed task completion point, not thread death
            operation.end = end;
            operation.javaStage = 5;
            boolean cancelled = operation.cancelled || operation.service.destroyed || operation.service.replaced;
            statusText = operation.refusal
                + (operation.firstError != 0 ? " First error " + operation.firstError + "." : "")
                + (operation.cleanupError != 0 ? " Cleanup error " + operation.cleanupError + "." : "")
                + (cancelled ? " Cancellation observed." : "")
                + (operation.unresolved ? " Cleanup unresolved; reservation retained." : "")
                + (operation.beforeClose == null ? " Observation unavailable." :
                    " Acquisition stage " + operation.beforeClose.at(2) + ":" + operation.beforeClose.at(3)
                    + "; last " + operation.beforeClose.at(4) + ".")
                + (operation.logFailures != 0 ? " Observation loss detected." : "");
            if (!operation.unresolved) {
                operation.acquisition = null;
                outstanding = null;
            }
        }
        observe(operation, 3);
    }
    @Override public int onStartCommand(Intent intent,int flags,int id) {
        NotificationManager notifications = getSystemService(NotificationManager.class);
        notifications.createNotificationChannel(new NotificationChannel("schemii-host","Schemii server",NotificationManager.IMPORTANCE_LOW));
        Notification notification = new Notification.Builder(this,"schemii-host").setContentTitle("Schemii server")
            .setContentText(statusText).setSmallIcon(android.R.drawable.stat_notify_sync).build();
        startForeground(266,notification);
        String action = intent == null ? "status" : intent.getAction();
        if ("stop".equals(action)) {
            Operation pending;
            synchronized (lifecycle) {
                if (!created || destroyed || replaced || active != this) return START_NOT_STICKY;
                pending = outstanding != null && outstanding.service == this && !outstanding.cancelled ? outstanding : null;
                if (pending != null) pending.cancelled = true;
                statusText = outstanding == null
                    ? "Stop observed — ownership/recovery unproved"
                    : "Stop observed — preparation pending; reservation retained";
            }
            if (pending != null) observe(pending, 4);
            return START_NOT_STICKY;
        }
        Operation operation;
        synchronized (lifecycle) {
            if (!"start".equals(action)) return START_NOT_STICKY;
            if (!created || destroyed || replaced || active != this || outstanding != null) {
                statusText = "Launch refused — stale service or preparation pending; reservation retained";
                return START_NOT_STICKY;
            }
            operation = new Operation(this);
            outstanding = operation;
            statusText = "Checking CE storage and installed profile — native acquisition pending";
        }
        try {
            work.execute(() -> {
                long begin = observationTime();
                synchronized (lifecycle) { operation.workerStarted = true; operation.begin = begin; }
                observe(operation, 1);
                try { prepare(operation); }
                catch (Exception | LinkageError error) {
                    // Keep the failed stage even if Stop/destroy also arrived.
                    // No raw exception/path export or cleanup retry.
                    synchronized (lifecycle) { operation.failed = true; if (operation.javaFailure == 0) operation.javaFailure = operation.javaStage; }
                }
                finally { finish(operation); }
            });
        } catch (RejectedExecutionException error) {
            // Destroy may shut down the executor after reservation but before
            // dispatch. Nothing was acquired; release this exact reservation.
            synchronized (lifecycle) {
                operation.javaFailure = 1;
                operation.refusal = "Launch refused — preparation dispatch unavailable. Data preserved.";
            }
            finish(operation);
        }
        return START_NOT_STICKY;
    }
    @Override public void onDestroy() {
        Operation cancellation = null;
        synchronized (lifecycle) {
            destroyed = true;
            if (outstanding != null && outstanding.service == this) {
                if (!outstanding.cancelled) cancellation = outstanding;
                outstanding.cancelled = true;
                statusText = "Service destroyed — preparation pending; reservation retained";
            }
            if (active == this) active = null;
        }
        if (cancellation != null) observe(cancellation, 4);
        // Do not interrupt/close pending I/O or join on the framework thread.
        work.shutdown(); super.onDestroy();
    }
    @Override public IBinder onBind(Intent intent) { return null; }
}
