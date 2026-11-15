package development.landmine.schemii.android.network;

import android.content.Context;
import android.net.ConnectivityManager;
import android.net.DnsResolver;
import android.net.LinkAddress;
import android.net.LinkProperties;
import android.net.Network;
import android.net.NetworkCapabilities;
import android.net.RouteInfo;
import android.os.CancellationSignal;
import android.os.Looper;
import android.os.ParcelFileDescriptor;
import android.util.Base64;
import org.json.JSONArray;
import org.json.JSONObject;
import java.io.IOException;
import java.net.NetworkInterface;
import java.security.KeyStore;
import java.security.cert.X509Certificate;
import java.util.HashMap;
import java.util.Map;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import javax.net.ssl.TrustManager;
import javax.net.ssl.TrustManagerFactory;
import javax.net.ssl.X509TrustManager;

/** One bridge in a dedicated network process. No VpnService, TUN or process binding. */
public final class NetworkBridge implements AutoCloseable {
    static { System.loadLibrary("schemii_network"); }
    private final ConnectivityManager manager;
    private final ExecutorService dnsExecutor = Executors.newSingleThreadExecutor();
    private final Object lock = new Object();
    private static final class Query {
        final CancellationSignal signal = new CancellationSignal();
        final CountDownLatch finished = new CountDownLatch(1);
        boolean cancelled;
        void cancel() { cancelled = true; signal.cancel(); finished.countDown(); }
    }
    private final Map<Long, Query> queries = new HashMap<>();
    private final AtomicInteger dnsCount = new AtomicInteger();
    private final AtomicInteger bindCount = new AtomicInteger();
    private Network network;
    private NetworkCapabilities capabilities;
    private LinkProperties properties;
    private long generation = 1;
    private boolean closed;
    private boolean registered;
    private boolean detached;

    private final ConnectivityManager.NetworkCallback callback = new ConnectivityManager.NetworkCallback() {
        @Override public void onAvailable(Network n) {
            synchronized (lock) { network = n; capabilities = null; properties = null; changedLocked(); }
            nativeChanged();
        }
        @Override public void onCapabilitiesChanged(Network n, NetworkCapabilities c) {
            synchronized (lock) { if (!n.equals(network)) return; capabilities = c; changedLocked(); }
            nativeChanged();
        }
        @Override public void onLinkPropertiesChanged(Network n, LinkProperties p) {
            synchronized (lock) { if (!n.equals(network)) return; properties = p; changedLocked(); }
            nativeChanged();
        }
        @Override public void onLost(Network n) {
            synchronized (lock) {
                if (!n.equals(network)) return;
                network = null; capabilities = null; properties = null; changedLocked();
            }
            nativeChanged();
        }
    };

    public NetworkBridge(Context context) throws IOException {
        manager = (ConnectivityManager) context.getApplicationContext().getSystemService(Context.CONNECTIVITY_SERVICE);
        if (manager == null || !nativeAttach()) throw new IOException("Network bridge unavailable or already owned");
        try {
            // Observe the default network. Never silently select an underlying
            // non-VPN network to bypass a different application's VPN policy.
            manager.registerDefaultNetworkCallback(callback);
            registered = true;
        } catch (RuntimeException error) {
            nativeDetach(); dnsExecutor.shutdownNow();
            throw new IOException("Cannot observe Android default network", error);
        }
    }

    private void changedLocked() {
        generation++;
        for (Query query : queries.values()) query.cancel();
        lock.notifyAll();
    }

    private boolean availableLocked() {
        return !closed && network != null && capabilities != null && properties != null
            && capabilities.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
            && capabilities.hasCapability(NetworkCapabilities.NET_CAPABILITY_VALIDATED)
            && capabilities.hasCapability(NetworkCapabilities.NET_CAPABILITY_NOT_VPN)
            && !capabilities.hasTransport(NetworkCapabilities.TRANSPORT_VPN);
    }

    private Network captureLocked(long expected) throws IOException {
        if (!availableLocked() || expected != generation) throw new IOException("Android network generation unavailable");
        return network;
    }

    // JNI methods below use fixed method names/signatures. They run on attached
    // Go worker threads, never the Android main thread. No keys or login URLs.
    public String snapshotJson() throws Exception {
        synchronized (lock) {
            JSONObject out = new JSONObject().put("generation", generation).put("available", availableLocked());
            if (!availableLocked()) return out.put("reason", closed ? "closed" : "no-validated-non-vpn-default").toString();
            String name = properties.getInterfaceName();
            NetworkInterface nic = NetworkInterface.getByName(name);
            if (nic == null || !nic.isUp() || nic.isLoopback()) throw new IOException("Missing real interface metadata");
            int mtu = properties.getMtu() > 0 ? properties.getMtu() : nic.getMTU();
            int flags = 1; // Go net.FlagUp, with other flags from observed Java metadata.
            if (nic.isPointToPoint()) flags |= 8;
            if (nic.supportsMulticast()) flags |= 16;
            JSONArray addresses = new JSONArray();
            for (LinkAddress link : properties.getLinkAddresses()) {
                String address = link.getAddress().getHostAddress();
                int zone = address.indexOf('%');
                if (zone >= 0) address = address.substring(0, zone);
                addresses.put(address + "/" + link.getPrefixLength());
            }
            String gateway = "";
            for (RouteInfo route : properties.getRoutes()) {
                if (route.isDefaultRoute() && route.hasGateway()) {
                    gateway = route.getGateway().getHostAddress(); break;
                }
            }
            return out.put("network_handle", network.getNetworkHandle()).put("interface", name)
                .put("index", nic.getIndex()).put("mtu", mtu).put("flags", flags)
                .put("addresses", addresses).put("gateway", gateway)
                .put("private_dns", properties.isPrivateDnsActive()).toString();
        }
    }

    public byte[] queryDNS(long expected, long request, byte[] query, long timeoutMs) throws Exception {
        if (Looper.myLooper() == Looper.getMainLooper()) throw new IOException("DNS cannot block main thread");
        if (timeoutMs < 1 || timeoutMs > 10000 || query.length < 12 || query.length > 65535)
            throw new IOException("Invalid bounded DNS request");
        final Query pending;
        final byte[][] answer = new byte[1][];
        final Network selected;
        synchronized (lock) {
            pending = queries.get(request);
            if (pending == null) throw new IOException("Unregistered DNS operation");
        }
        try {
            synchronized (lock) { selected = captureLocked(expected); if (pending.cancelled) throw new IOException("DNS cancelled"); }
            // netd owns strict Private DNS, transport and resolver selection.
            // No raw DNS socket/public resolver fallback is opened by this app.
            DnsResolver.getInstance().rawQuery(selected, query, DnsResolver.FLAG_EMPTY,
                dnsExecutor, pending.signal, new DnsResolver.Callback<byte[]>() {
                    @Override public void onAnswer(byte[] bytes, int rcode) { answer[0] = bytes; pending.finished.countDown(); }
                    @Override public void onError(DnsResolver.DnsException error) { pending.finished.countDown(); }
                });
            dnsCount.incrementAndGet();
            if (!pending.finished.await(timeoutMs, TimeUnit.MILLISECONDS) || answer[0] == null)
                throw new IOException("Android DNS failed or deadline expired");
            synchronized (lock) { captureLocked(expected); }
            return answer[0];
        } finally {
            pending.cancel();
            synchronized (lock) { queries.remove(request); }
        }
    }

    public boolean prepareDNS(long request) {
        synchronized (lock) {
            if (closed || queries.containsKey(request)) return false;
            queries.put(request, new Query()); return true;
        }
    }

    public void cancelDNS(long request) {
        synchronized (lock) { Query query = queries.get(request); if (query != null) query.cancel(); }
    }

    public boolean bindSocket(long expected, int descriptor) {
        try {
            synchronized (lock) {
                Network selected = captureLocked(expected);
                // Duplicate only the FD; binding applies to the same socket.
                // Closing the Java duplicate never closes Go's original FD.
                try (ParcelFileDescriptor copy = ParcelFileDescriptor.fromFd(descriptor)) {
                    selected.bindSocket(copy.getFileDescriptor());
                }
                captureLocked(expected);
                bindCount.incrementAndGet();
                return true;
            }
        } catch (Exception error) { return false; }
    }

    public String systemRootsPem() throws Exception {
        TrustManagerFactory factory = TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm());
        factory.init((KeyStore) null); // Modern-target Android default system trust policy.
        StringBuilder pem = new StringBuilder();
        for (TrustManager manager : factory.getTrustManagers()) {
            if (!(manager instanceof X509TrustManager)) continue;
            for (X509Certificate cert : ((X509TrustManager) manager).getAcceptedIssuers()) {
                pem.append("-----BEGIN CERTIFICATE-----\n")
                    .append(Base64.encodeToString(cert.getEncoded(), Base64.NO_WRAP))
                    .append("\n-----END CERTIFICATE-----\n");
            }
        }
        if (pem.length() == 0 || pem.length() > 2 * 1024 * 1024) throw new IOException("Android system roots unavailable");
        return pem.toString();
    }

    public JSONObject probe() throws Exception {
        if (Looper.myLooper() == Looper.getMainLooper()) throw new IOException("Probe must use a worker thread");
        long until = System.nanoTime() + TimeUnit.SECONDS.toNanos(15);
        synchronized (lock) {
            while (!availableLocked() && !closed) {
                long remaining = until - System.nanoTime();
                if (remaining <= 0) break;
                TimeUnit.NANOSECONDS.timedWait(lock, remaining);
            }
        }
        return new JSONObject(nativeProbe()).put("android_dns_queries", dnsCount.get())
            .put("android_socket_binds", bindCount.get()).put("stack_acceptance", false);
    }

    /** Host supplies roots/endpoint/state paths through the agreed JSON contract. */
    public JSONObject start(String configuration) throws Exception {
        synchronized (lock) {
            if (closed) throw new IOException("Network bridge is closed");
            return new JSONObject(nativeStart(configuration));
        }
    }

    @Override public void close() throws IOException {
        synchronized (lock) { if (detached) return; if (!closed) { closed = true; changedLocked(); } }
        if (registered) { manager.unregisterNetworkCallback(callback); registered = false; }
        nativeChanged();
        dnsExecutor.shutdownNow();
        // Never detach callbacks while a half-initialized node is still using
        // them. The host owns containment of this dedicated process on failure.
        if (!nativeStop(15000)) throw new IOException("Native stop incomplete; retain dedicated process ownership");
        nativeDetach();
        synchronized (lock) { detached = true; }
    }

    private native boolean nativeAttach();
    private native void nativeDetach();
    private native void nativeChanged();
    private native String nativeProbe();
    private native String nativeStart(String configuration);
    private native boolean nativeStop(int timeoutMs);
}
