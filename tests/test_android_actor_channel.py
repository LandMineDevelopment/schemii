"""Finite private wire/FD producer controls; never an APK admission proof."""

from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOST = ROOT / "platforms/android/native/host"


@pytest.fixture(scope="module")
def actor_control(tmp_path_factory):
    out = tmp_path_factory.mktemp("actor-control") / "control"
    subprocess.run(
        [
            "cc",
            "-std=c11",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-pedantic",
            "-I",
            str(HOST),
            str(HOST / "actor_channel.c"),
            str(ROOT / "platforms/android/native/tests/actor_control.c"),
            "-Wl,--wrap=fork",
            "-Wl,--wrap=send",
            "-Wl,--wrap=close",
            "-Wl,--wrap=waitpid",
            "-o",
            str(out),
        ],
        check=True,
    )
    return out


@pytest.mark.parametrize(
    "mode",
    [
        "valid",
        "short",
        "truncated",
        "version",
        "reserved",
        "source",
        "creator",
        "duplicate",
        "gap",
        "wrap",
        "tx-wrap",
        "rights",
        "peer-loss",
        "pressure",
        "native",
        "loss-before-export",
        "fork-failure",
        "birth-before-export-loss",
        "policy",
        "before-birth-loss",
        "close-errors",
        "slot-saturation",
        "early-saturation",
        "incarnation-wrap",
        "wait-1",
        "wait-2",
        "wait-3",
        "wait-4",
        "wait-5",
    ],
)
def test_actor_wire_and_direct_child(actor_control, mode):
    subprocess.run([str(actor_control), mode], check=True, timeout=10)


@pytest.fixture(scope="module")
def owner_fault_control(tmp_path_factory):
    out = tmp_path_factory.mktemp("owner-faults") / "control"
    subprocess.run(
        [
            "cc",
            "-DAC_OWNER_FAULT",
            "-std=c11",
            "-pthread",
            "-Wall",
            "-Wextra",
            "-I",
            str(HOST),
            str(HOST / "owner.c"),
            str(HOST / "boot_authority.c"),
            str(HOST / "boot_compare.c"),
            str(HOST / "boot_identity.c"),
            str(ROOT / "platforms/android/native/tests/actor_control.c"),
            "-Wl,--wrap=openat",
            "-Wl,--wrap=socketpair",
            "-Wl,--wrap=pthread_create",
            "-Wl,--wrap=close",
            "-o",
            str(out),
        ],
        check=True,
    )
    return out


@pytest.mark.parametrize(
    "mode",
    ["second-pair", "pthread", "pthread-close-errors", "close-errors", "healthy"],
)
def test_owner_failure_close_ownership(owner_fault_control, mode, tmp_path):
    tmp_path.chmod(0o700)
    subprocess.run(
        [str(owner_fault_control), mode, str(tmp_path)], check=True, timeout=10
    )
