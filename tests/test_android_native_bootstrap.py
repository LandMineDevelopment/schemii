"""Production held-object controls. Host bytes/doubles do not prove Android C/A."""

import ctypes
import errno
import hashlib
import io
from pathlib import Path
import shutil
import subprocess
import struct
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOST = ROOT / "platforms/android/native/host"


class Pin(ctypes.Structure):
    _fields_ = [("name", ctypes.c_char_p), ("sha256", ctypes.c_char_p)]


class Errors(ctypes.Structure):
    _fields_ = [
        (name, ctypes.c_int) for name in ("error", "cleanup", "secondary", "failures")
    ]


@pytest.fixture(scope="module")
def installed(tmp_path_factory):
    work = tmp_path_factory.mktemp("installed-real-c")
    wrapper = work / "fault.c"
    wrapper.write_text("""
#include <errno.h>
#include <unistd.h>
#include <string.h>
int fail_strdup;
char *__real_strdup(const char *);
char *__wrap_strdup(const char *s) {
 if (fail_strdup>0 && --fail_strdup==0) { errno=ENOMEM; return NULL; }
 return __real_strdup(s);
}
int fail_close;
int __real_close(int);
int __wrap_close(int fd) {
    int result=__real_close(fd);
    if (fail_close) { fail_close--; errno=EIO; return -1; }
    return result;
}
""")
    target = work / "installed.so"
    subprocess.run(
        [
            "cc",
            "-std=c11",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-fPIC",
            "-shared",
            *[
                str(HOST / (name + ".c"))
                for name in (
                    "installed_provenance",
                    "native_bootstrap",
                    "boot_authority",
                    "boot_compare",
                    "boot_identity",
                )
            ],
            str(wrapper),
            "-Wl,--wrap=close",
            "-Wl,--wrap=strdup",
            "-o",
            str(target),
        ],
        check=True,
    )
    lib = ctypes.CDLL(str(target))
    lib.installed_open.argtypes = [ctypes.c_char_p] * 3 + [
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(Errors),
    ]
    lib.installed_validate.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(Pin),
        ctypes.c_size_t,
    ]
    lib.installed_close.argtypes = [
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(Errors),
    ]
    return lib


def specimen(tmp_path, mode):
    data = tmp_path / "data"
    control = data / "files/connected-host-v1/control"
    control.mkdir(parents=True, mode=0o700)
    module = tmp_path / "libschemii_owner.so"
    module.write_bytes(b"independent held module fixture\x00\xff")
    members = {
        "AndroidManifest.xml": b"manifest fixture",
        "classes.dex": b"dex fixture",
        "lib/arm64-v8a/libschemii_owner.so": module.read_bytes(),
    }
    pins = (Pin * len(members))(
        *[
            Pin(
                k.encode(),
                None
                if k.startswith("lib/")
                else hashlib.sha256(v).hexdigest().encode(),
            )
            for k, v in members.items()
        ]
    )
    if mode == "unknown":
        members["hidden.dex"] = b"unknown"
    if mode == "tamper":
        members["classes.dex"] = b"tampered"
    if mode == "split":
        members["classes2.dex"] = b"split"
    stream = io.BytesIO()
    compression = zipfile.ZIP_DEFLATED if mode == "compressed" else zipfile.ZIP_STORED
    with zipfile.ZipFile(stream, "w", compression=compression) as apk:
        for name, value in members.items():
            apk.writestr(name, value)
        if mode == "duplicate":
            with pytest.warns(UserWarning):
                apk.writestr("classes.dex", b"duplicate")
    apk = tmp_path / "base.apk"
    content = stream.getvalue()
    padded_modes = {
        "page-padded-signblock",
        "nonzero-sign-padding",
        "short-sign-padding",
        "long-sign-padding",
        "padded-unknown-signblock",
        "padded-header-size",
        "padded-footer-size",
        "padded-no-signatures",
    }
    if mode in {"signblock", "unknown-signblock", "invalid-gap"} | padded_modes:
        central = struct.unpack_from("<I", content, len(content) - 6)[0]
        identity = (
            0xDEADBEEF
            if mode in {"unknown-signblock", "padded-unknown-signblock"}
            else 0x42726577
            if mode == "padded-no-signatures"
            else 0x7109871A
        )
        pair = struct.pack("<QI", 8, identity) + (
            b"\0" * 4 if mode == "padded-no-signatures" else b"test"
        )
        size = len(pair) + 24
        block = (
            struct.pack("<Q", size)
            + pair
            + struct.pack("<Q", size)
            + b"APK Sig Block 42"
        )
        # SDK36's real signed product pads the data domain to a 4KiB block
        # boundary. These are structural fixtures, not signature/C/A proof.
        padding = b"\0" * (-central % 4096) if mode in padded_modes else b""
        if mode == "nonzero-sign-padding":
            padding = b"X" + padding[1:]
        elif mode == "short-sign-padding":
            padding = padding[:-1]
        elif mode == "long-sign-padding":
            padding += b"\0" * 4096
        elif mode == "padded-header-size":
            block = struct.pack("<Q", size + 1) + block[8:]
        elif mode == "padded-footer-size":
            block = block[:-24] + struct.pack("<Q", (1 << 64) - 1) + block[-16:]
        if mode == "invalid-gap":
            block = b"unknown gap"
        inserted = padding + block
        content = bytearray(content[:central] + inserted + content[central:])
        struct.pack_into("<I", content, len(content) - 6, central + len(inserted))
    apk.write_bytes(content)
    if mode == "module-tamper":
        module.write_bytes(b"different module")
    if mode == "symlink":
        apk.rename(tmp_path / "target.apk")
        apk.symlink_to(tmp_path / "target.apk")
    return data, control, apk, module, pins


@pytest.mark.parametrize(
    "mode",
    [
        "valid",
        "signblock",
        "unknown-signblock",
        "invalid-gap",
        "page-padded-signblock",
        "nonzero-sign-padding",
        "short-sign-padding",
        "long-sign-padding",
        "padded-unknown-signblock",
        "padded-header-size",
        "padded-footer-size",
        "padded-no-signatures",
        "tamper",
        "unknown",
        "split",
        "compressed",
        "duplicate",
        "module-tamper",
        "symlink",
        "missing",
        "replace",
        "mutate",
        "root-replace",
        "module-replace",
        "missing-pin",
        "wrong-pin",
        "cleanup-error",
    ],
)
def test_held_installed_objects(installed, tmp_path, mode):
    data, control, apk, module, pins = specimen(tmp_path, mode)
    if mode == "missing":
        control.rmdir()
    out, errors = ctypes.c_void_p(), Errors()
    result = installed.installed_open(
        bytes(apk), bytes(module), bytes(data), ctypes.byref(out), ctypes.byref(errors)
    )
    assert out.value  # even partial acquisition has a sole disposer
    if mode in {"symlink", "missing"}:
        assert result != 0
    else:
        assert result == 0
        if mode == "replace":
            apk.rename(tmp_path / "old.apk")
            shutil.copyfile(tmp_path / "old.apk", apk)
        if mode == "mutate":
            with apk.open("r+b") as f:
                f.seek(35)
                f.write(b"X")
        if mode == "root-replace":
            control.rename(control.with_name("old"))
            control.mkdir(mode=0o700)
        if mode == "module-replace":
            module.rename(tmp_path / "old.so")
            shutil.copyfile(tmp_path / "old.so", module)
        if mode == "wrong-pin":
            pins[1].sha256 = b"0" * 64
        result = installed.installed_validate(
            out, pins, len(pins) - (mode == "missing-pin")
        )
        assert (result == 0) == (
            mode in {"valid", "signblock", "page-padded-signblock", "cleanup-error"}
        )
    if mode == "cleanup-error":
        ctypes.c_int.in_dll(installed, "fail_close").value = 1
    result = installed.installed_close(ctypes.byref(out), ctypes.byref(errors))
    if mode == "cleanup-error":
        assert (
            result != 0 and errors.cleanup != 0 and errors.failures == 1 and out.value
        )
        # Tombstone is retained. Never retry Linux close or infer release.
    else:
        assert result == 0 and not out.value


def test_native_hash_matches_independent_vectors(installed):
    installed.installed_sha256.argtypes = [
        ctypes.c_char_p,
        ctypes.c_size_t,
        ctypes.c_void_p,
    ]
    for content in [
        b"",
        b"abc",
        b"a" * 55,
        b"b" * 56,
        b"c" * 64,
        bytes(range(256)) * 9,
    ]:
        output = ctypes.create_string_buffer(32)
        installed.installed_sha256(content, len(content), output)
        assert output.raw == hashlib.sha256(content).digest()


class BootResult(ctypes.Structure):
    _fields_ = [
        (name, ctypes.c_int32)
        for name in (
            "relation",
            "reason",
            "observation_status",
            "observation_stage",
            "error",
            "cleanup_error",
            "current_admission",
            "prior_admission",
            "continuity",
        )
    ]


class ObjectReport(ctypes.Structure):
    _fields_ = [
        (n, ctypes.c_int)
        for n in ("error", "kind", "hop", "operation", "syscall_error")
    ]


class AcquisitionReport(ctypes.Structure):
    _fields_ = [
        (n, ctypes.c_int)
        for n in (
            "first_stage",
            "first_substage",
            "last_stage",
            "last_substage",
            "error",
            "secondary",
            "framework_reason",
            "image_reason",
            "image_count",
        )
    ] + [
        ("object", ObjectReport),
        ("cleanup", ctypes.c_int),
        ("secondary_cleanup", ctypes.c_int),
    ]


class BootstrapStatus(ctypes.Structure):
    _fields_ = [
        (name, ctypes.c_int)
        for name in (
            "error",
            "secondary",
            "cleanup",
            "secondary_cleanup",
            "retained",
            "stage",
        )
    ] + [("boot", BootResult)]


def test_real_native_bootstrap_lease_refusal_and_release(installed, tmp_path):
    data, control, apk, module, pins = specimen(tmp_path, "valid")
    original = sorted(p.name for p in control.iterdir())
    installed.native_bootstrap_open.argtypes = [ctypes.c_char_p] * 3 + [
        ctypes.POINTER(Pin),
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    installed.native_bootstrap_status.argtypes = [ctypes.c_void_p]
    installed.native_bootstrap_status.restype = BootstrapStatus
    installed.native_bootstrap_close.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
    owner = ctypes.c_void_p()
    installed.native_bootstrap_open(
        bytes(apk), bytes(module), bytes(data), pins, len(pins), ctypes.byref(owner)
    )
    assert owner.value
    status = installed.native_bootstrap_status(owner)
    assert status.retained == 1 and status.boot.relation == 2
    assert status.boot.current_admission == status.boot.prior_admission == 1
    competitor = ctypes.c_void_p()
    assert (
        installed.native_bootstrap_open(
            bytes(apk),
            bytes(module),
            bytes(data),
            pins,
            len(pins),
            ctypes.byref(competitor),
        )
        != 0
    )
    assert installed.native_bootstrap_close(ctypes.byref(competitor)) == 0
    assert installed.native_bootstrap_close(ctypes.byref(owner)) == 0
    assert not owner.value
    assert sorted(p.name for p in control.iterdir()) == original
    # The released lease can be freshly acquired without claiming historical A.
    installed.native_bootstrap_open(
        bytes(apk), bytes(module), bytes(data), pins, len(pins), ctypes.byref(owner)
    )
    assert installed.native_bootstrap_close(ctypes.byref(owner)) == 0


@pytest.mark.parametrize(
    "mode",
    [
        "custody-probe",
        "two-cleanup-errors",
        "earlier-error-and-cleanup",
        "custody-then-stale",
        "reader-then-custody",
    ],
)
def test_jni_close_preserves_disposed_and_retained_reports(tmp_path, mode):
    """Real production JNI close, seeded through a private fixture only.

    Real acquisition/lease/descriptors and disposal run unchanged. Only openat, flock
    and close outcomes are injected; no Java/framework/C/A admission is claimed.
    """
    data, _, apk, module, pins = specimen(tmp_path, "valid")
    javac = shutil.which("javac")
    assert javac, "JDK headers required for production JNI controls"
    include = Path(javac).resolve().parents[1] / "include"
    wrapper = tmp_path / "close-report.c"
    wrapper.write_text("""
#define _GNU_SOURCE
#include "owner_jni.c"
#include <fcntl.h>
#include <stdarg.h>
#include <unistd.h>
static int fail_probe, fail_reader, flock_calls, armed, close_calls;
int __real_openat(int,const char *,int,...);
int __wrap_openat(int fd,const char *path,int flags,...) {
  if (fail_reader && !strcmp(path,"/proc")) { errno=EACCES; return -1; }
  if (flags&O_CREAT) {
    va_list args; va_start(args,flags); int mode=va_arg(args,int); va_end(args);
    return __real_openat(fd,path,flags,mode);
  }
  return __real_openat(fd,path,flags);
}
int __real_flock(int,int);
int __wrap_flock(int fd,int op) {
  if (++flock_calls==fail_probe) { errno=EIO; return -1; }
  return __real_flock(fd,op);
}
int __real_close(int);
int __wrap_close(int fd) {
  int result=__real_close(fd);
  if (armed && ++close_calls<=2) {
    errno=close_calls==1 ? EIO : ENOSPC; return -1;
  }
  return result;
}
int exercise(const char *apk,const char *module,const char *data,
             const struct installed_pin *pins,size_t count,int mode,
             struct native_bootstrap_status *report,int *released,struct acquisition_observation *diagnostic) {
  fail_probe=(mode==1 || mode==4) ? 2 : mode==5 ? 3 : 0;
  fail_reader=mode==5;
  int opened=native_bootstrap_open(apk,module,data,pins,count,&owner);
  int expected=(mode==1 || mode==4) ? EIO : mode==5 ? EACCES : 0;
  if (opened!=expected || !owner) return -1;
  if (mode==3) {
    native_bootstrap_refuse(owner,EACCES);
    struct acquisition_observation d=native_bootstrap_observe(owner);
    acquisition_step(&d,AQ_FRAMEWORK,1); acquisition_failure(&d,EACCES);
    observation=d;
  }
  if (mode==4) {
    int changed=open(apk,O_WRONLY|O_CLOEXEC);
    if (changed<0 || ftruncate(changed,1) || close(changed)) return -2;
    if (native_bootstrap_validate(owner,pins,count)!=ESTALE) return -3;
  }
  reserved=1; busy=0; worker=pthread_self();
  armed=mode==2 || mode==3;
  int result=Java_development_landmine_schemii_android_host_NativeOwner_close(NULL,NULL);
  *report=snapshot; *released=owner==NULL; *diagnostic=observation;
  return result;
}
""")
    target = tmp_path / "close-report.so"
    subprocess.run(
        [
            "cc",
            "-std=c11",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-fPIC",
            "-shared",
            "-pthread",
            "-I",
            str(HOST),
            "-I",
            str(include),
            "-I",
            str(include / "linux"),
            *[
                str(HOST / (name + ".c"))
                for name in (
                    "native_bootstrap",
                    "installed_provenance",
                    "boot_authority",
                    "boot_compare",
                    "boot_identity",
                )
            ],
            str(wrapper),
            "-Wl,--wrap=flock",
            "-Wl,--wrap=close",
            "-Wl,--wrap=openat",
            "-ldl",
            "-o",
            str(target),
        ],
        check=True,
    )
    lib = ctypes.CDLL(str(target))
    lib.exercise.argtypes = [ctypes.c_char_p] * 3 + [
        ctypes.POINTER(Pin),
        ctypes.c_size_t,
        ctypes.c_int,
        ctypes.POINTER(BootstrapStatus),
        ctypes.POINTER(ctypes.c_int),
        ctypes.POINTER(AcquisitionReport),
    ]
    report, released, diagnostic = (
        BootstrapStatus(),
        ctypes.c_int(),
        AcquisitionReport(),
    )
    number = {
        "custody-probe": 1,
        "two-cleanup-errors": 2,
        "earlier-error-and-cleanup": 3,
        "custody-then-stale": 4,
        "reader-then-custody": 5,
    }[mode]
    result = lib.exercise(
        bytes(apk),
        bytes(module),
        bytes(data),
        pins,
        len(pins),
        number,
        ctypes.byref(report),
        ctypes.byref(released),
        ctypes.byref(diagnostic),
    )
    assert result == errno.EIO
    assert report.error == (errno.EACCES if number in (3, 5) else errno.EIO)
    assert diagnostic.last_stage == 24 and diagnostic.last_substage == 2
    assert diagnostic.first_stage == (6 if number == 3 else 24 if number == 2 else 15)
    assert diagnostic.error == (
        0 if number == 2 else errno.EACCES if number in (3, 5) else errno.EIO
    )
    assert diagnostic.secondary == (
        errno.ESTALE if number == 4 else errno.EIO if number == 5 else 0
    )
    assert report.boot.relation == 2
    assert report.boot.current_admission == report.boot.prior_admission == 1
    if number in (1, 4, 5):
        assert released.value == 1 and report.retained == 0
        assert report.cleanup == report.secondary_cleanup == 0
        assert report.secondary == (
            errno.ESTALE if number == 4 else errno.EIO if number == 5 else 0
        )
    else:
        assert released.value == 0 and report.retained == 1
        assert (report.cleanup, report.secondary_cleanup) == (errno.EIO, errno.ENOSPC)
        assert report.secondary == (errno.EIO if number == 3 else errno.ENOSPC)


@pytest.mark.parametrize(
    "mode",
    [
        "class-throw",
        "application-null",
        "application-throw",
        "selected-null",
        "selected-throw",
        "selected-mismatch",
    ],
)
def test_jni_pair_refusals_and_local_frame_ownership(tmp_path, mode):
    """JNI table/framework callback double; actual JNI consumer, no authority positive."""
    jdk = Path(shutil.which("javac")).resolve().parents[1]
    (tmp_path / "closed_pins.h").write_text(
        '#include "installed_provenance.h"\nstatic const char *closed_package="fixture", *closed_signer="'
        + "a" * 64
        + '";\nstatic const struct installed_pin closed_pins[]={{"unused",""}};\n'
    )
    driver = tmp_path / "driver.c"
    driver.write_text(r"""
#include <assert.h>
#include <errno.h>
#include <jni.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
static const char *mode;
static int exception, pushes, pops, classes, callbacks;
static jlong diagnostic[34];
static jlongArray array(JNIEnv *e,jsize count) { (void)e; assert(count==34); return (jlongArray)(uintptr_t)123; }
static void region(JNIEnv *e,jlongArray a,jsize start,jsize count,const jlong *v) {
 (void)e; assert((uintptr_t)a==123 && start==0 && count==34); memcpy(diagnostic,v,sizeof(diagnostic));
}
static jint push(JNIEnv *e,jint n) { (void)e; assert(n==64); pushes++; return 0; }
static jobject pop(JNIEnv *e,jobject o) { (void)e; assert(!o); pops++; return NULL; }
static jboolean pending(JNIEnv *e) { (void)e; return exception; }
static void clear(JNIEnv *e) { (void)e; assert(exception); exception=0; }
static jclass find(JNIEnv *e,const char *name) {
    (void)e; assert(!exception); classes++;
    assert(!strncmp(name,"development/landmine/schemii/android/host/",strlen("development/landmine/schemii/android/host/")));
    if (!strcmp(mode,"class-throw")) { exception=1; return NULL; }
    return (jclass)(uintptr_t)classes;
}
static jmethodID method(JNIEnv *e,jclass c,const char *name,const char *signature) {
    (void)e; (void)c; assert(!exception);
    assert(!strcmp(signature,"()Landroid/content/Context;"));
    if (!strcmp(name,"frameworkOwnerApplication")) return (jmethodID)(uintptr_t)1;
    assert(!strcmp(name,"frameworkSelectedStorage")); return (jmethodID)(uintptr_t)2;
}
static jobject call(JNIEnv *e,jclass c,jmethodID id,...) {
    (void)e; (void)c; assert(!exception); callbacks++;
    if ((uintptr_t)id==1) {
        if (!strcmp(mode,"application-throw")) exception=1;
        return !strcmp(mode,"application-null") || exception ? NULL : (jobject)(uintptr_t)10;
    }
    assert((uintptr_t)id==2);
    if (!strcmp(mode,"selected-throw")) exception=1;
    return !strcmp(mode,"selected-null") || exception ? NULL : (jobject)(uintptr_t)11;
}
static jboolean equal(JNIEnv *e,jobject a,jobject b) { (void)e; assert(!exception); return a==b; }
extern jint Java_development_landmine_schemii_android_host_NativeOwner_open(JNIEnv *,jclass);
extern jint Java_development_landmine_schemii_android_host_NativeOwner_close(JNIEnv *,jclass);
extern jlongArray Java_development_landmine_schemii_android_host_NativeOwner_observation(JNIEnv *,jclass);
int main(int argc,char **argv) {
    assert(argc==2); mode=argv[1];
    const struct JNINativeInterface_ table={.PushLocalFrame=push,.PopLocalFrame=pop,
      .ExceptionCheck=pending,.ExceptionClear=clear,.FindClass=find,.GetStaticMethodID=method,
      .CallStaticObjectMethod=call,.IsSameObject=equal,.NewLongArray=array,.SetLongArrayRegion=region};
    JNIEnv env=&table;
    Java_development_landmine_schemii_android_host_NativeOwner_observation(&env,NULL);
    assert(diagnostic[21]==0 && diagnostic[22]==2 && diagnostic[24]==-1 && diagnostic[28]==1 && diagnostic[29]==1 && diagnostic[31]==-1);
    assert(Java_development_landmine_schemii_android_host_NativeOwner_open(&env,(jclass)(uintptr_t)1)==EACCES);
    assert(pushes==1 && pops==1 && !exception);
    assert(callbacks==(!strcmp(mode,"class-throw") ? 0 : !strncmp(mode,"application-",12) ? 1 : 2));
    Java_development_landmine_schemii_android_host_NativeOwner_observation(&env,NULL);
    int first=!strcmp(mode,"class-throw") ? 3 : !strncmp(mode,"application-",12) ? 4 : 5;
    assert(diagnostic[0]==1 && diagnostic[1]==34 && diagnostic[2]==first && diagnostic[14]==EACCES && diagnostic[21]==2 && diagnostic[33]==1);
    assert(Java_development_landmine_schemii_android_host_NativeOwner_close(&env,NULL)==0);
    Java_development_landmine_schemii_android_host_NativeOwner_observation(&env,NULL);
    assert(diagnostic[2]==first && diagnostic[4]==24 && diagnostic[14]==EACCES && diagnostic[21]==4);
    puts("PASS fixed pair refusal; locals disposed; no global/attachment/import");
}
""")
    executable = tmp_path / "control"
    command = [
        "cc",
        "-std=c11",
        "-Wall",
        "-Wextra",
        "-Werror",
        "-pthread",
        "-DSCHEMII_CLOSED_PINS",
        "-I",
        str(tmp_path),
        "-I",
        str(HOST),
        "-I",
        str(jdk / "include"),
        "-I",
        str(jdk / "include/linux"),
        str(driver),
        *[
            str(HOST / (name + ".c"))
            for name in (
                "owner_jni",
                "native_bootstrap",
                "installed_provenance",
                "boot_authority",
                "boot_compare",
                "boot_identity",
            )
        ],
        "-ldl",
        "-o",
        str(executable),
    ]
    subprocess.run(command, check=True, capture_output=True)
    result = subprocess.run([str(executable), mode], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


class InstalledObservation(ctypes.Structure):
    _fields_ = [
        (name, ctypes.c_int)
        for name in ("error", "kind", "hop", "operation", "syscall_error")
    ]


@pytest.mark.parametrize("kind", [1, 2, 3])
def test_object_observation_records_actual_missing_hop(installed, tmp_path, kind):
    data, control, apk, module, pins = specimen(tmp_path, "valid")
    target = {1: apk, 2: module, 3: control}[kind]
    if kind == 3:
        target.rmdir()
    else:
        target.unlink()
    out, errors = ctypes.c_void_p(), Errors()
    installed.installed_observe.argtypes = [ctypes.c_void_p]
    installed.installed_observe.restype = InstalledObservation
    assert (
        installed.installed_open(
            bytes(apk),
            bytes(module),
            bytes(data),
            ctypes.byref(out),
            ctypes.byref(errors),
        )
        == errno.ENOENT
    )
    observed = installed.installed_observe(out)
    assert (
        observed.error,
        observed.kind,
        observed.operation,
        observed.syscall_error,
    ) == (errno.ENOENT, kind, 1, errno.ENOENT)
    assert observed.hop == len(target.parts) - 1
    saved = bytes(observed)
    assert installed.installed_close(ctypes.byref(out), ctypes.byref(errors)) == 0
    assert bytes(observed) == saved  # value snapshot survives disposal


def test_object_mode_refusal_is_not_a_syscall_errno(installed, tmp_path):
    data, control, apk, module, pins = specimen(tmp_path, "valid")
    control.chmod(0o750)
    out, errors = ctypes.c_void_p(), Errors()
    installed.installed_observe.argtypes = [ctypes.c_void_p]
    installed.installed_observe.restype = InstalledObservation
    assert (
        installed.installed_open(
            bytes(apk),
            bytes(module),
            bytes(data),
            ctypes.byref(out),
            ctypes.byref(errors),
        )
        == errno.EACCES
    )
    observed = installed.installed_observe(out)
    assert (
        observed.error,
        observed.kind,
        observed.hop,
        observed.operation,
        observed.syscall_error,
    ) == (errno.EACCES, 3, 0, 5, 0)
    assert installed.installed_close(ctypes.byref(out), ctypes.byref(errors)) == 0


def test_object_allocation_errno_is_not_reported_as_syscall(installed, tmp_path):
    data, _, apk, module, _ = specimen(tmp_path, "valid")
    out, errors = ctypes.c_void_p(), Errors()
    installed.installed_observe.argtypes = [ctypes.c_void_p]
    installed.installed_observe.restype = InstalledObservation
    ctypes.c_int.in_dll(installed, "fail_strdup").value = 3  # first held-path copy
    assert (
        installed.installed_open(
            bytes(apk),
            bytes(module),
            bytes(data),
            ctypes.byref(out),
            ctypes.byref(errors),
        )
        == errno.ENOMEM
    )
    observed = installed.installed_observe(out)
    assert (
        observed.error,
        observed.kind,
        observed.operation,
        observed.syscall_error,
    ) == (errno.ENOMEM, 1, 1, 0)
    assert installed.installed_close(ctypes.byref(out), ctypes.byref(errors)) == 0


def test_preclose_observation_preserves_two_equal_cleanup_errors(installed, tmp_path):
    data, _, apk, module, pins = specimen(tmp_path, "valid")
    installed.native_bootstrap_open.argtypes = [ctypes.c_char_p] * 3 + [
        ctypes.POINTER(Pin),
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    installed.native_bootstrap_observe.argtypes = [ctypes.c_void_p]
    installed.native_bootstrap_observe.restype = AcquisitionReport
    installed.native_bootstrap_status.argtypes = [ctypes.c_void_p]
    installed.native_bootstrap_status.restype = BootstrapStatus
    installed.native_bootstrap_close_observed.argtypes = [
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(BootstrapStatus),
        ctypes.POINTER(AcquisitionReport),
    ]
    ctypes.c_int.in_dll(installed, "fail_close").value = 2
    owner = ctypes.c_void_p()
    assert (
        installed.native_bootstrap_open(
            bytes(apk), bytes(module), bytes(data), pins, len(pins), ctypes.byref(owner)
        )
        == errno.EIO
    )
    before = installed.native_bootstrap_observe(owner)
    assert (before.first_stage, before.cleanup, before.secondary_cleanup) == (
        12,
        errno.EIO,
        errno.EIO,
    )
    # New observation must not silently redefine raw16's original ABI/semantics.
    assert installed.native_bootstrap_status(owner).cleanup == 0
    final, observed = BootstrapStatus(), AcquisitionReport()
    assert (
        installed.native_bootstrap_close_observed(
            ctypes.byref(owner), ctypes.byref(final), ctypes.byref(observed)
        )
        == errno.EIO
    )
    assert (
        observed.first_stage,
        observed.last_stage,
        observed.cleanup,
        observed.secondary_cleanup,
    ) == (12, 24, errno.EIO, errno.EIO)
    assert (final.cleanup, final.secondary_cleanup, final.retained) == (errno.EIO, 0, 1)
