"""Finite production-Java ordering controls; not Android or native provenance.

Only Android/framework and the installed framework observation edge are doubles.
HostService, StorageProfile, NativeOwner and JNI are compiled unchanged. No
positive native admission, APK build, framework process or device is exercised.
"""

import ctypes
from pathlib import Path
import shutil
import stat
import hashlib
import os
import zipfile
import subprocess
import tempfile

import pytest


ROOT = Path(__file__).resolve().parents[1]
HOST = (
    ROOT
    / "platforms/android/app/src/main/java/development/landmine/schemii/android/host"
)
PACKAGE = "development.landmine.schemii.android.host"

STUBS = {
    "android/content/Context.java": """
package android.content;
import java.io.File;
import android.content.pm.*;
public abstract class Context {
    public Context getApplicationContext() { return this; }
    public boolean isDeviceProtectedStorage() { return false; }
    public abstract <T> T getSystemService(Class<T> kind);
    public File getDataDir() { throw new AssertionError("unexpected data lookup"); }
    public File getFilesDir() { throw new AssertionError("creating helper forbidden"); }
    public Context createDeviceProtectedStorageContext() { throw new AssertionError("DE forbidden"); }
    public ApplicationInfo getApplicationInfo() { return new ApplicationInfo(); }
    public String getPackageName() { return "fixture.package"; }
    public PackageManager getPackageManager() { throw new AssertionError("unexpected PM"); }
}
""",
    "android/content/Intent.java": """
package android.content;
public class Intent {
    private final String action;
    public Intent(String action) { this.action = action; }
    public String getAction() { return action; }
}
""",
    "android/app/Service.java": """
package android.app;
import android.content.*;
import android.os.IBinder;
public class Service extends Context {
    public Context frameworkApplication;
    public static final int START_NOT_STICKY = 2;
    public Context getApplicationContext() { return frameworkApplication; }
    public <T> T getSystemService(Class<T> kind) { return kind.cast(new NotificationManager()); }
    public void onCreate() {}
    public int onStartCommand(Intent intent, int flags, int id) { return 0; }
    public void onDestroy() {}
    public IBinder onBind(Intent intent) { return null; }
    public void startForeground(int id, Notification notification) {}
}
""",
    "android/app/NotificationManager.java": """
package android.app;
public class NotificationManager {
    public static final int IMPORTANCE_LOW = 2;
    public void createNotificationChannel(NotificationChannel channel) {}
}
""",
    "android/app/NotificationChannel.java": """
package android.app;
public class NotificationChannel { public NotificationChannel(String a, String b, int c) {} }
""",
    "android/app/Notification.java": """
package android.app;
import android.content.Context;
public class Notification {
    public static class Builder {
        public Builder(Context context, String channel) {}
        public Builder setContentTitle(String s) { return this; }
        public Builder setContentText(String s) { return this; }
        public Builder setSmallIcon(int i) { return this; }
        public Notification build() { return new Notification(); }
    }
}
""",
    "android/os/SystemClock.java": """package android.os; public class SystemClock { public static long elapsedRealtimeNanos() { if (Boolean.getBoolean("fixture.clockfail")) throw new SecurityException("SECRET-clock"); return System.nanoTime(); } }""",
    "android/util/Log.java": """
package android.util;
public class Log {
    public static final java.util.List<String> records = java.util.Collections.synchronizedList(new java.util.ArrayList<String>());
    public static volatile long[] rawImage;
    public static int i(String tag, String record) {
        if (!tag.equals("SchemiiAcquire")) throw new AssertionError("fixed tag");
        if ("stall".equals(System.getenv("SCHEMII_FIXTURE_MAPS")) && record.contains(" phase=2 ")) {
            try {
                java.lang.reflect.Method image = Class.forName("development.landmine.schemii.android.host.NativeOwner")
                    .getDeclaredMethod("imageRefusal");
                image.setAccessible(true);
                rawImage = (long[])image.invoke(null);
            } catch (ReflectiveOperationException failure) { throw new AssertionError(failure); }
        }
        records.add(record);
        return Boolean.getBoolean("fixture.logloss") ? 0 : record.length();
    }
}
""",
    "android/os/IBinder.java": "package android.os; public interface IBinder {}",
    "android/os/ParcelFileDescriptor.java": """
package android.os;
import java.io.*;
import java.lang.reflect.*;
import java.util.concurrent.atomic.AtomicInteger;
public final class ParcelFileDescriptor implements Closeable {
    public static final int MODE_READ_ONLY = 0x10000000;
    public static final AtomicInteger opens = new AtomicInteger(), closes = new AtomicInteger();
    public static volatile FileDescriptor last;
    private final FileInputStream stream;
    private ParcelFileDescriptor(FileInputStream stream) { this.stream = stream; }
    public static ParcelFileDescriptor open(File file, int mode) throws IOException {
        if (mode != MODE_READ_ONLY || !file.getName().equals("libschemii_owner.so"))
            throw new AssertionError("fixed read-only reference");
        FileInputStream stream = new FileInputStream(file);
        last = stream.getFD();
        opens.incrementAndGet();
        return new ParcelFileDescriptor(stream);
    }
    public int getFd() {
        if (Boolean.getBoolean("fixture.borrowFailure")) throw new SecurityException("SECRET-borrow");
        if (Boolean.getBoolean("fixture.badFd")) return Integer.MAX_VALUE;
        try {
            Field field = FileDescriptor.class.getDeclaredField("fd");
            field.setAccessible(true);
            return field.getInt(last);
        } catch (ReflectiveOperationException error) { throw new AssertionError(error); }
    }
    public void close() throws IOException {
        closes.incrementAndGet();
        if (!last.valid()) throw new AssertionError("JNI closed borrowed reference");
        stream.close();
        if (Boolean.getBoolean("fixture.referenceCloseFailure")) throw new IOException("SECRET-close");
    }
}
""",
    "android/os/UserManager.java": """
package android.os;
public class UserManager { public boolean isUserUnlocked() { return true; } }
""",
    "android/R.java": """
package android;
public class R { public static class drawable { public static int stat_notify_sync = 1; } }
""",
    "android/system/Os.java": """
package android.system;
public class Os { public static void chmod(String path, int mode) { throw new AssertionError("chmod forbidden"); } }
""",
    "android/content/pm/ApplicationInfo.java": """
package android.content.pm;
public class ApplicationInfo {
    public static final int FLAG_DEBUGGABLE = 2;
    public int flags, targetSdkVersion = 36;
    public String nativeLibraryDir;
}
""",
    "android/content/pm/PackageInfo.java": """
package android.content.pm;
public class PackageInfo { public SigningInfo signingInfo = new SigningInfo(); }
""",
    "android/content/pm/SigningInfo.java": """
package android.content.pm;
public class SigningInfo {
    public boolean hasMultipleSigners() { return false; }
    public Signature[] getApkContentsSigners() { return new Signature[] {new Signature()}; }
}
""",
    "android/content/pm/Signature.java": """
package android.content.pm;
public class Signature { public byte[] toByteArray() { return new byte[] {1}; } }
""",
    "android/content/pm/PackageManager.java": """
package android.content.pm;
public abstract class PackageManager {
    public static final int GET_SIGNING_CERTIFICATES = 0x08000000;
    public abstract PackageInfo getPackageInfo(String name, int flags);
}
""",
    "development/landmine/schemii/android/host/HostAnchors.java": """
package development.landmine.schemii.android.host;
public class HostAnchors {
    public static final String COMMIT = "source-double-only";
    public static final String PACKAGE = "fixture.package";
    public static final String SIGNER = "4bf5122f344554c53bde2ebb8cd2b7e3d1600ad631c385a5d7cce23c7785459a";
}
""",
    "development/landmine/schemii/android/host/InstalledSource.java": """
package development.landmine.schemii.android.host;
import android.content.Context;
public final class InstalledSource {
    static final class Framework {
        final String packageName="fixture.package", signer="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
        final String apk, module, data;
        Framework(Context app) {
            data=app.getDataDir().getAbsolutePath();
            apk=data+"/base.apk";
            module=System.getProperty("fixture.module");
        }
    }
    static int frameworkReason() { return 4; }
    static Framework framework(Context app) throws Exception {
        app.getPackageManager().getPackageInfo(app.getPackageName(),0);
        return new Framework(app);
    }
}
""",
}

HARNESS = """
package development.landmine.schemii.android.host;
import android.content.*;
import android.content.pm.*;
import android.os.*;
import java.io.*;
import java.lang.reflect.*;
import java.util.*;
import java.util.concurrent.*;

public class BootstrapControl {
    static void check(boolean condition, String message) {
        if (!condition) throw new AssertionError(message);
    }
    static class App extends Context {
        File data;
        String mode;
        List<String> calls = new ArrayList<>();
        CountDownLatch entered = new CountDownLatch(1), release = new CountDownLatch(1);
        volatile Context first, second, after;
        volatile Throwable workerFailure;
        volatile boolean resumed;
        App(File data, String mode) { this.data = data; this.mode = mode; }
        public Context getApplicationContext() { calls.add("application"); return mode.equals("wrapper") ? null : this; }
        public boolean isDeviceProtectedStorage() { calls.add("DE"); return mode.equals("DE"); }
        public <T> T getSystemService(Class<T> kind) {
            calls.add("user");
            if (mode.equals("missing-user")) return null;
            return kind.cast(new UserManager() {
                public boolean isUserUnlocked() { calls.add("unlock"); return !mode.equals("locked"); }
            });
        }
        public File getDataDir() {
            calls.add("data");
            if (mode.equals("null-data")) return null;
            if (mode.equals("data-error")) throw new SecurityException("private error must not escape");
            return data;
        }
        public PackageManager getPackageManager() {
            return new PackageManager() {
                public PackageInfo getPackageInfo(String name, int flags) {
                    try {
                        first = HostService.frameworkOwnerApplication();
                        second = HostService.frameworkSelectedStorage();
                        check(first == App.this && second == first, "coherent frozen pair");
                        entered.countDown();
                        check(release.await(5, TimeUnit.SECONDS), "test controller release");
                        after = HostService.frameworkSelectedStorage();
                        resumed = true;
                        if (mode.startsWith("pm-error")) throw new SecurityException("private PM error");
                        if (mode.startsWith("link-error")) throw new UnsatisfiedLinkError("private native error");
                        return new PackageInfo();
                    } catch (InterruptedException e) { throw new AssertionError(e); }
                    catch (AssertionError e) { workerFailure = e; throw e; }
                }
            };
        }
    }
    static HostService service(App app, boolean create) {
        HostService service = new HostService();
        service.frameworkApplication = app;
        if (create) service.onCreate();
        return service;
    }
    static void action(HostService service, String action) {
        check(service.onStartCommand(action == null ? null : new Intent(action),0,1) == 2, "nonsticky");
    }
    static ExecutorService executor(HostService service) throws Exception {
        Field field = HostService.class.getDeclaredField("work"); field.setAccessible(true);
        return (ExecutorService)field.get(service);
    }
    static void settle(HostService service) throws Exception {
        executor(service).shutdown();
        check(executor(service).awaitTermination(5, TimeUnit.SECONDS), "finite worker completion");
    }
    static void absent() {
        check(HostService.frameworkOwnerApplication() == null, "no application callback");
        check(HostService.frameworkSelectedStorage() == null, "no storage callback");
    }
    static native boolean mapsBlocked();
    static native void releaseMapsRead();
    static native int ownerCloses();
    static void await(java.util.function.BooleanSupplier condition, String failure) throws Exception {
        long start = System.nanoTime();
        while (!condition.getAsBoolean() && System.nanoTime()-start < TimeUnit.SECONDS.toNanos(5))
            Thread.sleep(2);
        check(condition.getAsBoolean(), failure);
    }
    static List<String> stage10(File data, int expectedCloses) throws Exception {
        App app = new App(data,"CE");
        HostService service = service(app,true);
        android.util.Log.records.clear();
        android.util.Log.rawImage=null;
        action(service,"start");
        check(app.entered.await(5,TimeUnit.SECONDS), "framework producer reached");
        action(service,null);
        check(HostService.status().contains("pending"), "host status responsive");
        action(service,"stop");
        check(HostService.status().contains("Stop observed"), "cancel responsive");
        app.release.countDown();
        settle(service);
        check(HostService.status().contains("First error 13.")
            && HostService.status().contains("Cancellation observed.")
            && HostService.status().contains("Acquisition stage 10:1; last 10.")
            && !HostService.status().contains("Cleanup error")
            && !HostService.status().contains("unresolved"), "independent stage-10 first error and disposal");
        check(ownerCloses()==expectedCloses,"one native close per acquisition");
        Field outstanding=HostService.class.getDeclaredField("outstanding");
        outstanding.setAccessible(true);
        check(outstanding.get(null)==null,"reservation released");
        long[] state=NativeOwner.status();
        check(state.length==16 && state[0]==13 && state[2]==0 && state[4]==0 && state[5]==0,"raw16");
        String record=android.util.Log.records.stream().filter(x -> x.contains("phase=3 ")).findFirst().get();
        String[] pre=record.split(" pre=")[1].split(" post=")[0].split(",");
        String[] post=record.split(" post=")[1].split(" image=")[0].split(",");
        String image=record.split(" image=")[1];
        long[] rawImage=android.util.Log.rawImage;
        byte[] name="/SECRET-fixture/libfirst-refusal.so".getBytes(java.nio.charset.StandardCharsets.US_ASCII);
        check(rawImage!=null && rawImage.length==8+name.length && rawImage[0]==1
            && rawImage[1]==rawImage.length && rawImage[2]==2 && rawImage[3]==7 && rawImage[4]==1
            && rawImage[5]==Long.parseLong(pre[31]) && rawImage[6]==10 && rawImage[7]==name.length,
            "original raw imageRefusal header and epoch");
        for (int i=0;i<name.length;i++) check(rawImage[8+i]==name[i],"original raw imageRefusal byte");
        check(pre.length==34 && post.length==34 && pre[2].equals("10") && pre[3].equals("1")
            && pre[7].equals("3") && pre[14].equals("13") && post[4].equals("24")
            && post[21].equals("4") && pre[31].equals(post[31]) && pre[33].equals("1") && post[33].equals("1"),
            "raw34 and same-worker same-epoch disposal");
        check(image.startsWith("v1,complete=1,") && image.endsWith(",provenance=unknown")
            && image.length()<=256 && !record.contains("SECRET") && record.length()<=2048, "bounded imageRefusal");
        absent();
        service.onDestroy();
        pre[31]=pre[32]=post[31]=post[32]="time";
        return Arrays.asList(Arrays.toString(state), Arrays.toString(pre), Arrays.toString(post), image);
    }
    static void ownMapping(File directory) throws Exception {
        String phase=System.getProperty("fixture.phase");
        boolean stall="stall".equals(System.getenv("SCHEMII_FIXTURE_MAPS"));
        check(NativeOwner.OwnMapping.status().equals("Own mapping not checked"),"explicit tap");
        List<String> before=stall ? stage10(directory,1) : null;
        NativeOwner.OwnMapping.start(directory.getAbsolutePath());
        if (stall) {
            try {
                await(BootstrapControl::mapsBlocked,"maps read stalled");
                NativeOwner.OwnMapping.start(directory.getAbsolutePath());
                check(NativeOwner.OwnMapping.status().startsWith("Own mapping pending")
                    && ParcelFileDescriptor.opens.get()==1 && ParcelFileDescriptor.closes.get()==0,
                    "one pending slot and retained borrow");
                check(stage10(directory,2).equals(before),"stage10 scalar fields unchanged while probe stalled");
                check(NativeOwner.OwnMapping.status().startsWith("Own mapping pending"),
                    "host disposal did not publish mapping result");
            } finally { releaseMapsRead(); }
        }
        await(() -> NativeOwner.OwnMapping.status().contains("sample=1"),"finite terminal result");
        String result=NativeOwner.OwnMapping.status();
        NativeOwner.OwnMapping.start(directory.getAbsolutePath());
        check(NativeOwner.OwnMapping.status().equals(result),"terminal one-shot never replaced");
        String expected=System.getProperty("fixture.fields");
        check(result.length()<400 && result.endsWith(expected)
            && !result.contains("SECRET") && !result.contains(directory.getPath())
            && !result.contains("libschemii_owner.so"), "bounded scalar mismatch: "+result);
        String source=phase.equals("SOURCE") ? "unbound" : "ffffffffffffffffffffffffffffffffffffffff";
        check(result.contains("v=1 profile=closed-connected-host-v1 source="+source+" sample=1"),"source bound independently");
        long begin=Long.parseLong(result.split("beginNs=")[1].split(" ")[0]);
        long end=Long.parseLong(result.split("endNs=")[1].split(" ")[0]);
        check(begin>=0 && end>=begin,"same-clock bounds");
        int borrowed=Integer.getInteger("fixture.borrowed",1);
        check(ParcelFileDescriptor.opens.get()==borrowed && ParcelFileDescriptor.closes.get()==borrowed
            && (borrowed==0 || !ParcelFileDescriptor.last.valid()), "Java reference closed once");
        Method decode=NativeOwner.OwnMapping.class.getDeclaredMethod("valid",long[].class,long.class);
        decode.setAccessible(true);
        long[] raw={1,begin,0,0,1,0};
        check((Boolean)decode.invoke(null,raw,begin)
            && !(Boolean)decode.invoke(null,raw,begin+1)
            && !(Boolean)decode.invoke(null,null,begin)
            && !(Boolean)decode.invoke(null,new long[]{1,begin,0,0,0,0},begin),"stale, missing or malformed JNI record refused");
        if (!stall) check(android.util.Log.records.isEmpty(),"no acquisition record crossover");
    }
    public static void main(String[] args) throws Exception {
        String mode = args[0]; File data = new File(args[1]);
        if (mode.equals("own-mapping")) {
            ownMapping(data);
            if (!System.getProperty("fixture.phase").equals("SOURCE"))
                check(NativeOwner.request(1)!=0,"birth ingress refused");
            return;
        } else if (mode.startsWith("diagnostic-array:")) {
            String variant = mode.substring(17);
            long[] raw = new long[34]; raw[0]=1; raw[1]=34; raw[4]=24; raw[21]=4; raw[31]=1; raw[32]=2; raw[33]=1;
            if (variant.equals("version")) raw[0]=2;
            if (variant.equals("length")) raw=new long[35];
            if (variant.equals("range")) raw[9]=99;
            if (variant.equals("time")) raw[32]=0;
            NativeOwner.Observation d=NativeOwner.Observation.decode(raw);
            if (variant.equals("clone")) {
                check(d!=null && d.disposed(),"valid explicit schema"); raw[18]=1;
                check(d.disposed(),"immutable cloned snapshot");
            } else check(d==null,"unknown/malformed diagnostic unresolved");
        } else if (mode.startsWith("storage:")) {
            String selection = mode.substring(8);
            App app = new App(data, selection);
            boolean refuse = !selection.equals("CE");
            try {
                StorageProfile profile = StorageProfile.select(selection.equals("null") ? null : app, selection.equals("explicit-DE"));
                check(!refuse, "selection must refuse " + selection);
                check(profile.control.equals(new File(data,"files/connected-host-v1/control")), "fixed control layout");
                check(profile.state.getName().equals("state") && profile.sources.getName().equals("sources"), "fixed siblings");
            } catch (IOException e) { check(refuse, "CE unexpectedly refused"); }
            if (selection.equals("CE")) check(app.calls.equals(Arrays.asList("application","DE","user","unlock","data")), "CE call order");
            if (Arrays.asList("null","wrapper","DE","locked","missing-user","explicit-DE").contains(selection))
                check(!app.calls.contains("data"), "refuse before data helper");
        } else if (mode.equals("missing") || mode.equals("null-app") || mode.equals("storage-error")) {
            App app = new App(data, mode.equals("storage-error") ? "data-error" : "CE");
            HostService service = service(app, !mode.equals("missing"));
            if (mode.equals("null-app")) { service.onDestroy(); service = service(null,true); }
            action(service,"start"); settle(service); absent();
            check(!app.resumed, "no signer/acquisition"); service.onDestroy();
        } else if (mode.equals("dispatch-rejected")) {
            App app = new App(data,"CE"); HostService service = service(app,true);
            executor(service).shutdown(); action(service,"start");
            check(HostService.status().contains("dispatch unavailable"), "dispatch refusal retained");
            check(app.calls.isEmpty(), "no work after rejected dispatch"); absent(); service.onDestroy();
        } else if (mode.equals("cancel-queued")) {
            App app = new App(data,"CE"); HostService service = service(app,true);
            CountDownLatch occupied = new CountDownLatch(1), unblock = new CountDownLatch(1);
            executor(service).execute(() -> { occupied.countDown(); try { unblock.await(); } catch (InterruptedException e) { throw new AssertionError(e); } });
            check(occupied.await(5,TimeUnit.SECONDS), "queue occupied");
            action(service,"start"); action(service,"stop"); absent(); unblock.countDown();
            settle(service); check(app.calls.isEmpty(), "cancel before acquire does no storage work"); service.onDestroy();
        } else {
            App app = new App(data,mode); HostService service = service(app,true), replacement = null;
            HostService stale = null;
            if (mode.equals("stale-stop")) {
                stale = service; service = service(app,true);
            }
            action(service,"start");
            check(app.entered.await(5,TimeUnit.SECONDS), "production Java reached PM with frozen pair");
            if (stale != null) { action(stale,"stop"); stale.onDestroy(); }
            check(NativeOwner.open() == 16 && NativeOwner.close() == 16, "pending native resource cannot be replaced/closed by another worker");
            long[] pending = NativeOwner.status();
            check(pending.length == 16 && pending[4] == 1 && pending[5] == 1, "pending scalar status responsive");
            absent(); // other thread cannot borrow
            action(service,null); check(HostService.status().contains("pending"), "status responsive while worker pending");
            if (mode.equals("stop") || mode.equals("pm-error-stop")) action(service,"stop");
            if (mode.equals("destroy") || mode.equals("replace") || mode.equals("link-error-destroy")) service.onDestroy();
            if (mode.equals("replace")) {
                App other = new App(data,"CE"); replacement = service(other,true); action(replacement,"start");
                check(HostService.status().contains("pending"), "replacement refused with pending reservation");
                check(other.calls.isEmpty(), "no competing selection");
            }
            if (mode.equals("supersede")) {
                replacement = service(new App(data,"CE"),true); action(replacement,"start");
                check(HostService.status().contains("pending"), "live service superseded without stealing reservation");
            }
            if (mode.equals("stop")) {
                action(service,"start"); check(HostService.status().contains("pending"), "no cancel revival");
            }
            app.release.countDown(); settle(service);
            check(app.workerFailure == null, "worker assertions observed");
            check(app.resumed, "blocked operation completed");
            boolean invalidated = Arrays.asList("stop","destroy","replace","supersede","pm-error-stop","link-error-destroy").contains(mode);
            check(invalidated ? app.after == null : app.after == app, "stale callback cannot mix or revive");
            check(!HostService.status().contains("private"), "raw exception redacted");
            boolean failed = mode.startsWith("pm-error") || mode.startsWith("link-error");
            check(HostService.status().contains("refused"), "honest refusal stage");
            if (failed && invalidated) check(HostService.status().contains("Cancellation observed"), "first failure distinct from cancellation; no cleanup attempted");
            if (Boolean.getBoolean("fixture.existing") && (mode.equals("normal") || mode.equals("stale-stop") || mode.equals("cleanup-error"))) {
                long[] state=NativeOwner.status();
                check(state[6]==3 && state[7]==2 && state[13]==1 && state[14]==1, "real held objects and unchanged UNKNOWN C/A result");
                if (mode.equals("cleanup-error")) {
                    check(state[2]!=0 && state[4]==1 && HostService.status().contains("unresolved"), "cleanup error retains native and Java reservation");
                    action(service,"start"); check(HostService.status().contains("pending"), "no replacement after unresolved cleanup");
                } else check(state[0]==0 && state[4]==0, "real JNI acquisition/mapping completed and disposed");
            }
            if (Boolean.getBoolean("fixture.existing") && mode.equals("custody-error")) {
                long[] state=NativeOwner.status();
                check(state[0]==5 && state[2]==0 && state[4]==0, "disposed custody error preserved without cleanup failure");
                check(HostService.status().contains("First error 5.") && !HostService.status().contains("Cleanup error"), "operation and cleanup diagnostics remain distinct");
            }
            long[] diagnostic=NativeOwner.observation();
            check(diagnostic.length==34 && diagnostic[0]==1 && diagnostic[1]==34,"separate v1 ABI");
            check(diagnostic[4]==24 && diagnostic[33]==0,"close attempted; main thread is not acquiring worker");
            if (mode.startsWith("pm-error") || mode.startsWith("link-error"))
                check(diagnostic[2]==6 && diagnostic[6]==4,"finite framework failure precedes cancellation/disposal");
            if (!Boolean.getBoolean("fixture.existing") && (mode.equals("normal") || mode.equals("stale-stop")))
                check(diagnostic[2]==12 && diagnostic[9]==3 && diagnostic[11]==1 && diagnostic[12]==2,
                    "selected-root open errno/hop attributed");
            for (String record : android.util.Log.records) {
                check(record.length()<=2048 && record.contains("delivery=unverified"),"bounded explicit log loss domain");
                check(!record.contains(data.getAbsolutePath()) && !record.contains("fixture.package")
                    && !record.contains("private PM") && !record.contains("private native"),"no raw private input in logs");
            }
            check(android.util.Log.records.stream().anyMatch(x -> x.contains("completionObserved=1") && !x.contains("end=-1")),"finite worker completion record");
            if (invalidated) check(android.util.Log.records.stream().anyMatch(x -> x.contains("cancelRequested=1")),"request and completion distinct");
            absent(); service.onDestroy();
            service.onCreate(); action(service,"start"); check(HostService.status().contains("stale"), "destroyed incarnation cannot revive");
            if (replacement != null) replacement.onDestroy();
        }
        for (String method : Arrays.asList("frameworkOwnerApplication","frameworkSelectedStorage")) {
            Method callback = HostService.class.getDeclaredMethod(method);
            check(!Modifier.isPublic(callback.getModifiers()) && Modifier.isStatic(callback.getModifiers()), "fixed nonpublic no-argument callback");
        }
        check(NativeOwner.request(1) != 0, "birth ingress refused");
        System.out.println("PASS " + mode + "; framework edge doubles; real JNI; C/A unproved");
    }
}
"""


def run_java(command: list[str], env=None) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command, capture_output=True, text=True, timeout=20, env=env
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result


@pytest.fixture(scope="module")
def java_bootstrap(tmp_path_factory):
    javac, java = shutil.which("javac"), shutil.which("java")
    assert javac and java, "JDK required for production Java controls"
    work = tmp_path_factory.mktemp("android-bootstrap-source-doubles")
    sources = []
    for name, content in STUBS.items():
        path = work / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        sources.append(str(path))
    harness = work / "BootstrapControl.java"
    harness.write_text(HARNESS)
    classes = work / "classes"
    classes.mkdir()
    run_java(
        [javac, "-source", "8", "-target", "8", "-d", str(classes)]
        + sources
        + [
            str(HOST / name)
            for name in (
                "HostService.java",
                "StorageProfile.java",
                "NativeOwner.java",
            )
        ]
        + [str(harness)]
    )
    native = ROOT / "platforms/android/native/host"
    jdk = Path(javac).resolve().parents[1]
    pins = work / "closed_pins.h"
    pins.write_text(
        '#include "installed_provenance.h"\n#define SCHEMII_OBSERVATION_SOURCE "'
        + "f" * 40
        + '"\nstatic const char *closed_package="fixture.package", *closed_signer="'
        + "a" * 64
        + '";\nstatic const struct installed_pin closed_pins[]={'
        + '{"AndroidManifest.xml","'
        + hashlib.sha256(b"manifest fixture").hexdigest()
        + '"},'
        + '{"classes.dex","'
        + hashlib.sha256(b"dex fixture").hexdigest()
        + '"},'
        + '{"lib/arm64-v8a/libschemii_owner.so",NULL}};\n'
    )
    # Explicit OS library-inventory edge double. Actual dladdr, current proc
    # mapping, held ELF, SHA, ZIP, authority lease and JNI remain production.
    inventory = work / "inventory.c"
    inventory.write_text(r"""
#define _GNU_SOURCE
#include <dlfcn.h>
#include <link.h>
#include <string.h>
#include <stdlib.h>
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <jni.h>
#include <pthread.h>
#include <stdarg.h>
#include <stdio.h>
#include <unistd.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <sys/sysmacros.h>
#include "native_bootstrap.h"
static int inventory_calls, armed, flock_calls;
static int owner_closes;
static _Thread_local int maps_fd=-1, maps_opens, maps_closes;
static pthread_mutex_t latch=PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t released=PTHREAD_COND_INITIALIZER;
static int blocked, release;
JNIEXPORT jboolean JNICALL Java_development_landmine_schemii_android_host_BootstrapControl_mapsBlocked(JNIEnv *e,jclass c) {
    (void)e; (void)c;
    pthread_mutex_lock(&latch);
    int ready=blocked;
    pthread_mutex_unlock(&latch);
    return ready;
}
JNIEXPORT void JNICALL Java_development_landmine_schemii_android_host_BootstrapControl_releaseMapsRead(JNIEnv *e,jclass c) {
    (void)e; (void)c;
    pthread_mutex_lock(&latch);
    release=1;
    pthread_cond_signal(&released);
    pthread_mutex_unlock(&latch);
}
JNIEXPORT jint JNICALL Java_development_landmine_schemii_android_host_BootstrapControl_ownerCloses(JNIEnv *e,jclass c) {
    (void)e; (void)c;
    return owner_closes;
}
int __real_native_bootstrap_close_observed(struct native_bootstrap **,struct native_bootstrap_status *,
    struct acquisition_observation *);
int __wrap_native_bootstrap_close_observed(struct native_bootstrap **o,struct native_bootstrap_status *s,
    struct acquisition_observation *d) {
    ++owner_closes;
    return __real_native_bootstrap_close_observed(o,s,d);
}
static int variant(const char *name) {
    const char *mode=getenv("SCHEMII_FIXTURE_MAPS");
    return mode && !strcmp(mode,name);
}
int __real_open(const char *,int,...);
extern void Java_development_landmine_schemii_android_host_NativeOwner_open(void);

struct fixture_row {
    uintptr_t lo, hi;
    uint64_t offset, inode;
    unsigned device_major, device_minor;
    char permissions[5];
};

/* Get the executing ELF segment from real procfs and its file identity from
 * the loader's own fixture path. Never use the display path from a maps row. */
static struct fixture_row fixture_owner_row(int fd, struct stat *module) {
    uintptr_t entry=(uintptr_t)&Java_development_landmine_schemii_android_host_NativeOwner_open;
    Dl_info image;
    if (!dladdr((void *)entry,&image)) abort();
    int reference=__real_open(image.dli_fname,O_RDONLY|O_CLOEXEC);
    if (reference<0 || fstat(reference,module) || close(reference)
        || !S_ISREG(module->st_mode) || module->st_size<=0) abort();
    FILE *in=fdopen(fd,"r");
    if (!in) abort();
    struct fixture_row owner={0};
    char *line=NULL;
    size_t capacity=0;
    unsigned found=0;
    while (getline(&line,&capacity,in)>=0) {
        struct fixture_row row;
        if (sscanf(line,"%" SCNxPTR "-%" SCNxPTR " %4s %" SCNx64 " %x:%x %" SCNu64,
                   &row.lo,&row.hi,row.permissions,&row.offset,&row.device_major,&row.device_minor,&row.inode)!=7)
            abort();
        if (row.lo<=entry && entry<row.hi) { owner=row; ++found; }
    }
    free(line);
    if (ferror(in) || fclose(in) || found!=1 || strcmp(owner.permissions,"r-xp")
        || owner.offset>=(uint64_t)module->st_size || !owner.inode) abort();
    return owner;
}

/* Independent raw precondition for unmodified real-procfs controls. Return
 * only the identity category to the test runner. */
int fixture_map_identity(void) {
    struct stat module;
    struct fixture_row row=fixture_owner_row(__real_open("/proc/self/maps",O_RDONLY|O_CLOEXEC),&module);
    if (row.inode!=(uint64_t)module.st_ino) return 0;
    return row.device_major==major(module.st_dev) && row.device_minor==minor(module.st_dev) ? 1 : 2;
}

static void substitute_maps(int fd) {
    struct stat module;
    struct fixture_row row=fixture_owner_row(dup(fd),&module);
    FILE *out=tmpfile();
    if (!out) abort();
    /* Same valid executable range/offset, with the loaded module's held-file
     * device and inode regardless of how this filesystem reports procfs IDs. */
    const char *permissions=row.permissions, *path="/fixture/owner.so";
    unsigned device_major=major(module.st_dev), device_minor=minor(module.st_dev);
    uint64_t offset=row.offset, inode=(uint64_t)module.st_ino;
    if (variant("opaque-path")) path="/SECRET-ignored-path";
    if (variant("zero-inode")) inode=0;
    if (variant("wrong-device")) device_minor^=1;
    if (variant("writable")) permissions="rwxp";
    if (variant("nonexec")) permissions="r--p";
    if (variant("offset-overflow")) offset=UINT64_MAX;
    char inode_field[32];
    snprintf(inode_field,sizeof(inode_field),"%" PRIu64,inode);
    if (variant("bad-inode")) strcpy(inode_field,"18446744073709551616");
    fputs("1-2 r--p 0 00:00 0\n",out);
    if (!variant("absent")) {
        int copies=variant("duplicate") ? 2 : 1;
        for (int i=0;i<copies;++i)
            fprintf(out,"%" PRIxPTR "-%" PRIxPTR " %s %" PRIx64 " %x:%x %s %s\n",
                    row.lo,row.hi,permissions,offset,device_major,device_minor,inode_field,path);
    }
    if (variant("malformed")) fputs("malformed after a match\n",out);
    if (variant("truncated")) fputs("1-2 r--p 0 00:00 0",out);
    if (variant("embedded-nul")) {
        fputs("1-2 r--p 0 00:00 0 ",out);
        fputc(0,out); fputc('\n',out);
    }
    if (variant("overlong")) {
        fputs("1-2 r--p 0 00:00 0 ",out);
        for (int i=0;i<8192;++i) fputc(' ',out);
        fputc('\n',out);
    }
    if (variant("total-cap"))
        for (int i=0;i<65536;++i) fputs("1-2 r--p 0 00:00 0\n",out);
    if (fflush(out) || fseek(out,0,SEEK_SET) || dup2(fileno(out),fd)!=fd || fclose(out)) abort();
}
int __wrap_open(const char *path,int flags,...) {
    if (strcmp(path,"/proc/self/maps")) {
        if (!(flags&O_CREAT)) return __real_open(path,flags);
        va_list args; va_start(args,flags);
        int mode=va_arg(args,int); va_end(args);
        return __real_open(path,flags,mode);
    }
    if (flags!=(O_RDONLY|O_CLOEXEC) || ++maps_opens!=1) abort();
    if (variant("open-denied")) { errno=EACCES; return -1; }
    maps_fd=__real_open(path,flags);
    if (maps_fd>=0 && getenv("SCHEMII_FIXTURE_MAPS")
        && !variant("real") && !variant("real-mismatch"))
        substitute_maps(maps_fd);
    return maps_fd;
}
ssize_t __real_read(int,void *,size_t);
ssize_t __wrap_read(int fd,void *out,size_t size) {
    if (fd==maps_fd) {
        if (variant("read-denied") || variant("read-both-closes-fail")) { errno=EPERM; return -1; }
        if (variant("stall")) {
            pthread_mutex_lock(&latch);
            blocked=1;
            while (!release) pthread_cond_wait(&released,&latch);
            pthread_mutex_unlock(&latch);
        }
    }
    return __real_read(fd,out,size);
}
int __real_flock(int,int);
int __wrap_flock(int fd,int op) {
    if (++flock_calls==2 && getenv("SCHEMII_J2_FIXTURE_CUSTODY")) { errno=EIO; return -1; }
    return __real_flock(fd,op);
}
int __real_close(int);
int __wrap_close(int fd) {
    int result=__real_close(fd);
    if (fd>=0 && fd==maps_fd) {
        if (++maps_closes!=1) abort();
        if (variant("close-fail") || variant("read-both-closes-fail")) { errno=EIO; return -1; }
    }
    if (armed) { armed=0; errno=EIO; return -1; }
    return result;
}
int __wrap_dl_iterate_phdr(int (*callback)(struct dl_phdr_info *,size_t,void *), void *arg) {
    Dl_info image;
    if (!dladdr((void *)&Java_development_landmine_schemii_android_host_NativeOwner_open,&image)) return 1;
    struct dl_phdr_info info; memset(&info,0,sizeof(info));
    info.dlpi_name=image.dli_fname;
    int result=callback(&info,sizeof(info),arg);
    if (!result && variant("stall")) {
        info.dlpi_name="/SECRET-fixture/libfirst-refusal.so";
        info.dlpi_phnum=7;
        result=callback(&info,sizeof(info),arg);
    }
    if (++inventory_calls==2 && getenv("SCHEMII_J2_FIXTURE_CLEANUP")) armed=1;
    return result;
}
""")
    library = work / "libschemii_owner.so"
    command = [
        "cc",
        "-std=c11",
        "-Wall",
        "-Wextra",
        "-Werror",
        "-shared",
        "-fPIC",
        "-pthread",
        "-DSCHEMII_CLOSED_PINS",
        "-I",
        str(work),
        "-I",
        str(native),
        "-I",
        str(jdk / "include"),
        "-I",
        str(jdk / "include/linux"),
        *[
            str(native / (name + ".c"))
            for name in (
                "owner_jni",
                "native_bootstrap",
                "installed_provenance",
                "boot_authority",
                "boot_compare",
                "boot_identity",
            )
        ],
        str(inventory),
        "-Wl,--wrap=dl_iterate_phdr",
        "-Wl,--wrap=close",
        "-Wl,--wrap=flock",
        "-Wl,--wrap=open",
        "-Wl,--wrap=read",
        "-Wl,--wrap=native_bootstrap_close_observed",
        "-ldl",
        "-o",
        str(library),
    ]
    run_java(command)
    # The actual fallback compilation lacks closed pins and the dedicated JNI
    # entry. Exercise its unbound source without modifying the production class.
    unbound = work / "unbound"
    unbound.mkdir()
    run_java(
        [arg for arg in command[:-1] if arg != "-DSCHEMII_CLOSED_PINS"]
        + [str(unbound / library.name)]
    )
    return java, classes, library


def mapping_identity(library: Path) -> int:
    if os.statvfs(library).f_flag & os.ST_NOEXEC:
        return 0
    return ctypes.CDLL(str(library)).fixture_map_identity()


@pytest.fixture
def own_mapping_library(java_bootstrap, mode):
    _, _, library = java_bootstrap
    if mode == "real":
        # Qualify the caller's root first. If procfs reports a different
        # device identity there, try a separate executable filesystem.
        if mapping_identity(library) == 1:
            yield library
            return
        with tempfile.TemporaryDirectory(
            prefix="schemii-own-mapping-", dir="/tmp"
        ) as work:
            candidate = Path(work) / library.name
            shutil.copyfile(library, candidate)
            if mapping_identity(candidate) != 1:
                pytest.skip(
                    "no executable fixture root with matching procfs and held-file identity"
                )
            yield candidate
    elif mode == "real-mismatch":
        # Keep the caller's actual backing filesystem and unmodified maps.
        if mapping_identity(library) != 2:
            pytest.skip("temporary root has no observed same-inode device mismatch")
        yield library
    elif mode == "unbound":
        yield library.parent / "unbound" / library.name
    else:
        yield library


@pytest.mark.parametrize(
    ("mode", "phase", "errno", "maps", "maps_errno", "reference", "complete", "loss"),
    [
        ("real", "NONE", 0, 1, 0, 1, 1, 0),
        ("real-mismatch", "MAPS_MATCH", 0, 1, 0, 1, 1, 0),
        ("known-good", "NONE", 0, 1, 0, 1, 1, 0),
        ("opaque-path", "NONE", 0, 1, 0, 1, 1, 0),
        ("stall", "NONE", 0, 1, 0, 1, 1, 0),
        ("open-denied", "MAPS_OPEN", 13, 0, 0, 1, 0, 0),
        ("read-denied", "MAPS_READ", 1, 1, 0, 1, 0, 0),
        ("close-fail", "NONE", 0, 2, 5, 1, 1, 0),
        ("read-both-closes-fail", "MAPS_READ", 1, 2, 5, 2, 0, 0),
        ("reference-close-fail", "NONE", 0, 1, 0, 2, 1, 0),
        ("missing-reference", "REFERENCE_OPEN", 0, 0, 0, 0, 0, 0),
        ("borrow-fail", "REFERENCE_BORROW", 0, 0, 0, 1, 0, 0),
        ("bad-fd", "REFERENCE_STAT", 9, 0, 0, 1, 0, 0),
        ("empty-reference", "REFERENCE_STAT", 0, 0, 0, 1, 0, 0),
        ("unbound", "SOURCE", 0, 0, 0, 0, 0, 1),
        *[
            (mode, "MAPS_MATCH", 0, 1, 0, 1, 1, 0)
            for mode in (
                "wrong-reference",
                "absent",
                "duplicate",
                "zero-inode",
                "wrong-device",
                "writable",
                "nonexec",
                "offset-overflow",
            )
        ],
        *[
            (mode, "MAPS_FORMAT", 0, 1, 0, 1, 0, 0)
            for mode in ("malformed", "bad-inode", "truncated", "embedded-nul")
        ],
        ("overlong", "MAPS_LIMIT", 0, 1, 0, 1, 0, 0),
        ("total-cap", "MAPS_LIMIT", 0, 1, 0, 1, 0, 0),
    ],
)
def test_own_mapping_linked_fixture(
    java_bootstrap,
    own_mapping_library,
    tmp_path,
    mode,
    phase,
    errno,
    maps,
    maps_errno,
    reference,
    complete,
    loss,
):
    java, classes, _ = java_bootstrap
    library = own_mapping_library
    reference_dir = library.parent
    if mode in {"missing-reference", "wrong-reference", "empty-reference", "stall"}:
        reference_dir = tmp_path
        if mode == "stall":
            os.link(library, tmp_path / library.name)
        elif mode == "wrong-reference":
            shutil.copyfile(library, tmp_path / library.name)
        elif mode == "empty-reference":
            (tmp_path / library.name).touch()
    before = snapshot(tmp_path)
    outcome = "match" if phase == "NONE" and maps == reference == 1 else "unknown"
    fields = (
        f"outcome={outcome} phase={phase} errno={errno} mapsClose={maps} "
        f"mapsCloseErrno={maps_errno} referenceClose={reference} complete={complete} loss={loss}"
    )
    env = os.environ.copy()
    env.pop("SCHEMII_FIXTURE_MAPS", None)
    if mode == "open-denied" or maps:
        env["SCHEMII_FIXTURE_MAPS"] = mode
    run_java(
        [
            java,
            "--add-opens=java.base/java.io=ALL-UNNAMED",
            "-Djava.library.path=" + str(library.parent),
            "-Dfixture.module=" + str(library),
            "-Dfixture.phase=" + phase,
            "-Dfixture.fields=" + fields,
            "-Dfixture.borrowed=" + str(int(reference != 0)),
            "-Dfixture.borrowFailure=" + str(mode == "borrow-fail").lower(),
            "-Dfixture.badFd=" + str(mode == "bad-fd").lower(),
            "-Dfixture.referenceCloseFailure=" + str(reference == 2).lower(),
            "-cp",
            str(classes),
            f"{PACKAGE}.BootstrapControl",
            "own-mapping",
            str(reference_dir),
        ],
        env=env,
    )
    assert snapshot(tmp_path) == before


def snapshot(root: Path) -> dict[str, tuple[int, bytes | None]]:
    return {
        str(path.relative_to(root)): (
            stat.S_IMODE(path.lstat().st_mode),
            path.read_bytes() if path.is_file() else None,
        )
        for path in root.rglob("*")
    }


@pytest.mark.parametrize(
    "case",
    [
        "diagnostic-array:version",
        "diagnostic-array:length",
        "diagnostic-array:range",
        "diagnostic-array:time",
        "diagnostic-array:clone",
        "storage:CE",
        "storage:DE",
        "storage:explicit-DE",
        "storage:locked",
        "storage:missing-user",
        "storage:wrapper",
        "storage:null",
        "storage:null-data",
        "storage:missing-data",
        "storage:relative-data",
        "missing",
        "null-app",
        "storage-error",
        "cancel-queued",
        "dispatch-rejected",
        "stale-stop",
        "normal",
        "cleanup-error",
        "custody-error",
        "stop",
        "destroy",
        "replace",
        "supersede",
        "pm-error",
        "link-error",
        "pm-error-stop",
        "link-error-destroy",
    ],
)
@pytest.mark.parametrize(
    "existing", [False, True], ids=["absent-layout", "retained-bytes"]
)
def test_production_ce_bootstrap_refuses_without_mutation(
    java_bootstrap, tmp_path, case, existing
):
    data = tmp_path / "data"
    data.mkdir(mode=0o700)
    if existing:
        control = data / "files/connected-host-v1/control"
        control.mkdir(parents=True, mode=0o700)
        # Deliberately not a valid or trusted lineage. Preserve even malformed
        # original bytes and permissions rather than parse/repair/adopt them.
        (control / "lineage-v1").write_bytes(b"retained\x00uncertain\xfflineage\n")
        (control / "lineage-v1").chmod(0o640)
        (control / "owner-events-v1.jsonl").write_bytes(b"retained incomplete event")
    if not case.startswith("storage:"):
        with zipfile.ZipFile(
            data / "base.apk", "w", compression=zipfile.ZIP_STORED
        ) as apk:
            apk.writestr("AndroidManifest.xml", b"manifest fixture")
            apk.writestr("classes.dex", b"dex fixture")
            apk.writestr(
                "lib/arm64-v8a/libschemii_owner.so", java_bootstrap[2].read_bytes()
            )
    before = snapshot(tmp_path)
    selected = data
    if case == "storage:missing-data":
        selected = data / "absent"
    if case == "storage:relative-data":
        selected = Path("relative-data-must-refuse")
    java, classes, library = java_bootstrap
    result = run_java(
        [
            java,
            "-Djava.library.path=" + str(library.parent),
            "-Dfixture.module=" + str(library),
            "-Dfixture.existing=" + str(existing).lower(),
            "-cp",
            str(classes),
            f"{PACKAGE}.BootstrapControl",
            case,
            str(selected),
        ],
        env={
            **{
                key: value
                for key, value in os.environ.items()
                if key
                not in {"SCHEMII_J2_FIXTURE_CLEANUP", "SCHEMII_J2_FIXTURE_CUSTODY"}
            },
            **({"SCHEMII_J2_FIXTURE_CLEANUP": "1"} if case == "cleanup-error" else {}),
            **({"SCHEMII_J2_FIXTURE_CUSTODY": "1"} if case == "custody-error" else {}),
        },
    )
    assert f"PASS {case}" in result.stdout
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize("anchors", [False, True], ids=["diagnostic", "legacy-host"])
def test_legacy_activity_routes_with_its_own_profile(java_bootstrap, tmp_path, anchors):
    """Execute the actual diagnostic guard/helper, without Android UI actions.

    The production service is compiled by java_bootstrap. Only the Activity's
    fixed guard and helper are extracted because its UI/diagnostic SDK surface
    is outside this host-JVM fixture; the helper bytes are never replaced.
    """
    activity = (
        ROOT
        / "platforms/android/app/src/main/java/development/landmine/schemii/runtimeprobe/ProbeActivity.java"
    ).read_text()
    start = activity.index("    private static boolean hostControlsAvailable() {")
    end = activity.index("\n    @Override", start)
    helper = activity[start:end]
    guard = f"{PACKAGE}.HostService.available() && hostControlsAvailable()"
    create = activity[activity.index("@Override public void onCreate(Bundle state)") :]
    assert f"if ({guard}) {{" in create
    java, classes, _ = java_bootstrap
    # The general bootstrap fixture has a compile-only HostAnchors stub.
    # Isolate its classpath and remove only that stub; never modify the shared
    # fixture or replace the compiled production service/helper under test.
    route_classes = tmp_path / "classes"
    anchor_class = Path(*PACKAGE.split(".")) / "HostAnchors.class"
    for compiled in classes.rglob("*.class"):
        relative = compiled.relative_to(classes)
        if relative == anchor_class:
            continue
        target = route_classes / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(compiled.read_bytes())
    assert not (route_classes / anchor_class).exists()
    sources = []
    if anchors:
        anchor = tmp_path / "HostAnchors.java"
        anchor.write_text(
            f'package {PACKAGE}; public class HostAnchors {{ public static final String COMMIT="fixture"; }}'
        )
        sources.append(str(anchor))
    harness = tmp_path / "LegacyRoute.java"
    harness.write_text(
        "public class LegacyRoute {\n"
        + helper
        + "\npublic static void main(String[] ignored) { System.out.println("
        + guard
        + ' ? "host" : "diagnostic"); }\n}\n'
    )
    run_java(
        [
            "javac",
            "-cp",
            str(route_classes),
            "-d",
            str(route_classes),
            *sources,
            str(harness),
        ]
    )
    result = run_java([java, "-cp", str(route_classes), "LegacyRoute"])
    assert result.stdout.strip() == ("host" if anchors else "diagnostic")


@pytest.mark.parametrize(
    "mode",
    [
        "valid",
        "version",
        "length",
        "throw",
        "time",
        "epoch",
        "first-stage",
        "flaky",
        "logloss",
        "clock",
        "source",
        "boot-unobserved",
        "boot-max",
        *[
            f"boot-{index}-{edge}"
            for index in range(22, 31)
            for edge in ("high", "negative")
        ],
    ],
)
def test_observation_failure_never_skips_native_cleanup(tmp_path, mode):
    """Production service/parser; explicit native report ABI edge double only.

    This proves Java failure isolation, not JNI or Android observations. The
    actual JNI projection is covered separately by the normal bootstrap cases.
    """
    sources = []
    for name, content in STUBS.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        sources.append(str(path))
    # Keep the real immutable Observation decoder verbatim, replace only native
    # ABI edges in this isolated classpath; no product hook or setter exists.
    owner = (HOST / "NativeOwner.java").read_text()
    owner = owner.replace('    static { System.loadLibrary("schemii_owner"); }', "")
    owner = owner.replace(
        "static native int open();", "static int open() { return 13; }"
    )
    owner = owner.replace(
        "static native int request(int operation);",
        "static int request(int operation) { return 13; }",
    )
    owner = owner.replace(
        "static native int close();",
        "static int close() { closes++; return 0; } static int closes; static int reports;",
    )
    owner = owner.replace(
        "static native String observationSource();",
        'static String observationSource() { if (System.getProperty("mode").equals("source")) return "SECRET-source"; return "'
        + "f" * 40
        + '"; }',
    )
    owner = owner.replace(
        "static native long[] status();",
        "static long[] status() { long[] a=new long[16]; a[0]=13; return a; }",
    )
    owner = owner.replace(
        "static native long[] observation();",
        """static long[] observation() {
        String mode=System.getProperty("mode"); reports++;
        if (mode.equals("throw")) throw new OutOfMemoryError("SECRET-NATIVE-REPORT");
        long[] a=new long[34]; a[0]=1; a[1]=34; a[2]=10; a[3]=1; a[4]=closes==0 ? 10 : 24; a[14]=13; a[21]=closes==0 ? 2 : 4; a[31]=1; a[32]=2; a[33]=1;
        if (mode.equals("version")) a[0]=99;
        if (mode.equals("length") || (mode.equals("flaky") && reports==1)) a=new long[16];
        if (mode.equals("time")) a[32]=0;
        if (mode.equals("epoch") && closes>0) { a[31]=3; a[32]=4; }
        if (mode.equals("first-stage") && closes>0) a[2]=9;
        if (mode.equals("boot-unobserved")) {
            a[22]=2; a[23]=3; a[28]=1; a[29]=1; a[30]=2;
            for (int i=24; i<=27; i++) a[i]=-1;
        } else if (mode.startsWith("boot-")) {
            long[] maximum={2,15,11,8,4095,4095,2,3,2};
            if (mode.equals("boot-max")) {
                for (int i=22; i<=30; i++) a[i]=maximum[i-22];
            } else {
                String[] parts=mode.split("-");
                int index=Integer.parseInt(parts[1]);
                a[index]=parts[2].equals("high") ? maximum[index-22]+1
                    : index>=24 && index<=27 ? -2 : -1;
            }
        }
        return a;
    }""",
    )
    native = tmp_path / "NativeOwner.java"
    native.write_text(owner)
    sources += [
        str(native),
        str(HOST / "HostService.java"),
        str(HOST / "StorageProfile.java"),
    ]
    harness = tmp_path / "MalformedControl.java"
    harness.write_text("""
package development.landmine.schemii.android.host;
import android.content.*; import android.os.*; import java.io.*; import java.lang.reflect.*; import java.util.concurrent.*;
public class MalformedControl {
 public static void main(String[] args) throws Exception {
  HostService s=new HostService();
  s.frameworkApplication=new Context() {
   public File getDataDir() { return new File(args[0]); }
   public <T> T getSystemService(Class<T> c) { return c.cast(new UserManager()); }
  };
  s.onCreate(); s.onStartCommand(new Intent("start"),0,1);
  Field field=HostService.class.getDeclaredField("work");field.setAccessible(true);
  ExecutorService work=(ExecutorService)field.get(s);work.shutdown();
  if (!work.awaitTermination(5,TimeUnit.SECONDS) || NativeOwner.closes!=1) throw new AssertionError("cleanup skipped");
  Field pending=HostService.class.getDeclaredField("outstanding");pending.setAccessible(true);
  String mode=System.getProperty("mode");
  boolean malformed=!(mode.equals("valid") || mode.equals("logloss") || mode.equals("clock") || mode.equals("boot-unobserved") || mode.equals("boot-max"));
  if ((pending.get(null)!=null)!=malformed || !HostService.status().contains("First error 13.")) throw new AssertionError("unknown report released or first failure erased");
  if (System.getProperty("mode").equals("logloss") && !HostService.status().contains("Observation loss detected")) throw new AssertionError("loss not surfaced");
  for (String line:android.util.Log.records) if(line.contains("SECRET") || line.contains(args[0])) throw new AssertionError("redaction");
  s.onDestroy();
 }
}
""")
    sources.append(str(harness))
    classes = tmp_path / "classes"
    classes.mkdir()
    run_java(["javac", "-source", "8", "-target", "8", "-d", str(classes), *sources])
    run_java(
        [
            "java",
            "-Dmode=" + mode,
            "-Dfixture.logloss=" + str(mode == "logloss").lower(),
            "-Dfixture.clockfail=" + str(mode == "clock").lower(),
            "-cp",
            str(classes),
            PACKAGE + ".MalformedControl",
            str(tmp_path),
        ]
    )


@pytest.mark.parametrize("fault", ["none", "holder", "set", "get", "remove"])
@pytest.mark.parametrize(
    "mode,reason", [("application", 1), ("storage", 2), ("loader", 3)]
)
def test_actual_framework_producer_retains_finite_failure_reason(
    tmp_path, mode, reason, fault
):
    """Actual framework validator, negative fake-framework edges only.

    JVM loaders cannot supply Android's required loader graph, so no positive
    framework/package/caller claim is made. JNI PM exceptions are separately
    exercised through the lifecycle fixture's declared observation-edge double.
    """
    stubs = dict(STUBS)
    stubs.pop("development/landmine/schemii/android/host/InstalledSource.java")
    stubs["android/content/Context.java"] = stubs[
        "android/content/Context.java"
    ].replace(
        "public abstract <T> T getSystemService",
        "public ClassLoader getClassLoader() { return getClass().getClassLoader(); }\n    public abstract <T> T getSystemService",
    )
    stubs["android/content/pm/ApplicationInfo.java"] = """package android.content.pm;
public class ApplicationInfo {
 public static final int FLAG_DEBUGGABLE=2; public int flags,targetSdkVersion,uid;
 public String packageName,className,appComponentFactory,processName,sourceDir,publicSourceDir,nativeLibraryDir,dataDir;
 public String[] splitSourceDirs,splitPublicSourceDirs,sharedLibraryFiles;
} """
    stubs["android/content/pm/PackageInfo.java"] = """package android.content.pm;
public class PackageInfo {
 public String packageName; public ApplicationInfo applicationInfo; public String[] splitNames;
 public Object[] providers,receivers,instrumentation;
 public ActivityInfo[] activities,services; public SigningInfo signingInfo;
} """
    stubs["android/content/pm/ActivityInfo.java"] = """package android.content.pm;
public class ActivityInfo { public String name,processName; public boolean exported; }"""
    stubs["android/content/pm/PackageManager.java"] = """package android.content.pm;
public abstract class PackageManager {
 public static final int GET_SIGNING_CERTIFICATES=1,GET_ACTIVITIES=2,GET_SERVICES=4,GET_PROVIDERS=8,GET_RECEIVERS=16,GET_INSTRUMENTATION=32,GET_SHARED_LIBRARY_FILES=64;
 public abstract PackageInfo getPackageInfo(String name,int flags);
} """
    stubs["android/os/Process.java"] = (
        "package android.os; public class Process { public static int myUid() { return 100; } }"
    )
    stubs["development/landmine/schemii/android/host/HostActivity.java"] = (
        "package development.landmine.schemii.android.host; public class HostActivity {}"
    )
    stubs["android/app/Application.java"] = """package android.app;
import android.content.Context; import android.os.UserManager; import java.io.File;
public class Application extends Context {
 public File getDataDir() { return new File(System.getProperty("data")); }
 public <T> T getSystemService(Class<T> c) { return c.cast(new UserManager() { public boolean isUserUnlocked() { return !System.getProperty("mode").equals("storage"); } }); }
} """
    sources = []
    for n, content in stubs.items():
        p = tmp_path / n
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        sources.append(str(p))
    harness = tmp_path / "ReasonControl.java"
    harness.write_text("""package development.landmine.schemii.android.host;
import android.content.Context;
public class ReasonControl {
 public static void main(String[] args) throws Exception {
  Context app=System.getProperty("mode").equals("application") ? new android.app.Service() : new android.app.Application();
  try { InstalledSource.framework(app); throw new AssertionError("must refuse"); }
  catch (java.io.IOException expected) { if (InstalledSource.frameworkReason()!=Integer.parseInt(args[0])) throw new AssertionError("finite reason lost"); if(InstalledSource.frameworkReason()!=0) throw new AssertionError("stale phase reused"); }
 }
} """)
    installed_source = HOST / "InstalledSource.java"
    if fault != "none":
        # Only the ThreadLocal allocation/storage edge is injected in a private
        # source copy; actual framework guards and catch/consume code stay exact.
        # No product setter, hook, injected source or class is packaged.
        copied = installed_source.read_text()
        if fault == "holder":
            allocation = 'throw new OutOfMemoryError("SECRET-phase-holder");'
        else:
            signature = {
                "set": "void set(Integer value)",
                "get": "Integer get()",
                "remove": "void remove()",
            }[fault]
            allocation = (
                "return new ThreadLocal<Integer>() { @Override public "
                + signature
                + ' { throw new OutOfMemoryError("SECRET-phase-edge"); } };'
            )
        assert "return new ThreadLocal<Integer>();" in copied
        copied = copied.replace("return new ThreadLocal<Integer>();", allocation)
        installed_source = tmp_path / "InstalledSource.java"
        installed_source.write_text(copied)
    classes = tmp_path / "classes"
    classes.mkdir()
    run_java(
        [
            "javac",
            "-source",
            "8",
            "-target",
            "8",
            "-d",
            str(classes),
            *sources,
            str(installed_source),
            *[
                str(HOST / n)
                for n in (
                    "StorageProfile.java",
                    "NativeOwner.java",
                    "HostService.java",
                )
            ],
            str(harness),
        ]
    )
    run_java(
        [
            "java",
            "-Dmode=" + mode,
            "-Ddata=" + str(tmp_path),
            "-cp",
            str(classes),
            PACKAGE + ".ReasonControl",
            str(reason if fault == "none" else 0),
        ]
    )
