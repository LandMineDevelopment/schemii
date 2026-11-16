package development.landmine.schemii.android.host;
/** Closed scalar projection only. No UUID/anchor/trust setter crosses JNI. */
public final class NativeOwner {
    private NativeOwner() {}
    static { System.loadLibrary("schemii_owner"); }
    public static native int open(int borrowedDirectoryFd);
    public static native int request(int operation);
    public static native long[] status();
    public static native int close();
}
