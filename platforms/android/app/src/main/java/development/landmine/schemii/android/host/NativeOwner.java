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
    static native long[] imageRefusal();
    static native int close();

    /** Separate observation ABI v1, never an admission or cleanup grant. */
    static final class Observation {
        private final long[] values;
        private Observation(long[] values) { this.values = values; }
        long at(int index) { return values[index]; }
        /* Name-origin hints, NOT stock/package provenance or admission. Raw
         * private name bytes never reach logs, status or retained Operation. */
        String imageRefusalScalars(long[] raw) throws java.security.NoSuchAlgorithmException {
            if (raw == null || raw.length < 8 || raw.length > 520 || raw[0] != 1
                || raw[1] != raw.length || raw[7] != raw.length-8 || raw[7] == 0
                || raw[2] < 1 || raw[2] > 4096 || raw[2] != values[8]
                || raw[3] < 0 || raw[3] > 65535 || raw[4] < 0 || raw[4] > 1
                || values[7] != 3 || values[31] < 0 || raw[5] != values[31]
                || values[33] != 1 || raw[6] != values[2]
                || (raw[6] != 10 && raw[6] != 22)) return "unknown";
            byte[] bytes = new byte[(int)raw[7]];
            boolean ascii = true;
            for (int i=0; i<bytes.length; i++) {
                if (raw[8+i] < 1 || raw[8+i] > 255) return "unknown";
                bytes[i] = (byte)raw[8+i];
                ascii &= raw[8+i] >= 32 && raw[8+i] <= 126;
            }
            // Truncated names are not identities and expose no prefix.
            if (raw[4] != 1) return "v1,complete=0,name=redacted,provenance=unknown";
            String path = ascii ? new String(bytes, java.nio.charset.StandardCharsets.US_ASCII) : "";
            String[] roots = {"/system/","/apex/","/vendor/","/product/","/system_ext/","/odm/","/data/app/","/data/"};
            int origin = 0;
            for (int i=0; i<roots.length; i++) if (path.startsWith(roots[i])) { origin=i+1; break; }
            if (!path.isEmpty() && path.charAt(0) != '/') origin=9;
            boolean lexical = !path.contains("//") && !path.contains("/../") && !path.contains("/./")
                && !path.endsWith("/..") && !path.endsWith("/.");
            String name = "redacted";
            if (origin >= 1 && origin <= 6 && lexical) {
                String base = path.substring(path.lastIndexOf('/')+1);
                boolean safe = base.startsWith("lib") && base.endsWith(".so") && base.length() <= 80;
                for (int i=0; i<base.length(); i++) {
                    char c=base.charAt(i);
                    safe &= (c>='a' && c<='z') || (c>='A' && c<='Z') || (c>='0' && c<='9')
                        || c=='_' || c=='-' || c=='+' || c=='.';
                }
                if (safe) name=base;
            }
            byte[] digest=java.security.MessageDigest.getInstance("SHA-256").digest(bytes);
            char[] hex=new char[64]; String digits="0123456789abcdef";
            for (int i=0; i<digest.length; i++) { hex[i*2]=digits.charAt((digest[i]>>>4)&15); hex[i*2+1]=digits.charAt(digest[i]&15); }
            // Hash identifies the complete loader NAME, not mapped/file bytes.
            String result="v1,complete=1,origin="+origin+",name="+name+",nameSha256="+new String(hex)
                +",ordinal="+raw[2]+",phnum="+raw[3]+",provenance=unknown";
            return result.length() <= 256 ? result : "unknown";
        }
        static Observation decode(long[] raw) {
            if (raw == null || raw.length != 34 || raw[0] != 1 || raw[1] != 34) return null;
            raw = raw.clone();
            // Only boot observation status/stage/errors have the explicit
            // BOOT_COMPARE_UNOBSERVED sentinel. Other boot fields are enums.
            for (int i = 2; i < 31; i++) if (raw[i] < (i >= 24 && i <= 27 ? -1 : 0) || raw[i] > 65535) return null;
            if (raw[2] > 24 || raw[4] > 24 || raw[3] > 5 || raw[5] > 5
                || raw[6] > 9 || raw[7] > 4 || raw[8] > 4096 || raw[9] > 4
                || raw[10] > 4096 || raw[11] > 7 || raw[21] > 5
                // Version1 domains from boot_compare.h and boot_identity.h.
                || raw[22] > 2 || raw[23] > 15 || raw[24] > 11 || raw[25] > 8
                || raw[26] > 4095 || raw[27] > 4095 || raw[28] > 2 || raw[29] > 3 || raw[30] > 2
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
