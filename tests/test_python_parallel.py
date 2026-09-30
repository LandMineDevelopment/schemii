"""Tiny real pytest processes protect partition identity and lifecycle ownership."""

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

from scripts.ci.summary import load, summarize


REPOSITORY = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "python_parallel", REPOSITORY / "scripts/ci/python-tests.py"
)
assert SPEC is not None and SPEC.loader is not None
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)
META = {
    "schema": 1,
    "source_sha": "a" * 40,
    "run_id": 12,
    "run_attempt": 1,
    "lane": "python",
    "project": "none",
    "shard": 0,
}


@pytest.fixture
def tiny(tmp_path):
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / "tests").mkdir()
    (repository / "testing").mkdir()
    (repository / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["tests", "testing"]\n'
    )
    for file in RUNNER.INSPECTION:
        (repository / file).write_text(
            "def test_owned_inspection():\n    assert True\n"
        )
    (repository / "tests/test_other.py").write_text(
        "def test_other():\n    assert True\n"
    )
    (repository / "testing/test_new_discovery.py").write_text(
        "def test_new():\n    assert True\n"
    )
    environment = {
        **os.environ,
        "PYTHONPATH": str(REPOSITORY),
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "CI_TELEMETRY_FILE": str(tmp_path / "merged.jsonl"),
        "CI_TELEMETRY_SHA": META["source_sha"],
        "CI_TELEMETRY_RUN_ID": str(META["run_id"]),
        "CI_TELEMETRY_RUN_ATTEMPT": str(META["run_attempt"]),
        "CI_TELEMETRY_LANE": "python",
    }
    temporary = tmp_path / "temporary"
    temporary.mkdir()
    return repository, environment, temporary


def test_real_default_union_matches_original_discovery_with_isolated_resources(tiny):
    root, environment, temporary = tiny
    (root / "conftest.py").write_text(
        "import json, os\n"
        "def pytest_sessionfinish(session):\n"
        "    with open('roots.jsonl', 'a') as stream:\n"
        "        previous = os.umask(0); os.umask(previous)\n"
        "        stream.write(json.dumps([str(session.config._tmp_path_factory.getbasetemp()), "
        "str(session.config.cache._cachedir), oct(previous)]) + '\\n')\n"
    )
    serial = Path(environment["CI_TELEMETRY_FILE"]).with_name("serial.jsonl")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "scripts.ci.pytest_timing",
            f"--basetemp={temporary / 'serial-tmp'}",
            "-o",
            f"cache_dir={temporary / 'serial-cache'}",
        ],
        cwd=root,
        env={**environment, "CI_TELEMETRY_FILE": str(serial)},
        check=False,
    )
    assert result.returncode == 0
    (root / "roots.jsonl").unlink()
    assert (
        RUNNER.run(root=root, environment=environment, temporary_parent=temporary) == 0
    )
    records = load(Path(environment["CI_TELEMETRY_FILE"]))
    actual = summarize(records)
    expected = summarize(load(serial))
    assert actual["complete"] and actual["collected"] == expected["collected"] == 6
    assert actual["attempts"] == 6
    assert {item["test_id"] for item in records if item["kind"] == "plan"} == {
        item["test_id"] for item in load(serial) if item["kind"] == "plan"
    }
    resources = [
        json.loads(line) for line in (root / "roots.jsonl").read_text().splitlines()
    ]
    assert len(resources) == 2
    assert resources[0][0] != resources[1][0] and resources[0][1] != resources[1][1]
    assert all(
        not Path(item).exists() for resource in resources for item in resource[:2]
    )
    assert not (root / ".pytest_cache").exists()
    assert not list(temporary.glob("schemii-python-*"))


def test_actual_partitions_execute_concurrently_without_duplicate_fixture_setup(tiny):
    root, environment, temporary = tiny
    (root / "conftest.py").write_text(
        "import os, pathlib, pytest\n"
        "@pytest.fixture(scope='session')\n"
        "def inspection_baseline():\n"
        "    with open('inspection-builds', 'a') as stream: stream.write(str(os.getpid()) + '\\n')\n"
        "    return True\n"
    )
    for item in RUNNER.INSPECTION:
        (root / item).write_text(
            "def test_owned_inspection(inspection_baseline):\n    assert inspection_baseline\n"
        )
    barrier = (
        "import os, pathlib, time\n"
        "def test_concurrent{fixture}:\n"
        "    pathlib.Path('barrier-' + str(os.getpid())).touch()\n"
        "    deadline = time.monotonic() + 5\n"
        "    while len(list(pathlib.Path('.').glob('barrier-*'))) < 2:\n"
        "        assert time.monotonic() < deadline, 'peer was not running concurrently'\n"
        "        time.sleep(0.01)\n"
    )
    (root / RUNNER.INSPECTION[0]).write_text(
        barrier.format(fixture="(inspection_baseline)")
    )
    (root / "tests/test_other.py").write_text(barrier.format(fixture="()"))
    assert (
        RUNNER.run(root=root, environment=environment, temporary_parent=temporary) == 0
    )
    assert len((root / "inspection-builds").read_text().splitlines()) == 1
    assert len(list(root.glob("barrier-*"))) == 2
    assert summarize(load(Path(environment["CI_TELEMETRY_FILE"])))["collected"] == 6


def test_real_partition_failure_stays_visible_while_peer_completes(tiny):
    root, environment, temporary = tiny
    (root / "tests/test_other.py").write_text(
        "def test_original_failure():\n    assert False\n"
    )
    assert (
        RUNNER.run(root=root, environment=environment, temporary_parent=temporary) == 1
    )
    result = summarize(load(Path(environment["CI_TELEMETRY_FILE"])))
    assert result["complete"] and result["outcome"] == "failed"
    assert result["first_attempt_failures"] == 1 and result["first_attempt_passes"] == 5
    assert result["retry_recovered"] == 0 and result["attempts"] == 6
    assert not list(temporary.iterdir())


def test_focused_selection_runs_one_family_and_rejects_overlap(tiny):
    root, environment, temporary = tiny
    assert len(RUNNER.partitions(root, [RUNNER.INSPECTION[0]])) == 1
    assert (
        RUNNER.run(
            ["testing/test_new_discovery.py"],
            root=root,
            environment=environment,
            temporary_parent=temporary,
        )
        == 0
    )
    result = summarize(load(Path(environment["CI_TELEMETRY_FILE"])))
    assert result["collected"] == result["attempts"] == 1
    with pytest.raises(ValueError, match="Overlapping"):
        RUNNER.partitions(root, ["tests", "tests/test_other.py"])
    with pytest.raises(ValueError, match="Overlapping"):
        RUNNER.partitions(
            root, ["tests/test_other.py", "tests/test_other.py::test_other"]
        )
    assert not list(temporary.iterdir())


@pytest.mark.parametrize("case", ["empty", "collection-error"])
def test_real_invalid_collection_cannot_publish_complete_peer_subset(tiny, case):
    root, environment, temporary = tiny
    for path in (root / "tests/test_other.py", root / "testing/test_new_discovery.py"):
        path.write_text(
            "raise ValueError('original collection error')\n"
            if case == "collection-error"
            else ""
        )
    status = RUNNER.run(root=root, environment=environment, temporary_parent=temporary)
    assert status == (2 if case == "collection-error" else 5)
    result = summarize(load(Path(environment["CI_TELEMETRY_FILE"])))
    assert not result["complete"] and result["outcome"] in {"error", "collection-error"}
    assert result["collected"] == 4
    assert not list(temporary.iterdir())


def receipt(name="one"):
    test_id = hashlib.sha256(name.encode()).hexdigest()
    return [
        {**META, "kind": "start", "planned": 1},
        {**META, "kind": "plan", "test_id": test_id},
        {
            **META,
            "kind": "attempt",
            "test_id": test_id,
            "source_id": "b" * 64,
            "source_line": 1,
            "attempt": 0,
            "outcome": "passed",
            "skip": "none",
            "setup_ms": 1,
            "execution_ms": 2,
            "teardown_ms": 1,
        },
        {**META, "kind": "end", "outcome": "passed", "wall_ms": 1000},
    ]


def test_strict_merge_uses_concurrent_wall_and_original_attempts():
    first, second = receipt(), receipt("two")
    merged = RUNNER.merge([first, second], [0, 0], 1200, META)
    result = summarize(merged)
    assert result["complete"] and result["wall_ms"] == 1200
    assert result["phase_totals_ms"]["execution_ms"] == 4
    assert merged[3:5] == [first[2], second[2]]


@pytest.mark.parametrize(
    "defect",
    [
        "duplicate",
        "missing",
        "empty",
        "identity",
        "stale-cohort",
        "missing-plan",
        "missing-footer",
        "missing-attempt",
    ],
)
def test_strict_merge_rejects_invalid_or_incomplete_inventory(defect):
    first, second = receipt(), receipt("two")
    if defect == "duplicate":
        second = copy.deepcopy(first)
    elif defect == "missing":
        with pytest.raises(ValueError, match="Missing"):
            RUNNER.merge([first], [0, 0], 1, META)
        return
    elif defect == "empty":
        second = [second[0], second[-1]]
        second[0]["planned"] = 0
    elif defect == "identity":
        second = [{**item, "run_attempt": 2} for item in second]
    elif defect == "stale-cohort":
        first = [{**item, "source_sha": "c" * 40} for item in first]
        second = [{**item, "source_sha": "c" * 40} for item in second]
    elif defect == "missing-plan":
        second.pop(1)
    elif defect == "missing-footer":
        second.pop()
    else:
        second.pop(2)
    if defect in {"duplicate", "identity", "stale-cohort", "missing-plan"}:
        with pytest.raises(ValueError):
            RUNNER.merge([first, second], [0, 0], 1, META)
    else:
        assert not summarize(RUNNER.merge([first, second], [0, 0], 1, META))["complete"]


def wait_for(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("Owned process did not reach its expected lifecycle state")


def alive_identity(pid, birth):
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    except FileNotFoundError:
        return False
    return int(fields[19]) == birth and fields[0] not in {"Z", "X", "x"}


@pytest.mark.parametrize("signum", [signal.SIGINT, signal.SIGTERM])
def test_signal_automatically_reaps_owned_descendants_and_temporary_roots(tiny, signum):
    root, environment, temporary = tiny
    source = (
        "import os, pathlib, subprocess, sys, time\n"
        "def test_owned_block():\n"
        "    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "    pathlib.Path('ready-' + str(os.getpid())).write_text(str(child.pid))\n"
        "    time.sleep(60)\n"
    )
    (root / RUNNER.INSPECTION[0]).write_text(source)
    (root / "tests/test_other.py").write_text(source)
    controller = (
        "import importlib.util, pathlib, sys; "
        "spec=importlib.util.spec_from_file_location('runner', sys.argv[1]); "
        "module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module); "
        "raise SystemExit(module.run(root=pathlib.Path(sys.argv[2]), temporary_parent=sys.argv[3]))"
    )
    peer = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True
    )
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            controller,
            str(REPOSITORY / "scripts/ci/python-tests.py"),
            str(root),
            str(temporary),
        ],
        env=environment,
        start_new_session=True,
    )
    identities = []
    try:
        wait_for(lambda: len(list(root.glob("ready-*"))) == 2)
        for ready in root.glob("ready-*"):
            for pid in (int(ready.name.removeprefix("ready-")), int(ready.read_text())):
                identities.append((pid, RUNNER.birth_tick(pid)))
        assert len(list(temporary.iterdir())) == 1
        process.send_signal(signum)
        assert process.wait(timeout=10) == 128 + signum
        assert not list(temporary.iterdir())
        wait_for(
            lambda: not any(alive_identity(pid, birth) for pid, birth in identities)
        )
        assert peer.poll() is None
        output = Path(environment["CI_TELEMETRY_FILE"])
        assert not output.exists() or not summarize(load(output))["complete"]
    finally:
        if process.poll() is None:
            process.send_signal(signal.SIGTERM)
            process.wait(timeout=10)
        peer.terminate()
        peer.wait(timeout=5)
        # Success assertions precede this emergency finalizer, which prevents
        # deliberately faulted controls from leaking their planted descendants.
        for pid, birth in identities:
            if alive_identity(pid, birth):
                os.kill(pid, signal.SIGKILL)


def test_normal_exit_cleans_ignoring_descendant_before_removing_owned_paths(
    tiny, monkeypatch
):
    root, environment, temporary = tiny
    (root / "tests/test_other.py").write_text(
        "import json, pathlib, subprocess, sys\n"
        "def test_descendant():\n"
        "    child = subprocess.Popen([sys.executable, '-c', "
        "'import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        'open("ignoring-ready", "w").write("ready"); time.sleep(60)\'])\n'
        "    import time\n"
        "    while not pathlib.Path('ignoring-ready').exists(): time.sleep(0.01)\n"
        "    birth = int(pathlib.Path(f'/proc/{child.pid}/stat').read_text().rsplit(')', 1)[1].split()[19])\n"
        "    pathlib.Path('descendant').write_text(json.dumps([child.pid, birth]))\n"
    )
    # This changes only the controlled fixture's shutdown grace, not production.
    monkeypatch.setattr(RUNNER, "SHUTDOWN_GRACE", 0.1)
    original_cleanup = RUNNER.tempfile.TemporaryDirectory.cleanup

    def verify_cleanup_after_exit(directory):
        identity = root / "descendant"
        if identity.exists():
            pid, birth = json.loads(identity.read_text())
            assert not alive_identity(pid, birth), (
                "paths released before descendant exited"
            )
        return original_cleanup(directory)

    monkeypatch.setattr(
        RUNNER.tempfile.TemporaryDirectory, "cleanup", verify_cleanup_after_exit
    )
    try:
        assert (
            RUNNER.run(root=root, environment=environment, temporary_parent=temporary)
            == 0
        )
        pid, birth = json.loads((root / "descendant").read_text())
        assert not alive_identity(pid, birth)
        assert not list(temporary.iterdir())
        assert summarize(load(Path(environment["CI_TELEMETRY_FILE"])))["complete"]
    finally:
        identity = root / "descendant"
        if identity.exists():
            pid, birth = json.loads(identity.read_text())
            if alive_identity(pid, birth):
                os.kill(pid, signal.SIGKILL)
