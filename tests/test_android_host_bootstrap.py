"""Finite production-Java ordering controls; not Android or native provenance.

Only Android/framework and the installed framework observation edge are doubles.
HostService, StorageProfile, NativeOwner and JNI are compiled unchanged. No
positive native admission, APK build, framework process or device is exercised.
"""

from pathlib import Path
import shutil
import stat
import hashlib
import os
import zipfile
import subprocess

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
    "android/os/IBinder.java": "package android.os; public interface IBinder {}",
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
public class ApplicationInfo { public static final int FLAG_DEBUGGABLE = 2; public int flags, targetSdkVersion = 36; }
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
    public static void main(String[] args) throws Exception {
        String mode = args[0]; File data = new File(args[1]);
        if (mode.startsWith("storage:")) {
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
        '#include "installed_provenance.h"\nstatic const char *closed_package="fixture.package", *closed_signer="'
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
    inventory.write_text("""
#define _GNU_SOURCE
#include <dlfcn.h>
#include <link.h>
#include <string.h>
#include <stdlib.h>
#include <errno.h>
#include <unistd.h>
static int inventory_calls, armed;
int __real_close(int);
int __wrap_close(int fd) {
    int result=__real_close(fd);
    if (armed) { armed=0; errno=EIO; return -1; }
    return result;
}
extern void Java_development_landmine_schemii_android_host_NativeOwner_open(void);
int __wrap_dl_iterate_phdr(int (*callback)(struct dl_phdr_info *,size_t,void *), void *arg) {
    Dl_info image;
    if (!dladdr((void *)&Java_development_landmine_schemii_android_host_NativeOwner_open,&image)) return 1;
    struct dl_phdr_info info; memset(&info,0,sizeof(info));
    info.dlpi_name=image.dli_fname;
    int result=callback(&info,sizeof(info),arg);
    if (++inventory_calls==2 && getenv("SCHEMII_J2_FIXTURE_CLEANUP")) armed=1;
    return result;
}
""")
    library = work / "libschemii_owner.so"
    run_java(
        [
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
            "-ldl",
            "-o",
            str(library),
        ]
    )
    return java, classes, library


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
                if key != "SCHEMII_J2_FIXTURE_CLEANUP"
            },
            **({"SCHEMII_J2_FIXTURE_CLEANUP": "1"} if case == "cleanup-error" else {}),
        },
    )
    assert f"PASS {case}" in result.stdout
    assert snapshot(tmp_path) == before
