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
    ],
)
def test_actor_wire_and_direct_child(actor_control, mode):
    subprocess.run([str(actor_control), mode], check=True, timeout=10)
