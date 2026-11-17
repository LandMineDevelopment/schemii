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

    /** Fixed reviewed framework edge. Observations only; native pins decide. */
    static final class Framework {
        final String packageName, signer, apk, data, module;
        Framework(String p, String s, String a, String d, String m) {
            packageName=p; signer=s; apk=a; data=d; module=m;
        }
    }
    static Framework framework(android.content.Context app) throws Exception {
        if (app == null || app.getApplicationContext() != app
            || !app.getClass().getName().equals("android.app.Application"))
            throw new IOException("Framework application refused");
        StorageProfile.select(app, false);
        ClassLoader loader = app.getClassLoader();
        if (loader == null || !loader.getClass().getName().equals("dalvik.system.PathClassLoader")
            || loader.getParent() == null || !loader.getParent().getClass().getName().equals("java.lang.BootClassLoader")
            || HostActivity.class.getClassLoader() != loader || HostService.class.getClassLoader() != loader || NativeOwner.class.getClassLoader() != loader
            || InstalledSource.class.getClassLoader() != loader || StorageProfile.class.getClassLoader() != loader)
            throw new IOException("Loader refused");
        String name = app.getPackageName();
        android.content.pm.PackageManager pm = app.getPackageManager();
        android.content.pm.PackageInfo p = pm.getPackageInfo(name,
            android.content.pm.PackageManager.GET_SIGNING_CERTIFICATES
            | android.content.pm.PackageManager.GET_ACTIVITIES | android.content.pm.PackageManager.GET_SERVICES
            | android.content.pm.PackageManager.GET_PROVIDERS | android.content.pm.PackageManager.GET_RECEIVERS
            | android.content.pm.PackageManager.GET_INSTRUMENTATION | android.content.pm.PackageManager.GET_SHARED_LIBRARY_FILES);
        android.content.pm.ApplicationInfo a = p.applicationInfo;
        if (a == null || !name.equals(p.packageName) || !name.equals(a.packageName)
            || a.targetSdkVersion != 36 || a.uid != android.os.Process.myUid()
            || (a.flags & android.content.pm.ApplicationInfo.FLAG_DEBUGGABLE) != 0
            || a.className != null || a.appComponentFactory != null || !name.equals(a.processName)
            || (a.splitSourceDirs != null && a.splitSourceDirs.length != 0)
            || (a.splitPublicSourceDirs != null && a.splitPublicSourceDirs.length != 0)
            || (a.sharedLibraryFiles != null && a.sharedLibraryFiles.length != 0)
            || (p.splitNames != null && p.splitNames.length != 0)
            || (p.providers != null && p.providers.length != 0)
            || (p.receivers != null && p.receivers.length != 0)
            || (p.instrumentation != null && p.instrumentation.length != 0)
            || p.activities == null || p.activities.length != 1
            || !p.activities[0].name.equals(HostActivity.class.getName())
            || !p.activities[0].exported || !name.equals(p.activities[0].processName)
            || p.services == null || p.services.length != 1
            || !p.services[0].name.equals(HostService.class.getName())
            || p.services[0].exported || !name.equals(p.services[0].processName)
            || p.signingInfo == null || p.signingInfo.hasMultipleSigners())
            throw new IOException("Closed package refused");
        android.content.pm.Signature[] signers = p.signingInfo.getApkContentsSigners();
        if (signers == null || signers.length != 1) throw new IOException("Current signer refused");
        android.content.pm.ApplicationInfo current = app.getApplicationInfo();
        if (a.sourceDir == null || !a.sourceDir.equals(a.publicSourceDir)
            || !a.sourceDir.equals(current.sourceDir) || a.nativeLibraryDir == null
            || !a.nativeLibraryDir.equals(current.nativeLibraryDir) || a.dataDir == null
            || !a.dataDir.equals(app.getDataDir().getAbsolutePath())) throw new IOException("Package objects refused");
        return new Framework(name,hex(MessageDigest.getInstance("SHA-256").digest(signers[0].toByteArray())),
            a.sourceDir,a.dataDir,a.nativeLibraryDir + "/libschemii_owner.so");
    }

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
