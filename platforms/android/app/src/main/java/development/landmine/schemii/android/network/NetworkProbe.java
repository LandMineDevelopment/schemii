package development.landmine.schemii.android.network;

import android.content.Context;
import org.json.JSONObject;

/** Runtime264 calls this from its existing worker-thread probe, not a new app. */
public final class NetworkProbe {
    private NetworkProbe() { }
    public static JSONObject run(Context context) throws Exception {
        try (NetworkBridge bridge = new NetworkBridge(context)) { return bridge.probe(); }
    }
}
