package development.landmine.schemii.android.host;
/** Closed scalar projection only. No UUID/anchor/trust setter crosses JNI. */
public final class NativeOwner {
    private NativeOwner() {}
    static { System.loadLibrary("schemii_owner"); }
    static native int open();
    static native int request(int operation);
    static native long[] status();
    static native int close();
}
