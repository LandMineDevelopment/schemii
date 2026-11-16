"""P1a host/synthetic controls only; no Android, new-boot or recovery proof.

Link wrappers control the actual production syscalls, not a duplicate reader.
Synthetic origin metadata is explicitly fabricated; real mode delegates to the
host kernel except for the selected fault. Neither mode exports a real UUID.
"""

from __future__ import annotations

import ctypes
import errno
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "platforms/android/native/host"
ARTIFACTS = ROOT / "artifacts/android-apk/host/boot-identity-p1a"
UUID = b"12345678-1234-4abc-8def-123456789abc"
STATUS = dict(
    zip(
        (
            "ok open_failed origin_failed origin_unknown origin_rejected "
            "read_failed empty short malformed oversized close_failed inconsistent"
        ).split(),
        range(12),
    )
)
STAGE = dict(
    zip("none open fstatfs fstat statx read parse close compare".split(), range(9))
)

HARNESS = r"""
#define _GNU_SOURCE
#include "boot_identity.h"
#include <errno.h>
#include <fcntl.h>
#include <linux/magic.h>
#include <linux/stat.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/vfs.h>
#include <unistd.h>

static int real_mode, target, injected_errno, bad_control;
static int opened, closed, open_calls, fs_calls, stat_calls, mount_calls;
static int read_calls, close_calls, current_sample, offset;
static const char *fault, *shape;
static int live[32], live_count;
static const char *const paths[] = {"/proc", "sys", "kernel", "random", "boot_id"};

int __real_openat(int, const char *, int, ...);
int __real_fstatfs(int, struct statfs *);
int __real_fstat(int, struct stat *);
long __real_syscall(long, ...);
ssize_t __real_read(int, void *, size_t);
int __real_close(int);

static int matches(const char *name, int call) {
  return !strcmp(fault, name) && target == call;
}
static int injected(const char *name, int call) {
  if (!matches(name, call)) return 0;
  errno = injected_errno;
  return 1;
}
int __wrap_openat(int parent, const char *path, int flags, ...) {
  int index = open_calls++ % 5;
  int expected = O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK;
  if (index < 4) expected |= O_DIRECTORY;
  if (strcmp(path, paths[index]) || flags != expected ||
      (index == 0 ? parent != AT_FDCWD :
       live_count != 1 || parent != live[0])) bad_control = 1;
  if (injected("open", open_calls)) return -1;
  int fd = real_mode ? __real_openat(parent, path, flags) : 1000 + open_calls;
  if (fd >= 0) {
    ++opened;
    live[live_count++] = fd;
    if (index == 4) { ++current_sample; offset = 0; }
  }
  return fd;
}
int __wrap_fstatfs(int fd, struct statfs *out) {
  ++fs_calls;
  if (injected("fstatfs", fs_calls)) return -1;
  int rc = 0;
  if (real_mode) rc = __real_fstatfs(fd, out);
  else { memset(out, 0, sizeof(*out)); out->f_type = PROC_SUPER_MAGIC; }
  if (matches("foreign-fs", fs_calls)) out->f_type = 0x01021994;
  return rc;
}
int __wrap_fstat(int fd, struct stat *out) {
  ++stat_calls;
  if (injected("fstat", stat_calls)) return -1;
  int rc = 0;
  if (real_mode) rc = __real_fstat(fd, out);
  else {
    memset(out, 0, sizeof(*out));
    out->st_mode = open_calls % 5 ? S_IFDIR | 0500 : S_IFREG | 0400;
  }
  if (matches("wrong-type", stat_calls)) out->st_mode = S_IFLNK | 0700;
  return rc;
}
long __wrap_syscall(long number, ...) {
  va_list ap;
  va_start(ap, number);
  int fd = va_arg(ap, int);
  const char *path = va_arg(ap, const char *);
  int flags = va_arg(ap, int);
  unsigned mask = va_arg(ap, unsigned);
  struct statx *out = va_arg(ap, struct statx *);
  va_end(ap);
  ++mount_calls;
  if (number != SYS_statx || *path ||
      flags != (AT_EMPTY_PATH | AT_SYMLINK_NOFOLLOW) || mask != STATX_MNT_ID)
    bad_control = 1;
  if (injected("statx", mount_calls)) return -1;
  long rc = 0;
  if (real_mode) rc = __real_syscall(number, fd, path, flags, mask, out);
  else {
    memset(out, 0, sizeof(*out));
    out->stx_mask = STATX_MNT_ID;
    out->stx_mnt_id = !strcmp(shape, "mount-change") && open_calls > 5 ? 200 : 100;
  }
  if (matches("unknown-mount", mount_calls)) out->stx_mask = 0;
  if (matches("other-mount", mount_calls)) ++out->stx_mnt_id;
  return rc;
}
ssize_t __wrap_read(int fd, void *out, size_t size) {
  ++read_calls;
  if (size > BOOT_IDENTITY_READ_MAX || read_calls > 76 || !size) bad_control = 1;
  if (injected("read", read_calls)) return -1;
  if (real_mode) return __real_read(fd, out, size);
  const char *text = "12345678-1234-4abc-8def-123456789abc\n";
  if (!strcmp(shape, "mismatch") && current_sample == 2)
    text = "22345678-1234-4abc-8def-123456789abc\n";
  if (!strcmp(shape, "empty")) text = "";
  if (!strcmp(shape, "short")) text = "1234\n";
  if (!strcmp(shape, "malformed")) text = "12345678-1234-4abc-8def-123456789abX\n";
  if (!strcmp(shape, "second-empty") && current_sample == 2) text = "";
  if (!strcmp(shape, "oversized")) {
    memset(out, 'a', size);
    return (ssize_t)size; /* Endless source: reader MUST stop at byte bound. */
  }
  size_t length = strlen(text);
  if (!strcmp(shape, "no-lf")) --length;
  size_t n = length - (size_t)offset;
  if (n > size) n = size;
  if (!strcmp(shape, "one-byte") && n > 1) n = 1;
  memcpy(out, text + offset, n);
  offset += (int)n;
  return (ssize_t)n;
}
int __wrap_close(int fd) {
  ++close_calls;
  int found = -1;
  for (int i = 0; i < live_count; ++i) if (live[i] == fd) found = i;
  if (found < 0) { bad_control = 1; return -1; }
  live[found] = live[--live_count];
  ++closed;
  int rc = real_mode ? __real_close(fd) : 0;
  /* Linux close may release the FD even when reporting an error. */
  if (injected("close", close_calls) ||
      (!strcmp(shape, "cleanup-error") && close_calls == 5)) {
    errno = injected_errno;
    return -1;
  }
  return rc;
}
int main(int argc, char **argv) {
  if (argc != 7) return 2;
  real_mode = !strcmp(argv[1], "real");
  fault = argv[2]; target = atoi(argv[3]); injected_errno = atoi(argv[4]);
  shape = argv[5];
  if (strcmp(argv[6], "private-control")) return 2;
  struct boot_identity_result r = boot_identity_read();
  int zero = 1;
  for (unsigned i = 0; i < sizeof(r.uuid); ++i) if (r.uuid[i]) zero = 0;
  printf("{\"known\":%d,\"status\":%d,\"stage\":%d,\"errno\":%d,"
         "\"cleanup_errno\":%d,\"failed_sample\":%u,\"failed_node\":%u,"
         "\"method\":%d,\"context\":%d,\"untraced_verified\":%d,"
         "\"uuid_zero\":%d,\"synthetic\":%d,\"opened\":%d,\"closed\":%d,"
         "\"open_calls\":%d,\"read_calls\":%d,\"samples\":[",
         r.knownness, r.status, r.stage, r.error, r.cleanup_error,
         r.failed_sample, r.failed_node, r.method, r.context,
         r.untraced_owner_verified, zero, !real_mode, opened, closed,
         open_calls, read_calls);
  for (int i = 0; i < 2; ++i) {
    const struct boot_identity_sample *s = &r.samples[i];
    printf("%s{\"origin\":%d,\"opened\":%u,\"procfs\":%u,\"type\":%u,"
           "\"mount\":%u,\"read_calls\":%u,\"bytes\":%zu}", i ? "," : "",
           s->origin, s->opened_nodes, s->procfs_checks, s->type_checks,
           s->mount_checks, s->read_calls, s->bytes_read);
  }
  puts("]}");
  return bad_control || live_count || opened != closed ? 3 : 0;
}
"""


@pytest.fixture(scope="module")
def native():
    compiler = shutil.which("cc")
    assert compiler, "Strict C11 host compiler required"
    ARTIFACTS.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="controls-", dir=ARTIFACTS) as temp:
        directory = Path(temp)
        harness = directory / "control.c"
        harness.write_text(HARNESS)
        harness.chmod(0o600)
        common = [
            compiler,
            "-std=c11",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-Wpedantic",
            "-I",
            str(SOURCE),
            str(SOURCE / "boot_identity.c"),
        ]
        library = directory / "libboot-identity.so"
        executable = directory / "boot-identity-control"
        commands = [
            [*common, "-fPIC", "-shared", "-o", str(library)],
            [
                *common,
                str(harness),
                *[
                    f"-Wl,--wrap={name}"
                    for name in (
                        "openat",
                        "fstatfs",
                        "fstat",
                        "syscall",
                        "read",
                        "close",
                    )
                ],
                "-o",
                str(executable),
            ],
        ]
        for command in commands:
            run = subprocess.run(command, capture_output=True, text=True, timeout=30)
            assert run.returncode == 0, run.stderr
        lib = ctypes.CDLL(str(library))
        lib.boot_identity_parse.argtypes = [
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_void_p,
        ]
        lib.boot_identity_parse.restype = ctypes.c_int
        yield lib, executable


def parse(native, text, length=None):
    out = (ctypes.c_ubyte * 16)(*[255] * 16)
    status = native[0].boot_identity_parse(
        text, len(text) if length is None else length, out
    )
    return status, bytes(out)


def invoke(native, *, mode="synthetic", fault="none", at=0, error=0, shape="valid"):
    command = [
        str(native[1]),
        mode,
        fault,
        str(at),
        str(error),
        shape,
        "private-control",
    ]
    run = subprocess.run(command, capture_output=True, text=True, timeout=3)
    assert run.returncode == 0, (run.returncode, run.stderr)
    assert run.stderr == ""
    assert len(run.stdout) < 2048
    report = json.loads(run.stdout)
    assert report["synthetic"] == (mode == "synthetic")
    assert report["opened"] == report["closed"]
    assert report["read_calls"] <= 76
    assert report["method"] == 1
    assert report["context"] == 1
    assert report["untraced_verified"] == 0
    assert report["uuid_zero"] == (not report["known"])
    return report


def refused(report, status, stage, error=0):
    assert report["known"] == 0
    assert report["status"] == STATUS[status]
    assert report["stage"] == STAGE[stage]
    assert report["errno"] == error


@pytest.mark.parametrize("suffix", [b"", b"\n"])
def test_strict_parser_accepts_only_kernel_uuid_form(native, suffix):
    status, value = parse(native, UUID + suffix)
    assert status == STATUS["ok"]
    assert value == bytes.fromhex(UUID.decode().replace("-", ""))


@pytest.mark.parametrize(
    ("text", "status"),
    [
        (b"", "empty"),
        (b"\n", "short"),
        (UUID[:35], "short"),
        (UUID + b"\n\n", "oversized"),
        (UUID + b"\r\n", "oversized"),
        (UUID * 1000, "oversized"),
        (UUID + b" ", "malformed"),
        (UUID + b"\x00", "malformed"),
        (UUID.upper(), "malformed"),
        (b"0" * 36, "malformed"),
        (b"00000000-0000-0000-0000-000000000000", "malformed"),
        (UUID[:14] + b"1" + UUID[15:], "malformed"),
        (UUID[:19] + b"c" + UUID[20:], "malformed"),
    ],
)
def test_parser_refusals_clear_private_output(native, text, status):
    actual, value = parse(native, text)
    assert actual == STATUS[status]
    assert value == bytes(16)


@pytest.mark.parametrize("position", range(36))
def test_parser_rejects_every_corrupted_position(native, position):
    text = UUID[:position] + b"\x00" + UUID[position + 1 :]
    status, value = parse(native, text)
    assert status == STATUS["malformed"]
    assert value == bytes(16)


def test_parser_bounds_before_access(native):
    # NULL is safe at an oversized length: no scan, strlen or allocation.
    for length, status in [
        (0, "empty"),
        (35, "short"),
        (36, "malformed"),
        (999, "oversized"),
    ]:
        actual, value = parse(native, None, length)
        assert actual == STATUS[status]
        assert value == bytes(16)


@pytest.mark.parametrize("shape", ["valid", "no-lf", "one-byte"])
def test_two_bounded_independent_synthetic_reads(native, shape):
    report = invoke(native, shape=shape)
    assert report["known"] == 1
    assert report["status"] == STATUS["ok"]
    assert report["stage"] == STAGE["none"]
    assert report["errno"] == report["cleanup_errno"] == 0
    assert report["failed_sample"] == report["failed_node"] == 0
    assert report["open_calls"] == 10
    for sample in report["samples"]:
        assert sample["origin"] == 1
        assert (
            sample["opened"]
            == sample["procfs"]
            == sample["type"]
            == sample["mount"]
            == 5
        )
        assert sample["bytes"] == (36 if shape == "no-lf" else 37)
        assert sample["read_calls"] == (38 if shape == "one-byte" else 2)


@pytest.mark.parametrize("at", range(1, 11))
@pytest.mark.parametrize(
    "error", [errno.EACCES, errno.ENOENT, errno.EINTR, errno.ELOOP]
)
def test_failed_open_retains_exact_errno_without_retry(native, at, error):
    report = invoke(native, fault="open", at=at, error=error)
    refused(report, "open_failed", "open", error)
    assert report["failed_sample"] == (at - 1) // 5 + 1
    assert report["failed_node"] == (at - 1) % 5 + 1
    assert report["open_calls"] == at


@pytest.mark.parametrize("at", range(1, 11))
@pytest.mark.parametrize("fault", ["fstatfs", "fstat", "statx"])
def test_failed_origin_syscall_refuses_and_retains_stage(native, at, fault):
    report = invoke(native, fault=fault, at=at, error=errno.EPERM)
    refused(report, "origin_failed", fault, errno.EPERM)
    assert report["failed_sample"] == (at - 1) // 5 + 1
    assert report["failed_node"] == (at - 1) % 5 + 1
    assert report["samples"][report["failed_sample"] - 1]["origin"] == 0


@pytest.mark.parametrize("at", range(1, 11))
@pytest.mark.parametrize(
    ("fault", "status", "stage"),
    [
        ("foreign-fs", "origin_rejected", "fstatfs"),
        ("wrong-type", "origin_rejected", "fstat"),
        ("unknown-mount", "origin_unknown", "statx"),
        ("other-mount", "origin_rejected", "statx"),
    ],
)
def test_unknown_or_wrong_origin_never_becomes_known(native, at, fault, status, stage):
    # A changed root alone is rejected at the next node in that walk.
    report = invoke(native, fault=fault, at=at)
    refused(report, status, stage)
    assert report["samples"][report["failed_sample"] - 1]["origin"] == (
        0 if fault == "unknown-mount" else 2
    )


@pytest.mark.parametrize("at", [1, 2, 3, 4])
@pytest.mark.parametrize("error", [errno.EACCES, errno.EIO, errno.EINTR, errno.EAGAIN])
def test_read_and_eof_probe_failures_are_not_retried(native, at, error):
    report = invoke(native, fault="read", at=at, error=error)
    refused(report, "read_failed", "read", error)
    assert report["read_calls"] == at
    assert report["failed_sample"] == (at - 1) // 2 + 1


@pytest.mark.parametrize(
    ("shape", "status"),
    [
        ("empty", "empty"),
        ("short", "short"),
        ("malformed", "malformed"),
        ("oversized", "oversized"),
    ],
)
def test_reader_applies_parser_and_stops_at_byte_bound(native, shape, status):
    report = invoke(native, shape=shape)
    refused(report, status, "parse")
    assert report["failed_sample"] == 1
    assert report["samples"][0]["bytes"] <= 38
    assert report["open_calls"] == 5
    if shape == "oversized":
        assert report["read_calls"] == 1
        assert report["samples"][0]["bytes"] == 38


@pytest.mark.parametrize("shape", ["mismatch", "mount-change"])
def test_inconsistent_uuid_or_mount_context_refuses(native, shape):
    report = invoke(native, shape=shape)
    refused(report, "inconsistent", "compare")
    assert report["failed_sample"] == 2
    assert report["failed_node"] == 0
    assert [sample["origin"] for sample in report["samples"]] == [1, 1]


def test_second_sample_must_parse_independently(native):
    report = invoke(native, shape="second-empty")
    refused(report, "empty", "parse")
    assert report["failed_sample"] == 2


@pytest.mark.parametrize("at", range(1, 11))
def test_close_failure_refuses_without_retry(native, at):
    report = invoke(native, fault="close", at=at, error=errno.EINTR)
    refused(report, "close_failed", "close", errno.EINTR)
    assert report["failed_sample"] == (at - 1) // 5 + 1
    assert report["failed_node"] == (at - 1) % 5 + 1


def test_cleanup_error_does_not_hide_original_failure(native):
    report = invoke(native, fault="read", at=1, error=errno.EIO, shape="cleanup-error")
    refused(report, "read_failed", "read", errno.EIO)
    assert report["cleanup_errno"] == errno.EIO
    assert report["read_calls"] == 1
    assert report["failed_sample"] == 1
    assert report["failed_node"] == 5


def test_real_fixed_proc_reader_without_exporting_identity(native):
    report = invoke(native, mode="real")
    # Host capability is deliberately asserted, never converted to a fake pass.
    assert report["known"] == 1, report
    assert report["status"] == STATUS["ok"]
    assert all(s["origin"] == 1 and s["bytes"] == 37 for s in report["samples"])


@pytest.mark.parametrize(
    ("fault", "at", "error", "status", "stage"),
    [
        ("open", 1, errno.EACCES, "open_failed", "open"),
        ("open", 5, errno.EPERM, "open_failed", "open"),
        ("read", 1, errno.EACCES, "read_failed", "read"),
        ("read", 2, errno.EIO, "read_failed", "read"),
        ("fstatfs", 5, errno.EIO, "origin_failed", "fstatfs"),
        ("statx", 1, errno.ENOSYS, "origin_failed", "statx"),
        ("unknown-mount", 1, 0, "origin_unknown", "statx"),
        ("other-mount", 5, 0, "origin_rejected", "statx"),
    ],
)
def test_real_reader_fault_controls(native, fault, at, error, status, stage):
    report = invoke(native, mode="real", fault=fault, at=at, error=error)
    refused(report, status, stage, error)
