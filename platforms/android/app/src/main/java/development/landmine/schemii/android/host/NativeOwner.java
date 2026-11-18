package development.landmine.schemii.android.host;

import android.os.ParcelFileDescriptor;
import android.os.SystemClock;
import java.io.File;
import java.io.IOException;
import java.util.concurrent.atomic.AtomicBoolean;

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

    private static native long[] checkOwnMapping(int referenceFd, long begin);

    /** Independent of acquisition. Reading UI state does not initialize the outer JNI class. */
    static final class OwnMapping {
        // Native ABI v1 phase numbers in order. Remaining phases are Java-only.
        enum Phase {
            NONE, REFERENCE_STAT, MAPS_OPEN, MAPS_READ, MAPS_FORMAT, MAPS_LIMIT, MAPS_MATCH,
            DISPATCH, CLOCK, SOURCE, REFERENCE_OPEN, REFERENCE_BORROW, JNI
        }
        private static final int NOT_OPENED = 0, CLOSED = 1, CLOSE_FAILED = 2, UNOBSERVED = 3;
        private static final AtomicBoolean started = new AtomicBoolean();
        private static volatile String terminal;

        static void start(String libraryDir) {
            if (!started.compareAndSet(false, true)) return;
            try {
                new Thread(() -> measure(libraryDir), "schemii-own-mapping").start();
            } catch (RuntimeException | OutOfMemoryError failure) {
                publish("unbound", -1, -1, Phase.DISPATCH, 0, NOT_OPENED, 0, NOT_OPENED);
            }
        }

        static String status() {
            String result = terminal;
            if (result != null) return result;
            return started.get() ? "Own mapping pending — refresh status to check"
                : "Own mapping not checked";
        }

        private static long now() {
            try { return SystemClock.elapsedRealtimeNanos(); }
            catch (RuntimeException | LinkageError | OutOfMemoryError failure) { return -1; }
        }

        private static void measure(String libraryDir) {
            long begin = now();
            String source = "unbound";
            Phase phase = Phase.CLOCK;
            int error = 0, maps = NOT_OPENED, mapsError = 0, reference = NOT_OPENED;
            ParcelFileDescriptor held = null;
            try {
                if (begin >= 0) {
                    phase = Phase.SOURCE;
                    String compiled = observationSource();
                    if (compiled != null && compiled.matches("[0-9a-f]{40}")) {
                        source = compiled;
                        phase = Phase.REFERENCE_OPEN;
                        if (libraryDir != null && new File(libraryDir).isAbsolute()) {
                            held = ParcelFileDescriptor.open(new File(libraryDir, "libschemii_owner.so"),
                                ParcelFileDescriptor.MODE_READ_ONLY);
                            phase = Phase.REFERENCE_BORROW;
                            int fd = held.getFd();
                            if (fd >= 0) {
                                phase = Phase.JNI;
                                maps = UNOBSERVED;
                                long[] raw = checkOwnMapping(fd, begin);
                                if (valid(raw, begin)) {
                                    phase = Phase.values()[(int)raw[2]];
                                    error = (int)raw[3];
                                    maps = (int)raw[4];
                                    mapsError = (int)raw[5];
                                }
                            }
                        }
                    }
                }
            } catch (IOException | RuntimeException | LinkageError | OutOfMemoryError failure) {
                // The current phase identifies the first operation failure.
                // SDK exceptions are not a syscall errno or safe output.
            } finally {
                if (held != null) {
                    reference = CLOSE_FAILED;
                    try {
                        held.close();
                        reference = CLOSED;
                    } catch (IOException | RuntimeException | LinkageError | OutOfMemoryError failure) {
                        // One close attempt, even if the descriptor's disposition is uncertain.
                    }
                }
            }
            long end = now();
            if (end < begin && phase == Phase.NONE) phase = Phase.CLOCK;
            publish(source, begin, end, phase, error, maps, mapsError, reference);
        }

        private static boolean valid(long[] raw, long begin) {
            if (raw == null || raw.length != 6 || raw[0] != 1 || raw[1] != begin
                || raw[2] < 0 || raw[2] > Phase.MAPS_MATCH.ordinal()
                || raw[3] < 0 || raw[3] > 4095 || raw[5] < 0 || raw[5] > 4095) return false;
            int phase = (int)raw[2];
            if ((phase == Phase.MAPS_OPEN.ordinal() || phase == Phase.MAPS_READ.ordinal())
                && raw[3] == 0) return false;
            if ((phase == 0 || phase >= Phase.MAPS_FORMAT.ordinal()) && raw[3] != 0) return false;
            if (phase == Phase.REFERENCE_STAT.ordinal() || phase == Phase.MAPS_OPEN.ordinal())
                return raw[4] == NOT_OPENED && raw[5] == 0;
            return (raw[4] == CLOSED && raw[5] == 0) || (raw[4] == CLOSE_FAILED && raw[5] > 0);
        }

        private static void publish(String source, long begin, long end, Phase phase, int error,
                                    int maps, int mapsError, int reference) {
            boolean loss = begin < 0 || end < begin || source.equals("unbound") || maps == UNOBSERVED;
            boolean complete = !loss && (phase == Phase.NONE || phase == Phase.MAPS_MATCH);
            boolean matched = complete && phase == Phase.NONE && maps == CLOSED && reference == CLOSED;
            terminal = (matched ? "Own mapping matched" : "Own mapping unknown")
                + "\nv=1 profile=closed-connected-host-v1 source=" + source
                + " sample=1 beginNs=" + begin + " endNs=" + end
                + " outcome=" + (matched ? "match" : "unknown") + " phase=" + phase + " errno=" + error
                + " mapsClose=" + maps + " mapsCloseErrno=" + mapsError + " referenceClose=" + reference
                + " complete=" + (complete ? 1 : 0) + " loss=" + (loss ? 1 : 0);
        }
    }

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
