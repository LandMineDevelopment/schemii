"""No Docker, app server, canonical lock, database or public network is used here."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import stat

import pytest

from dev.pressure import controller as c


def container(service, project, run=None, image=None):
    identifier = hashlib.sha256((project + service).encode()).hexdigest()
    limits = {
        "metadata": (268435456, 500000000, 64),
        "source": (268435456, 500000000, 64),
        "schemii": (536870912, 1000000000, 128),
        "ingress": (67108864, 250000000, 32),
    }
    memory, cpu, pids = limits[service]
    return {
        "id": identifier,
        "created": "2026-11-12T00:00:00Z",
        "name": f"/{project}-{service}-1",
        "image": image
        or "sha256:"
        + ("a" if service == "schemii" else "b" if service == "ingress" else "c") * 64,
        "state": "running",
        "health": "healthy",
        "labels": {
            "com.docker.compose.project": project,
            "com.docker.compose.service": service,
            **({c.RUN_LABEL: run} if run else {}),
        },
        "ports": {"8443/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8001"}]}
        if service == "ingress"
        else {},
        "mounts": [],
        "networks": {
            project + "-database": {
                "NetworkID": hashlib.sha256((project + "network").encode()).hexdigest(),
                "IPAddress": "172.30.0.2" if service == "metadata" else "172.30.0.3",
            }
        },
        "memory": memory,
        "nanoCpus": cpu,
        "pids": pids,
    }


class Runtime:
    """Closed command surface: unknown commands and cross-project writes trip."""

    def __init__(self, root):
        self.root = root
        self.records = {
            record["id"]: record
            for record in (
                container("ingress", "schemii-test"),
                container("schemii", "schemii-test"),
            )
        }
        self.resources = {"volume": {}, "network": {}}
        self.calls = []
        self.writes = []
        self.fail = None
        self.name_collision = None
        self.endpoint = "unix:///local-test-endpoint"
        self.daemon = "owned-local-daemon"
        self.entrypoint = None

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        if argv[0] == "git":
            if "ls-files" in argv:
                return "testing/load/source_monitor.py\n"
            assert "diff" in argv and "--quiet" in argv
            return ""
        if argv[0] != "docker":
            assert argv[1:] == ["-c", "import psycopg,httpx"]
            return ""
        args = argv[1:]
        if args == ["context", "show"]:
            return "default"
        if args[:2] == ["context", "inspect"]:
            return json.dumps(self.endpoint)
        if args[:2] == ["info", "--format"]:
            return json.dumps(self.daemon)
        if (
            args[0] in {"container", "volume", "network"}
            and args[1] == "ls"
            and args[-1].startswith("name=")
        ):
            project = kwargs["environment"]["PRESSURE_PROJECT"]
            if self.name_collision == args[0]:
                return project + "-metadata"
            if args[0] == "container":
                return "\n".join(
                    record["id"]
                    for record in self.records.values()
                    if project in record["name"]
                )
            return "\n".join(
                name for name in self.resources[args[0]] if project in name
            )
        if args[:2] == ["container", "ls"]:
            project = args[-1].split("=", 2)[-1]
            return "\n".join(
                record["id"]
                for record in self.records.values()
                if record["labels"]["com.docker.compose.project"] == project
            )
        if args[:2] == ["container", "inspect"]:
            assert args[2:4] == ["--format", c.INSPECT]
            return json.dumps(self.records[args[-1]])
        if args[:2] == ["container", "exec"]:
            assert args[2] == container("schemii", "schemii-test")["id"]
            assert args[3:] == ["python", "-c", c.PACKAGE_SCRIPT]
            return json.dumps(c.package_identity(self.root))
        if args[:2] == ["image", "inspect"]:
            if args[3] == "{{json .Config.Entrypoint}}":
                return json.dumps(self.entrypoint)
            if args[3] == "{{json .Config.Cmd}}":
                return json.dumps(
                    [
                        "uvicorn",
                        "schemii.main:app",
                        "--host",
                        "0.0.0.0",
                        "--port",
                        "8000",
                        "--no-access-log",
                    ]
                )
            return json.dumps("sha256:" + ("b" if args[-1] == c.INGRESS else "c") * 64)
        if args[0] in {"volume", "network"}:
            kind = args[0]
            if args[1] == "ls":
                project = args[-1].split("=", 2)[-1]
                return "\n".join(
                    name
                    for name, value in self.resources[kind].items()
                    if value["Labels"]["com.docker.compose.project"] == project
                )
            assert args[1] == "inspect"
            return json.dumps([self.resources[kind][args[-1]]])
        if args[:2] in (["container", "stop"], ["container", "start"]):
            record = self.records[args[-1]]
            assert (
                record["labels"]["com.docker.compose.project"] == "schemii-test"
                and record["labels"]["com.docker.compose.service"] == "ingress"
                or record["labels"].get(c.RUN_LABEL)
            )
            self.writes.append(args)
            record["state"] = "exited" if args[1] == "stop" else "running"
            if self.fail == "uncertain-state":
                raise c.Refused("fake_cli_error")
            return record["id"]
        assert args[0] == "compose"
        assert args[1:3] == ["--env-file", "/dev/null"]
        env = kwargs["environment"]
        project, run = env["PRESSURE_PROJECT"], env["PRESSURE_RUN"]
        assert project.startswith("schemii-pressure-") and project != "schemii-test"
        journal = c.private_read(Path(env["PRESSURE_DIRECTORY"]) / "journal.json")
        assert journal["stage"] == "operation-intent"
        assert (
            journal["operation"].startswith("up:")
            if "up" in args
            else journal["operation"] == "cleanup-owned-project"
        )
        self.writes.append(args)
        if "up" in args:
            services = args[args.index("60") + 1 :]
            for service in services:
                record = container(service, project, run)
                self.records[record["id"]] = record
            for kind, names in (
                ("volume", ("metadata", "source")),
                ("network", ("database", "ingress", "loopback")),
            ):
                for name in names:
                    full = project + "-" + name
                    self.resources[kind][full] = {
                        "Name": full,
                        "Id": hashlib.sha256(full.encode()).hexdigest(),
                        "CreatedAt": "2026-11-12",
                        "Created": None,
                        "Driver": "local",
                        "Labels": {
                            c.RUN_LABEL: run,
                            "com.docker.compose.project": project,
                        },
                    }
            if self.fail == "create-after-write":
                raise c.Refused("fake_create_error")
        elif "down" in args:
            self.records = {
                key: value
                for key, value in self.records.items()
                if value["labels"]["com.docker.compose.project"] != project
            }
            for kind in self.resources:
                self.resources[kind] = {
                    key: value
                    for key, value in self.resources[kind].items()
                    if value["Labels"]["com.docker.compose.project"] != project
                }
        else:
            raise AssertionError("unsupported fake command")
        return ""


@pytest.fixture
def setup(tmp_path, monkeypatch):
    root, primary = tmp_path / "checkout", tmp_path / "primary"
    root.mkdir()
    primary.mkdir()
    source = Path(__file__).resolve().parents[1]
    for name in c.ASSETS:
        destination = root / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((source / name).read_bytes())
    (root / "src/schemii").mkdir(parents=True)
    (root / "src/schemii/owned.py").write_text("VALUE = 42\n")
    (root / "testing/load").mkdir(parents=True)
    (root / "testing/load/source_monitor.py").write_text("# fake committed witness\n")
    (primary / ".venv/bin").mkdir(parents=True)
    (primary / ".venv/bin/python").touch()
    monkeypatch.setattr(
        c,
        "REQUIRED_SOURCE_WITNESS_SHA256",
        c.sha(root / "testing/load/source_monitor.py"),
    )
    runtime = Runtime(root)
    monkeypatch.setattr(c, "command", runtime)
    monkeypatch.setattr(c, "ready_origins", lambda: None)
    monkeypatch.setattr(c, "free_canonical_port", lambda: None)
    return root, primary, runtime


def fixture(setup):
    root, primary, runtime = setup
    return c.begin(root, primary, None, runtime)


def test_intent_is_private_and_precedes_every_create(setup):
    owned = fixture(setup)
    assert setup[2].writes == []
    value = c.private_read(owned.journal)
    assert value["stage"] == "intent-before-create"
    assert stat.S_IMODE(owned.journal.stat().st_mode) == 0o600
    assert stat.S_IMODE(owned.directory.stat().st_mode) == 0o700
    owned.up(["metadata", "source"])
    assert (
        owned.state["containers"]["metadata"]["labels"][c.RUN_LABEL]
        == owned.state["run"]
    )
    assert len(owned.state["resources"]) == 5


def test_empty_retained_deployment_refuses_before_any_creation(setup):
    root, primary, runtime = setup
    runtime.records.clear()
    with pytest.raises(c.Refused, match="retained_deployment_required"):
        c.begin(root, primary, None, runtime)
    assert runtime.writes == []
    assert not (primary / ".schemii").exists()


@pytest.mark.parametrize(
    "field,value",
    [
        ("state", "exited"),
        ("health", "unhealthy"),
        ("name", "/another-ingress"),
        ("ports", {"8443/tcp": [{"HostIp": "0.0.0.0", "HostPort": "9000"}]}),
    ],
)
def test_ineligible_retained_ingress_refuses_before_mutation(setup, field, value):
    root, primary, runtime = setup
    runtime.records[container("ingress", "schemii-test")["id"]][field] = value
    with pytest.raises(c.Refused):
        c.begin(root, primary, None, runtime)
    assert runtime.writes == []


def test_missing_source_witness_refuses_before_fixture_or_retained_mutation(setup):
    root, primary, runtime = setup
    (root / "testing/load/source_monitor.py").unlink()
    with pytest.raises(c.Refused, match="source_monitor_dependency_missing"):
        c.begin(root, primary, None, runtime)
    assert runtime.writes == []
    assert not (primary / ".schemii").exists()


def test_failed_probe_restores_exact_retained_ingress_and_preserves_user_state(
    setup, monkeypatch
):
    owned = fixture(setup)
    runtime = setup[2]
    baseline = deepcopy(runtime.records)
    user = setup[1] / "retained-user-data"
    user.write_bytes(b"private unchanged")
    monkeypatch.setattr(c.Fixture, "secrets", lambda self: self.intent("fake-secrets"))
    monkeypatch.setattr(
        c.Fixture,
        "probe",
        lambda self, phase: (
            {
                "phase": "prepared",
                "sourceBaseline": {"ownedBackends": 0, "releaseVerified": False},
            }
            if phase == "prepare"
            else {"acceptance": False, "capacityAcceptance": False}
        ),
    )
    with pytest.raises(c.Refused, match="qualification_incomplete"):
        owned.run()
    assert runtime.records == baseline
    assert user.read_bytes() == b"private unchanged"
    assert owned.state["restored"] is True and owned.state["cleanup"] is True
    writes_to_retained = [
        args
        for args in runtime.writes
        if args[:2] in (["container", "stop"], ["container", "start"])
    ]
    assert [args[1] for args in writes_to_retained] == ["stop", "start"]
    assert all(
        args[-1] == baseline[container("ingress", "schemii-test")["id"]]["id"]
        for args in writes_to_retained
    )


def test_uncertain_create_is_inspected_before_cleanup_without_repeating_create(setup):
    owned = fixture(setup)
    setup[2].fail = "create-after-write"
    with pytest.raises(c.Refused):
        owned.up(["metadata", "source"])
    assert len(owned.state["containers"]) == 2
    setup[2].fail = None
    owned.cleanup()
    assert len([args for args in setup[2].writes if "up" in args]) == 1
    assert owned.state["cleanup"] is True


@pytest.mark.parametrize(
    "field,value",
    [
        ("image", "sha256:" + "e" * 64),
        ("memory", 0),
        ("ports", {"5432/tcp": [{"HostPort": "5432"}]}),
        ("mounts", [{"Type": "volume", "Name": "retained-metadata"}]),
    ],
)
def test_unknown_or_drifted_disposable_identity_never_bulk_deletes(setup, field, value):
    owned = fixture(setup)
    owned.up(["metadata", "source"])
    target = setup[2].records[owned.state["containers"]["metadata"]["id"]]
    target[field] = value
    with pytest.raises(c.Refused):
        owned.cleanup()
    assert not any("down" in args for args in setup[2].writes)
    assert owned.state["restored"] is True
    assert owned.state["cleanup"] is False


def test_guarded_recovery_after_controller_exit_never_replays_creation(setup):
    owned = fixture(setup)
    owned.up(["metadata", "source"])
    owned.up(["schemii"])
    owned.stop_retained()
    owned.up(["ingress"])
    owned.state["owner"] = {"pid": os.getpid(), "start": "different-old-start-tick"}
    owned.save("operation-intent", operation="probe:qualify")
    recovered = c.recover(owned.root, owned.primary, owned.directory, setup[2])
    before = len([args for args in setup[2].writes if "up" in args])
    recovered.cleanup()
    assert len([args for args in setup[2].writes if "up" in args]) == before
    assert recovered.state["restored"] and recovered.state["cleanup"]


def test_recovery_refuses_live_controller_or_changed_source(setup):
    owned = fixture(setup)
    with pytest.raises(c.Refused, match="recorded_controller_still_active"):
        c.recover(owned.root, owned.primary, owned.directory, setup[2])
    (owned.root / "dev/pressure/compose.yaml").write_text("changed\n")
    with pytest.raises(c.Refused, match="recovery_source_drift"):
        c.recover(owned.root, owned.primary, owned.directory, setup[2])
    assert setup[2].writes == []


def test_retained_replacement_is_not_started_or_adopted(setup):
    owned = fixture(setup)
    owned.up(["metadata", "source"])
    retained = setup[2].records[owned.state["retainedIngress"]["id"]]
    retained["created"] = "replacement"
    with pytest.raises(c.Refused, match="retained_restore_owner_changed"):
        owned.cleanup()
    assert not any(args[:2] == ["container", "start"] for args in setup[2].writes)


@pytest.mark.parametrize("mode", [0o644, 0o666])
def test_public_or_symlinked_journals_are_rejected(tmp_path, mode):
    path = tmp_path / "journal.json"
    path.write_text("{}")
    path.chmod(mode)
    with pytest.raises(c.Refused, match="journal_permissions"):
        c.private_read(path)
    linked = tmp_path / "linked"
    linked.symlink_to(path)
    with pytest.raises(c.Refused, match="journal_permissions"):
        c.private_read(linked)


def test_protocol_partial_line_and_oversize_fail_without_waiting_for_eof():
    read, write = os.pipe()
    try:
        os.write(write, b'{"operation":')
        os.close(write)
        write = -1
        with pytest.raises(c.Refused, match="probe_partial_reply"):
            c.Fixture.protocol_line(read, __import__("time").monotonic() + 1)
    finally:
        os.close(read)
        if write >= 0:
            os.close(write)


def shell_fixture(tmp_path):
    import shutil
    import sys

    source = Path(__file__).resolve().parents[1]
    root, primary, common, binary = (
        tmp_path / name for name in ("checkout", "primary", "common", "bin")
    )
    for path in (root, primary, common, binary):
        path.mkdir()
    (root / "dev/pressure").mkdir(parents=True)
    for name in ("start.sh", "dev/pressure/launcher.sh"):
        shutil.copy2(source / name, root / name)
    (root / "dev/pressure/controller.py").touch()
    log = tmp_path / "fake-commands.jsonl"
    for name in ("bash", "dirname", "sed", "readlink", "flock"):
        (binary / name).symlink_to(shutil.which(name))
    header = f'#!{sys.executable}\nimport json,os,sys\nfrom pathlib import Path\nwith Path({str(log)!r}).open("a") as f:f.write(json.dumps([Path(sys.argv[0]).name,*sys.argv[1:]])+"\\n")\n'
    (binary / "git").write_text(
        header
        + f'if sys.argv[1:]==["-C",{str(root)!r},"rev-parse","--path-format=absolute","--git-common-dir"]:print({str(common)!r})\n'
        f'elif sys.argv[1:]==["--git-dir="+{str(common)!r},"worktree","list","--porcelain"]:print("worktree "+{str(primary)!r})\n'
        "else:sys.exit(99)\n"
    )
    (binary / "docker").write_text(
        header + 'sys.exit(0 if sys.argv[1:]==["info"] else 98)\n'
    )
    (binary / "python3").write_text(
        header
        + 'assert "--qualify-metadata-pressure" in sys.argv or "--recover-metadata-pressure" in sys.argv\n'
    )
    for name in ("git", "docker", "python3"):
        (binary / name).chmod(0o755)
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("SCHEMII_", "DOCKER_", "COMPOSE_"))
    }
    environment["PATH"] = str(binary)
    return root, common, log, environment


@pytest.mark.parametrize("lockname", ["qa-startup.lock", "qa-deployment.lock"])
def test_shell_mode_refuses_active_disposable_lease_before_docker(tmp_path, lockname):
    import fcntl
    import subprocess

    root, common, log, environment = shell_fixture(tmp_path)
    with (common / lockname).open("a") as lease:
        fcntl.flock(lease, fcntl.LOCK_EX)
        result = subprocess.run(
            [str(root / "start.sh"), "--qualify-metadata-pressure"],
            env=environment,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    assert result.returncode == 1
    events = list(map(json.loads, log.read_text().splitlines()))
    assert all(event[0] == "git" for event in events)


def test_shell_mode_dispatches_only_controller_after_exclusive_fake_leases(tmp_path):
    import subprocess

    root, _, log, environment = shell_fixture(tmp_path)
    result = subprocess.run(
        [str(root / "start.sh"), "--qualify-metadata-pressure"],
        env=environment,
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    events = list(map(json.loads, log.read_text().splitlines()))
    assert [event[0] for event in events] == ["git", "git", "docker", "python3"]
    assert events[2] == ["docker", "info"]
    assert events[3][-2:] == ["--", "--qualify-metadata-pressure"]
    assert not (root / ".schemii").exists()


def test_cancelled_live_phase_restores_without_replaying_or_other_project_writes(
    setup, monkeypatch
):
    owned = fixture(setup)
    baseline = deepcopy(setup[2].records)
    monkeypatch.setattr(c.Fixture, "secrets", lambda self: self.intent("fake-secrets"))

    def probe(self, phase):
        if phase == "qualify":
            raise KeyboardInterrupt()
        return {
            "phase": "prepared",
            "sourceBaseline": {"ownedBackends": 0, "releaseVerified": False},
        }

    monkeypatch.setattr(c.Fixture, "probe", probe)
    with pytest.raises(KeyboardInterrupt):
        owned.run()
    assert setup[2].records == baseline
    assert owned.state["restored"] and owned.state["cleanup"]


def test_unknown_project_resource_is_quarantined_while_exact_ingress_restores(setup):
    owned = fixture(setup)
    owned.up(["metadata", "source"])
    owned.up(["schemii"])
    owned.stop_retained()
    owned.up(["ingress"])
    target = next(iter(setup[2].resources["volume"].values()))
    target["Labels"][c.RUN_LABEL] = "foreign"
    with pytest.raises(c.Refused, match="resource_owner"):
        owned.cleanup()
    assert not any("down" in args for args in setup[2].writes)
    assert setup[2].records[owned.state["retainedIngress"]["id"]]["state"] == "running"
    assert owned.state["restored"] and not owned.state["cleanup"]


@pytest.mark.parametrize("kind", ["container", "volume", "network"])
def test_foreign_or_unlabeled_name_collision_refuses_before_any_creation(setup, kind):
    root, primary, runtime = setup
    runtime.name_collision = kind
    with pytest.raises(c.Refused, match="fresh_resource_names_required"):
        c.begin(root, primary, None, runtime)
    assert runtime.writes == []
    assert not (primary / ".schemii").exists()


def test_remote_context_and_changed_recovery_daemon_are_refused(setup):
    root, primary, runtime = setup
    runtime.endpoint = "ssh://another-machine"
    with pytest.raises(c.Refused, match="local_runtime_context_required"):
        c.begin(root, primary, None, runtime)
    assert runtime.writes == []
    runtime.endpoint = "unix:///local-test-endpoint"
    owned = fixture(setup)
    owned.state["owner"] = {"pid": os.getpid(), "start": "old-tick"}
    owned.save("operation-intent", operation="before-create")
    runtime.daemon = "another-local-daemon"
    with pytest.raises(c.Refused, match="recovery_runtime_context_drift"):
        c.recover(root, primary, owned.directory, runtime)
    assert runtime.writes == []


def test_controller_rederives_canonical_common_directory_instead_of_trusting_args(
    tmp_path, monkeypatch
):
    root, primary, common = (tmp_path / name for name in ("root", "primary", "common"))
    calls = []

    def git(argv, **kwargs):
        calls.append(argv)
        return (
            str(common) + "\n"
            if "rev-parse" in argv
            else "worktree " + str(primary) + "\0HEAD owned\0"
        )

    monkeypatch.setattr(c, "command", git)
    c.checkout_authority(root, primary, common)
    with pytest.raises(c.Refused, match="canonical_git_common_directory"):
        c.checkout_authority(root, primary, tmp_path / "unrelated-common")
    assert all(argv[0] == "git" for argv in calls)


def test_sigkill_of_exact_fake_controller_retains_recoverable_journal(setup):
    import subprocess
    import sys

    child = subprocess.Popen(
        [sys.executable, "-c", "import time;time.sleep(30)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        owned = fixture(setup)
        owned.up(["metadata", "source"])
        owned.stop_retained()
        owned.state["owner"] = {
            "pid": child.pid,
            "start": Path(f"/proc/{child.pid}/stat").read_text().split()[21],
        }
        owned.save("operation-intent", operation="up:ingress")
        with pytest.raises(c.Refused, match="recorded_controller_still_active"):
            c.recover(owned.root, owned.primary, owned.directory, setup[2])
        child.kill()
        child.wait()
        recovered = c.recover(owned.root, owned.primary, owned.directory, setup[2])
        recovered.cleanup()
        assert recovered.state["restored"] and recovered.state["cleanup"]
        assert len([args for args in setup[2].writes if "up" in args]) == 1
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()


def test_cleanup_does_not_remove_replacement_volume_with_missing_project_label(setup):
    owned = fixture(setup)
    owned.up(["metadata", "source"])
    target = next(iter(setup[2].resources["volume"].values()))
    target["Labels"].pop("com.docker.compose.project")
    # The daemon's project-label filter cannot see this replacement; its exact
    # name still blocks compose down before any volume deletion.
    for resource in setup[2].resources["volume"].values():
        resource["Labels"].setdefault("com.docker.compose.project", "foreign")
    with pytest.raises(c.Refused, match="unlabeled_or_foreign_resource"):
        owned.cleanup()
    assert not any("down" in args for args in setup[2].writes)
    assert target in setup[2].resources["volume"].values()


def test_oversize_journal_update_preserves_previous_valid_intent(tmp_path):
    path = tmp_path / "journal.json"
    c.private_write(path, {"stage": "original-intent"})
    with pytest.raises(c.Refused, match="journal_size"):
        c.private_write(path, {"data": "x" * 262145})
    assert c.private_read(path) == {"stage": "original-intent"}


def test_changed_retained_network_identity_is_never_restarted(setup):
    owned = fixture(setup)
    owned.up(["metadata", "source"])
    original = setup[2].records[owned.state["retainedIngress"]["id"]]
    original["state"] = "exited"
    next(iter(original["networks"].values()))["NetworkID"] = "e" * 64
    with pytest.raises(c.Refused, match="retained_restore_owner_changed"):
        owned.cleanup()
    assert not any(args[:2] == ["container", "start"] for args in setup[2].writes)


def test_fifo_journal_is_rejected_before_open_or_block(tmp_path):
    path = tmp_path / "journal.json"
    os.mkfifo(path, 0o600)
    with pytest.raises(c.Refused, match="journal_permissions"):
        c.private_read(path)


def test_unexpected_application_image_entrypoint_refuses_before_creation(setup):
    root, primary, runtime = setup
    runtime.entrypoint = ["another-server-launcher"]
    with pytest.raises(c.Refused, match="application_image_entrypoint"):
        c.begin(root, primary, None, runtime)
    assert runtime.writes == []
    assert not (primary / ".schemii").exists()


def test_fixture_drops_ambient_database_authority_and_refuses_redirects(
    setup, monkeypatch
):
    monkeypatch.setenv("PGSERVICE", "retained-user-service")
    monkeypatch.setenv("PGPASSWORD", "private-test-secret")
    owned = fixture(setup)
    assert (
        "PGSERVICE" not in owned.environment and "PGPASSWORD" not in owned.environment
    )
    assert (
        c.NoRedirect().redirect_request(
            None, None, 302, "redirect", {}, "http://another-origin/"
        )
        is None
    )


def test_fixed_command_output_and_timeout_are_bounded_and_sanitized():
    import sys

    with pytest.raises(c.Refused, match="command_output_bound"):
        c.command(
            [
                sys.executable,
                "-c",
                "import sys;print('PRIVATE_ERROR_SENTINEL',file=sys.stderr);sys.stdout.write('x'*4096)",
            ],
            limit=128,
        )
    with pytest.raises(c.Refused, match="command_timeout"):
        c.command([sys.executable, "-c", "import time;time.sleep(2)"], timeout=0.05)


def test_partial_probe_reply_cannot_block_past_its_deadline():
    import time

    read, write = os.pipe()
    try:
        os.write(write, b'{"operation":')
        with pytest.raises(c.Refused, match="probe_timeout"):
            c.Fixture.protocol_line(read, time.monotonic() + 0.05)
    finally:
        os.close(read)
        os.close(write)


def test_old_or_modified_source_witness_never_admits_fixture_creation(setup):
    root, primary, runtime = setup
    (root / "testing/load/source_monitor.py").write_text("# old unbound witness\n")
    with pytest.raises(c.Refused, match="source_witness_fingerprint"):
        c.begin(root, primary, None, runtime)
    assert runtime.writes == []
    assert not (primary / ".schemii").exists()
