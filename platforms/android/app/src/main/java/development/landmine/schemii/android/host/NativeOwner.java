package development.landmine.schemii.android.host;
/** Closed scalar projection only. No UUID/anchor/trust setter crosses JNI. */
public final class NativeOwner {
    private NativeOwner() {}
    static { System.loadLibrary("schemii_owner"); }
    static native int open();
    static native int request(int operation);
    static native long[] status();
    static native long[] observation();
    static native String observationSource();
    static native int close();

    /** Separate observation ABI v1, never an admission or cleanup grant. */
    static final class Observation {
        private final long[] values;
        private Observation(long[] values) { this.values = values; }
        long at(int index) { return values[index]; }
        static Observation decode(long[] raw) {
            if (raw == null || raw.length != 34 || raw[0] != 1 || raw[1] != 34) return null;
            raw = raw.clone();
            for (int i = 2; i < 31; i++) if (raw[i] < (i >= 22 ? -1 : 0) || raw[i] > 65535) return null;
            if (raw[2] > 24 || raw[4] > 24 || raw[3] > 5 || raw[5] > 5
                || raw[6] > 9 || raw[7] > 4 || raw[8] > 4096 || raw[9] > 4
                || raw[10] > 4096 || raw[11] > 7 || raw[21] > 5
                || raw[18] > 1 || raw[19] > 1 || raw[20] > 1 || raw[33] < 0 || raw[33] > 1
                || raw[31] < -1 || raw[32] < -1 || (raw[31] >= 0 && raw[32] >= 0 && raw[32] < raw[31])) return null;
            return new Observation(raw);
        }
        boolean disposed() {
            return at(18) == 0 && at(19) == 0 && at(20) == 0 && at(21) == 4 && at(4) == 24
                && at(33) == 1 && at(31) >= 0 && at(32) >= at(31);
        }
        boolean follows(Observation before) {
            return before != null && before.at(21) == 2 && before.at(33) == 1
                && before.at(31) >= 0 && before.at(32) >= before.at(31)
                && at(31) == before.at(31) && at(32) >= before.at(32)
                && (before.at(2) == 0 || (at(2) == before.at(2) && at(3) == before.at(3)))
                && (before.at(14) == 0 || at(14) == before.at(14));
        }
        String scalars() {
            StringBuilder out = new StringBuilder();
            for (int i = 0; i < values.length; i++) {
                if (i != 0) out.append(',');
                out.append(values[i]);
            }
            return out.toString();
        }
    }
}
