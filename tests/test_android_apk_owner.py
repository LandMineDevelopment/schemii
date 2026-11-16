"""Actual native control and production Python refusal, no device/runtime lease."""

from __future__ import annotations

import ctypes
import os
from pathlib import Path
import socket
import struct
import subprocess
import threading
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOST = ROOT / "platforms/android/native/host"


class Status(ctypes.Structure):
    _fields_ = [(k, ctypes.c_uint64) for k in ("sequence", "monotonic_ns")] + [
        (k, ctypes.c_int32)
        for k in (
            "phase",
            "reason",
            "error",
            "cleanup_error",
            "journal_status",
            "journal_effect",
            "boot_relation",
            "boot_reason",
            "births",
            "closure_known",
        )
    ]


@pytest.fixture
def native(tmp_path):
    # Actual production owner and comparator objects. Observation wrapper is a
    # finite syscall-pending control, never a trust constructor or boot donor.
    wrapper = tmp_path / "reader.c"
    wrapper.write_text("""#include "boot_identity.h"
#include <string.h>
#include <unistd.h>
#include <stdatomic.h>
static atomic_int delay;
void delay_read(int value) { atomic_store(&delay,value); }
struct boot_identity_result boot_identity_read(void) {
 while (atomic_load(&delay)) usleep(1000);
 struct boot_identity_result r; memset(&r,0,sizeof(r));
 r.status=BOOT_ID_ORIGIN_UNKNOWN; return r;
}
""")
    library = tmp_path / "owner.so"
    subprocess.run(
        [
            "cc",
            "-shared",
            "-fPIC",
            "-std=c11",
            "-D_DEFAULT_SOURCE",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-pedantic",
            "-pthread",
            "-I",
            str(HOST),
            str(HOST / "owner.c"),
            str(HOST / "boot_compare.c"),
            str(wrapper),
            "-o",
            str(library),
        ],
        check=True,
    )
    lib = ctypes.CDLL(str(library))
    lib.apk_owner_open.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)]
    lib.apk_owner_status.argtypes = [ctypes.c_void_p]
    lib.apk_owner_status.restype = Status
    lib.apk_owner_request.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    lib.apk_owner_close.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
    return lib, library


def opened(lib, path):
    path.mkdir(mode=0o700)
    fd = os.open(path, os.O_DIRECTORY | os.O_RDONLY | os.O_CLOEXEC)
    owner = ctypes.c_void_p()
    assert lib.apk_owner_open(fd, ctypes.byref(owner)) == 0
    os.close(fd)  # borrowed descriptor does not own the retained lease
    return owner


def settled(lib, owner):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        state = lib.apk_owner_status(owner)
        if state.phase != 1:
            return state
        time.sleep(0.001)
    pytest.fail("Owner remained pending")


def test_real_owner_retains_lease_and_never_promotes_observation(native, tmp_path):
    lib, library = native
    owner = opened(lib, tmp_path / "control")
    fd = os.open(tmp_path / "control", os.O_DIRECTORY | os.O_RDONLY)
    peer = ctypes.c_void_p()
    assert lib.apk_owner_open(fd, ctypes.byref(peer)) != 0
    assert not peer.value
    os.close(fd)
    assert lib.apk_owner_request(owner, 1) == 0
    state = settled(lib, owner)
    assert state.phase == 2 and state.reason == 1
    assert state.boot_relation == 2 and state.births == 0 and state.closure_known == 0
    assert state.journal_effect == 0  # no fsync/ACK/grant fabricated
    assert list((tmp_path / "control").iterdir()) == []
    assert lib.apk_owner_close(ctypes.byref(owner)) == 0 and not owner.value
    # Complete production owner object has no fork/exec/sweep/adoption path.
    symbols = subprocess.check_output(["nm", "-D", str(library)], text=True)
    for symbol in (" fork", " exec", " kill", " waitpid"):
        assert symbol not in symbols


def test_pending_observer_keeps_status_and_stop_responsive(native, tmp_path):
    lib, _ = native
    owner = opened(lib, tmp_path / "control")
    lib.delay_read(1)
    assert lib.apk_owner_request(owner, 1) == 0
    before = time.monotonic()
    assert lib.apk_owner_status(owner).phase == 1
    assert lib.apk_owner_request(owner, 2) != 0  # cancellation latched, no replacement
    assert lib.apk_owner_close(ctypes.byref(owner)) != 0
    assert time.monotonic() - before < 0.5
    lib.delay_read(0)
    assert settled(lib, owner).births == 0
    assert lib.apk_owner_close(ctypes.byref(owner)) == 0


def test_directory_refusals(native, tmp_path):
    lib, _ = native
    for path in [tmp_path, tmp_path / "regular"]:
        if path.name == "regular":
            path.touch()
        else:
            path.chmod(0o755)
        fd = os.open(path, os.O_RDONLY)
        owner = ctypes.c_void_p()
        assert lib.apk_owner_open(fd, ctypes.byref(owner)) != 0
        assert not owner.value
        os.close(fd)


@pytest.mark.parametrize(
    "reply",
    [
        b"",
        b"x" * 17,
        struct.pack("!4sIII", b"SCH1", 1, 2, 0),
        struct.pack("!4sIII", b"SCH1", 1, 1, 0),
    ],
)
def test_python_channel_cannot_turn_peer_text_into_authority(reply):
    parent, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)

    def peer():
        try:
            assert len(parent.recv(32)) == 16
            parent.send(reply)
        finally:
            parent.close()

    thread = threading.Thread(target=peer)
    thread.start()
    code = """import os,sys
os.dup2(int(sys.argv[1]),3)
sys.path.insert(0,sys.argv[2])
from apk_owner import require_launch
try: require_launch()
except (OSError,ValueError): sys.exit(0)
sys.exit(5)
"""
    result = subprocess.run(
        [os.sys.executable, "-c", code, str(child.fileno()), str(ROOT / "dev/android")],
        pass_fds=(child.fileno(),),
        timeout=5,
    )
    child.close()
    thread.join(3)
    assert result.returncode == 0
