"""HOST production custody prerequisites; no positive Android C/A admission."""

from __future__ import annotations

import ctypes
import errno
import os
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOST = ROOT / "platforms/android/native/host"


class Result(ctypes.Structure):
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


class Errors(ctypes.Structure):
    _fields_ = [
        (name, ctypes.c_int)
        for name in (
            "error",
            "close_error",
            "secondary_close_error",
            "close_failures",
        )
    ]


@pytest.fixture(scope="module")
def native(tmp_path_factory):
    work = tmp_path_factory.mktemp("authority")
    library = work / "authority.so"
    subprocess.run(
        [
            "cc",
            "-std=c11",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-pedantic",
            "-shared",
            "-fPIC",
            "-Wl,-z,defs",
            *[
                str(HOST / f"{unit}.c")
                for unit in ("boot_authority", "boot_compare", "boot_identity")
            ],
            "-o",
            str(library),
        ],
        check=True,
        capture_output=True,
    )
    lib = ctypes.CDLL(str(library))
    lib.boot_authority_open.argtypes = [
        ctypes.c_int,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(Errors),
    ]
    lib.boot_authority_compare.argtypes = [ctypes.c_void_p]
    lib.boot_authority_compare.restype = Result
    lib.boot_authority_cancel.argtypes = [ctypes.c_void_p]
    lib.boot_authority_close.argtypes = [
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(Errors),
    ]
    return lib


def opened(lib, path):
    path.mkdir(mode=0o700)
    selected = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    handle, errors = ctypes.c_void_p(), Errors()
    assert (
        lib.boot_authority_open(selected, ctypes.byref(handle), ctypes.byref(errors))
        == 0
    )
    os.close(selected)
    return handle


def test_native_custody_never_relabels_r_as_c_or_creates_state(native, tmp_path):
    path = tmp_path / "selected"
    handle = opened(native, path)
    try:
        for _ in range(2):
            result = native.boot_authority_compare(handle)
            assert result.relation == 2 and result.reason in (3, 4)
            assert result.current_admission == result.prior_admission == 1
            assert result.continuity == 2
        fd = os.open(path, os.O_DIRECTORY | os.O_RDONLY)
        try:
            peer, errors = ctypes.c_void_p(), Errors()
            assert native.boot_authority_open(
                fd, ctypes.byref(peer), ctypes.byref(errors)
            ) in (errno.EAGAIN, errno.EWOULDBLOCK)
            assert not peer.value
        finally:
            os.close(fd)
        assert list(path.iterdir()) == []
    finally:
        assert native.boot_authority_close(ctypes.byref(handle), None) == 0
    assert not handle.value
    assert native.boot_authority_compare(handle).reason == 2


@pytest.mark.parametrize("loss", ["mode", "cancel", "fork"])
def test_real_lifetime_loss_cannot_be_restored(native, tmp_path, loss):
    path = tmp_path / "selected"
    handle = opened(native, path)
    try:
        if loss == "fork":
            pid = os.fork()
            if pid == 0:
                r = native.boot_authority_compare(handle)
                closed = native.boot_authority_close(ctypes.byref(handle), None)
                os._exit(
                    0
                    if r.relation == 2 and r.reason == 2 and closed == errno.ECHILD
                    else 5
                )
            _, status = os.waitpid(pid, 0)
            assert os.waitstatus_to_exitcode(status) == 0
            # Closing an inherited description must not unlock the parent.
            assert native.boot_authority_compare(handle).reason in (3, 4)
        else:
            if loss == "mode":
                path.chmod(0o755)
            else:
                native.boot_authority_cancel(handle)
            assert native.boot_authority_compare(handle).relation == 2
            path.chmod(0o700)
            assert native.boot_authority_compare(handle).reason == 2
        assert not list(path.iterdir())
    finally:
        assert native.boot_authority_close(ctypes.byref(handle), None) == (
            errno.EACCES if loss == "mode" else 0
        )


@pytest.mark.parametrize("mode", [0o755, 0o770, 0o1700])
def test_invalid_selected_storage_never_constructs(native, tmp_path, mode):
    path = tmp_path / "selected"
    path.mkdir(mode=0o700)
    path.chmod(mode)
    fd = os.open(path, os.O_DIRECTORY | os.O_RDONLY)
    handle, errors = ctypes.c_void_p(), Errors()
    try:
        assert (
            native.boot_authority_open(fd, ctypes.byref(handle), ctypes.byref(errors))
            == errno.EACCES
        )
        assert not handle.value and errors.close_failures == 0
    finally:
        os.close(fd)


@pytest.mark.parametrize("operation", ["lease", "traced", "first-error"])
def test_native_loss_and_traced_caller_controls(tmp_path, operation):
    # Including the unit exposes the private descriptor ONLY in this temporary
    # fault harness. Separate production-object controls above have no such seam.
    driver = tmp_path / "control.c"
    driver.write_text(r"""
#define _GNU_SOURCE
#include "boot_authority.c"
#include <sys/ptrace.h>
#include <sys/wait.h>
#include <signal.h>
static int inject_close;
int __real_close(int);
int __wrap_close(int fd) {
  int r=__real_close(fd);
  if (inject_close) { errno=EIO; return -1; }
  return r;
}
int main(int argc, char **argv) {
  if (argc!=3) return 2;
  int traced=!strcmp(argv[2],"traced");
  pid_t child=-1;
  if (traced) {
    child=fork(); if (child<0) return 3;
    if (child) {
      int status;
      if (waitpid(child,&status,0)!=child || !WIFSTOPPED(status)) return 4;
      if (ptrace(PTRACE_CONT,child,0,0)<0) return 5;
      if (waitpid(child,&status,0)!=child || !WIFEXITED(status)) return 6;
      return WEXITSTATUS(status);
    }
    if (ptrace(PTRACE_TRACEME,0,0,0)<0) return 7;
    if (raise(SIGSTOP)) return 8;
  }
  int fd=open(argv[1],O_RDONLY|O_DIRECTORY|O_CLOEXEC);
  struct boot_authority *o=NULL;
  struct boot_authority_errors errors;
  if (fd<0 || boot_authority_open(fd,&o,&errors) || __real_close(fd)) return 9;
  if (!strcmp(argv[2],"lease")) {
    if (flock(o->dir,LOCK_UN)) return 10; /* explicit injected private lease loss */
  } else if (!strcmp(argv[2],"first-error")) {
    if (fchmod(o->dir,0755)) return 11;
  }
  struct boot_compare_result r=boot_authority_compare(o);
  if (r.relation!=BOOT_COMPARE_UNKNOWN || r.current_admission!=BOOT_COMPARE_UNPROVED) return 12;
  if (traced) {
    if (r.reason!=BOOT_COMPARE_CALLER_UNPROVED && r.reason!=BOOT_COMPARE_OBSERVATION_UNKNOWN) return 13;
  } else if (r.reason!=BOOT_COMPARE_CE_LOCKED_OR_STORAGE_UNAVAILABLE) return 14;
  if (!strcmp(argv[2],"first-error")) inject_close=1;
  int closed=boot_authority_close(&o,&errors);
  if (o) return 15;
  if (!strcmp(argv[2],"first-error")) {
    if (closed!=EACCES || errors.error!=EACCES || errors.close_error!=EIO || errors.close_failures!=1) return 16;
  } else if (!strcmp(argv[2],"lease")) {
    if (closed!=ESTALE) return 17;
  } else if (closed) return 18;
  return 0;
}
""")
    binary = tmp_path / "control"
    subprocess.run(
        [
            "cc",
            "-std=c11",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-pedantic",
            "-I",
            str(HOST),
            str(driver),
            str(HOST / "boot_compare.c"),
            str(HOST / "boot_identity.c"),
            "-Wl,--wrap=close",
            "-o",
            str(binary),
        ],
        check=True,
        capture_output=True,
    )
    selected = tmp_path / "selected"
    selected.mkdir(mode=0o700)
    subprocess.run([str(binary), str(selected), operation], check=True, timeout=5)
    assert not list(selected.iterdir())
