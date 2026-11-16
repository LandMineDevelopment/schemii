package development.landmine.schemii.android.host;

import java.io.*;
import java.nio.file.*;
import java.security.MessageDigest;
import java.util.*;
import java.util.zip.*;

/** Exact installed source composition. Expectations come from compiled build
 * anchors, never an Intent, the extracted manifest, or a mutable state file.
 * Package/signature/storage admission is the caller's separate prerequisite.
 */
public final class InstalledSource {
    private InstalledSource() {}
    private static final long MAX_FILE = 16L * 1024 * 1024;
    private static final long MAX_TOTAL = 128L * 1024 * 1024;

    static String safe(String name) throws IOException {
        if (name.length() == 0 || name.length() > 1024 || !name.matches("[A-Za-z0-9_.@/+\\-]+")
            || name.startsWith("/") || name.endsWith("/") || name.contains("//"))
            throw new IOException("Source path syntax refused");
        for (String part : name.split("/", -1)) {
            if (part.equals(".") || part.equals("..") || part.isEmpty()) throw new IOException("Source alias refused");
        }
        return name;
    }
    public static String hex(byte[] bytes) {
        StringBuilder out = new StringBuilder();
        for (byte b : bytes) out.append(String.format(java.util.Locale.ROOT, "%02x", b & 255));
        return out.toString();
    }
    private static long u32(RandomAccessFile in) throws IOException {
        return Integer.toUnsignedLong(Integer.reverseBytes(in.readInt()));
    }
    private static int u16(RandomAccessFile in) throws IOException { return Short.toUnsignedInt(Short.reverseBytes(in.readShort())); }

    /** ZipFile omits Unix entry type. Read the actual central directory too;
     * reject ZIP64/multidisk, links/specials, duplicate/case/normalization aliases.
     * Ordinary APK files may omit Unix mode, but never claim another file kind.
     */
    private static Set<String> central(File apk) throws IOException {
        Set<String> names = new HashSet<>(), folded = new HashSet<>();
        try (RandomAccessFile in = new RandomAccessFile(apk, "r")) {
            long end = -1, length = in.length();
            for (long p = length - 22; p >= Math.max(0, length - 65557); --p) {
                in.seek(p);
                if (u32(in) == 0x06054b50L) {
                    in.seek(p + 20);
                    if (p + 22 + u16(in) == length) { end = p; break; }
                }
            }
            if (end < 0) throw new IOException("APK directory absent");
            in.seek(end + 4);
            if (u16(in) != 0 || u16(in) != 0) throw new IOException("Multidisk APK refused");
            int count = u16(in);
            if (count == 65535 || u16(in) != count) throw new IOException("ZIP64/count refused");
            long bytes = u32(in), offset = u32(in);
            if (offset + bytes != end) throw new IOException("APK directory bounds refused");
            in.seek(offset);
            for (int i = 0; i < count; ++i) {
                long start = in.getFilePointer();
                if (u32(in) != 0x02014b50L) throw new IOException("APK entry refused");
                in.seek(start + 8);
                if ((u16(in) & 1) != 0) throw new IOException("Encrypted APK entry refused");
                in.seek(start + 28);
                int n = u16(in), extra = u16(in), comment = u16(in);
                if (u16(in) != 0) throw new IOException("APK disk refused");
                u16(in);
                int mode = (int)(u32(in) >>> 16), kind = mode & 0170000;
                long local = u32(in);
                byte[] encoded = new byte[n]; in.readFully(encoded);
                String name = new String(encoded, java.nio.charset.StandardCharsets.UTF_8);
                boolean dir = name.endsWith("/");
                safe(dir ? name.substring(0, name.length() - 1) : name);
                if (kind != 0 && kind != (dir ? 0040000 : 0100000)) throw new IOException("APK link/special refused");
                if (!names.add(name) || !folded.add(name.toLowerCase(Locale.ROOT))) throw new IOException("APK duplicate/alias refused");
                long next = start + 46L + n + extra + comment;
                if (next > end || local >= offset) throw new IOException("APK entry bounds refused");
                in.seek(next);
            }
            if (in.getFilePointer() != end) throw new IOException("APK directory tail refused");
        }
        return names;
    }

    public static void compose(File apk, File fresh, Map<String, String> source,
                               Map<String, String> assets) throws Exception {
        // Caller owns private parent. Never reuse/repair/publish a partial tree.
        if (Files.isSymbolicLink(fresh.toPath()) || fresh.exists()) throw new IOException("Source destination must be new");
        for (Path p = fresh.toPath().toAbsolutePath().getParent(); p != null; p = p.getParent())
            if (Files.isSymbolicLink(p)) throw new IOException("Source parent linked");
        Set<String> canonical = central(apk);
        Map<String, String> wanted = new TreeMap<>(assets);
        Set<String> folded = new HashSet<>();
        for (Map.Entry<String,String> e : source.entrySet()) {
            String name = safe(e.getKey());
            if (!folded.add(name.toLowerCase(Locale.ROOT)) || !e.getValue().matches("[a-f0-9]{64}")) throw new IOException("Source inventory refused");
            String entry = (name.startsWith("dev/android/") || name.equals("dev/schemii.toml") ? "assets/runtime-source/" : "assets/shared/") + name;
            if (wanted.put(entry, e.getValue()) != null) throw new IOException("Source overlap refused");
        }
        for (String name : canonical) {
            if (name.endsWith("/")) continue;
            if ((name.startsWith("assets/") || name.startsWith("lib/")) && !wanted.containsKey(name)) throw new IOException("Unknown packaged asset refused");
        }
        long total = 0;
        // Verify ALL admitted assets/native/payload manifests before writing.
        try (ZipFile zip = new ZipFile(apk)) {
            Set<String> javaNames = new HashSet<>();
            for (Enumeration<? extends ZipEntry> all = zip.entries(); all.hasMoreElements();) javaNames.add(all.nextElement().getName());
            if (!javaNames.equals(canonical)) throw new IOException("APK parser disagreement");
            for (Map.Entry<String,String> expected : wanted.entrySet()) {
                ZipEntry entry = zip.getEntry(safe(expected.getKey()));
                if (entry == null || entry.isDirectory() || entry.getSize() < 0) throw new IOException("Packaged member missing");
                MessageDigest hash = MessageDigest.getInstance("SHA-256");
                long read = 0;
                try (InputStream in = zip.getInputStream(entry)) {
                    byte[] buffer = new byte[65536];
                    for (int n; (n = in.read(buffer)) != -1;) {
                        read += n;
                        if (read > entry.getSize() || read > 1024L * 1024 * 1024) throw new IOException("Packaged size refused");
                        hash.update(buffer, 0, n);
                    }
                }
                if (read != entry.getSize() || !hex(hash.digest()).equals(expected.getValue())) throw new IOException("Packaged bytes changed");
            }
            Files.createDirectory(fresh.toPath());
            for (Map.Entry<String,String> e : source.entrySet()) {
                String name = e.getKey();
                String prefix = name.startsWith("dev/android/") || name.equals("dev/schemii.toml") ? "assets/runtime-source/" : "assets/shared/";
                ZipEntry member = zip.getEntry(prefix + name);
                total += member.getSize();
                if (member.getSize() > MAX_FILE || total > MAX_TOTAL) throw new IOException("Source budget refused");
                Path target = fresh.toPath().resolve(name);
                Files.createDirectories(target.getParent());
                try (InputStream in = zip.getInputStream(member); OutputStream out = Files.newOutputStream(target, StandardOpenOption.CREATE_NEW)) {
                    byte[] buffer = new byte[65536];
                    for (int n; (n = in.read(buffer)) != -1;) out.write(buffer, 0, n);
                }
                if (!hex(MessageDigest.getInstance("SHA-256").digest(Files.readAllBytes(target))).equals(e.getValue())) throw new IOException("Source readback changed");
                Files.setPosixFilePermissions(target, java.nio.file.attribute.PosixFilePermissions.fromString("r--r--r--"));
            }
            // Immutable to ordinary runtime writes; Android same-UID malicious
            // rewriting is outside this boundary, not hardware sealing.
            try (java.util.stream.Stream<Path> paths = Files.walk(fresh.toPath())) {
                for (Iterator<Path> it = paths.iterator(); it.hasNext();) {
                    Path p = it.next();
                    if (Files.isDirectory(p)) Files.setPosixFilePermissions(p, java.nio.file.attribute.PosixFilePermissions.fromString("r-xr-xr-x"));
                }
            }
        }
    }
}
