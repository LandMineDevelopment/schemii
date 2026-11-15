package development.landmine.schemii.android.network;

import android.content.Context;
import android.os.Looper;
import java.io.IOException;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;
import java.util.concurrent.atomic.AtomicReference;
import org.json.JSONObject;

/** Runtime264 calls this from its existing worker-thread probe, not a new app. */
public final class NetworkProbe {
    private NetworkProbe() { }
    public static JSONObject run(Context context) throws Exception {
        if (Looper.myLooper() == Looper.getMainLooper()) throw new IOException("NetworkProbe requires a worker thread");
        AtomicReference<NetworkBridge> owner = new AtomicReference<>();
        ExecutorService worker = Executors.newSingleThreadExecutor();
        Future<JSONObject> result = worker.submit(() -> {
            try (NetworkBridge bridge = new NetworkBridge(context)) {
                owner.set(bridge);
                if (Thread.currentThread().isInterrupted()) throw new InterruptedException("Probe cancelled");
                return bridge.probe();
            }
        });
        try {
            // One deadline includes network discovery, JNI work and normal close.
            return result.get(45, TimeUnit.SECONDS);
        } catch (TimeoutException | InterruptedException error) {
            result.cancel(true);
            IOException failure = new IOException("Network probe deadline/cancellation; retain process ownership until native cleanup is confirmed", error);
            NetworkBridge bridge = owner.get();
            if (bridge != null) {
                try { bridge.requestCancellation(); }
                catch (Exception cleanup) { failure.addSuppressed(cleanup); }
            }
            if (error instanceof InterruptedException) Thread.currentThread().interrupt();
            throw failure;
        } catch (ExecutionException error) {
            throw new IOException("Network probe failed", error.getCause());
        } finally {
            worker.shutdownNow();
        }
    }
}
