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


def test_execution_channel_exports_only_fixed_owned_artifact_under_existing_fences(
    tmp_path, monkeypatch
):
    import io
    import tarfile
    from dev.pressure.execution_observer import ExecutionObserver

    root = tmp_path / "root"
    root.mkdir()
    (root / "asset").write_text("source")
    directory = tmp_path / "pressure"
    directory.mkdir(mode=0o700)
    (directory / "execution-input").mkdir(mode=0o750)
    owned = {"id": "a" * 64}
    process = {
        "pid": 1,
        "uid": 10001,
        "startTicks": "1",
        "incarnation": "b" * 32,
        "startedAt": c.now(),
    }
    baseline = {
        "containerId": owned["id"],
        "containerStartedAt": c.now(),
        "observation": {"process": process},
    }
    monkeypatch.setattr(c, "ASSETS", ("asset",))
    leases = []
    monkeypatch.setattr(c, "verify_leases", lambda path: leases.append(path))
    fixture = c.Fixture.__new__(c.Fixture)
    fixture.root, fixture.primary, fixture.directory = root, root, directory
    fixture.environment = {}
    fixture.state = {
        "run": "pressure_" + "a" * 32,
        "assets": {"asset": c.sha(root / "asset")},
        "containers": {"schemii": owned},
        "hold": {"nonce": "c" * 64},
    }
    fixture.observation = lambda: baseline
    fixture.save = lambda *args: None
    calls = []

    def runtime(argv, **kwargs):
        assert argv == [
            "docker",
            "container",
            "cp",
            owned["id"] + ":/qualification-state/execution-observation.json",
            "-",
        ]
        assert kwargs["binary"] is True
        calls.append(argv)
        command = json.loads(
            (directory / "execution-input/execution-commands.json").read_text()
        )
        observer = ExecutionObserver(fixture.state["run"], directory, process)
        observer.command = command["command"]
        observer.operations = {
            item["operation"]: {
                **item,
                "request": "declared",
                "unknown": False,
                "receipts": [],
            }
            for item in command["operations"]
        }
        raw = json.dumps(observer.snapshot()).encode()
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w") as writer:
            member = tarfile.TarInfo("execution-observation.json")
            member.size, member.uid, member.mode = len(raw), 10001, 0o600
            writer.addfile(member, io.BytesIO(raw))
        return archive.getvalue()

    fixture.runtime = runtime
    operation = {
        "operation": "d" * 32,
        "actor": "user_owned",
        "resource": "dash_owned",
        "method": "POST",
        "path": "/api/v1/schemer/dashboards/dash_owned/executions/stream",
        "kind": "dashboard-stream",
    }
    result = fixture.execution_channel(
        {"version": 1, "declare": [operation], "acknowledge": []}
    )
    assert result["status"] == "observed" and len(calls) == 1
    assert leases == [root / ".git"]
    assert (
        stat.S_IMODE(
            (directory / "execution-input/execution-commands.json").stat().st_mode
        )
        == 0o640
    )
    assert (
        stat.S_IMODE((directory / "execution-observation.json").stat().st_mode) == 0o600
    )
    assert (
        c.private_read(directory / "execution-observation.json")["observation"][
            "operations"
        ][0]["operation"]
        == operation["operation"]
    )
    with pytest.raises(c.Refused, match="nonce_reused"):
        fixture.execution_channel(
            {"version": 1, "declare": [operation], "acknowledge": []}
        )
    fixture.state["assets"]["asset"] = "changed"
    with pytest.raises(c.Refused, match="source_changed"):
        fixture.execution_channel({"version": 1, "declare": [], "acknowledge": []})
    assert len(calls) == 1


def test_execution_stdin_is_bounded_closed_data_without_paths_or_sql(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(c.sys, "stdin", SimpleNamespace(fileno=lambda: 123))
    monkeypatch.setattr(c.select, "select", lambda *args: ([123], [], []))
    for value in [
        {"version": 1, "declare": [], "acknowledge": []},
        {"version": 1, "declare": [], "acknowledge": [], "sql": "PRIVATE_SQL"},
    ]:
        monkeypatch.setattr(
            c.os, "read", lambda *args: (json.dumps(value) + "\n").encode()
        )
        if "sql" in value:
            with pytest.raises(c.Refused, match="input_schema"):
                c.read_execution_request()
        else:
            assert c.read_execution_request() == value
    monkeypatch.setattr(c.os, "read", lambda *args: b"x" * 65536)
    with pytest.raises(c.Refused, match="input_budget"):
        c.read_execution_request()
    monkeypatch.setattr(c.select, "select", lambda *args: ([], [], []))
    with pytest.raises(c.Refused, match="input_deadline"):
        c.read_execution_request()


def test_execution_input_read_group_survives_restrictive_caller_umask(
    tmp_path, monkeypatch
):
    directory = tmp_path / "run"
    directory.mkdir(mode=0o700)
    fixture = c.Fixture.__new__(c.Fixture)
    fixture.directory = directory
    fixture.secrets = lambda: None

    def stop_before_runtime(*args, **kwargs):
        raise RuntimeError("stop_before_runtime")

    fixture.up = stop_before_runtime
    monkeypatch.setattr(
        c, "command", lambda *args, **kwargs: pytest.fail("No runtime allowed")
    )
    previous = os.umask(0o077)
    try:
        with pytest.raises(RuntimeError, match="stop_before_runtime"):
            fixture.prepare_stack()
    finally:
        os.umask(previous)
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert stat.S_IMODE((directory / "execution-input").stat().st_mode) == 0o750


def container(service, project, run=None, image=None):
    identifier = hashlib.sha256((project + service).encode()).hexdigest()
    limits = {
        "metadata": (268435456, 500000000, 64),
        "source": (268435456, 500000000, 64),
        "schemii": (536870912, 1000000000, 128),
        "ingress": (67108864, 250000000, 32),
        "observer-init": (67108864, 250000000, 16),
    }
    memory, cpu, pids = limits[service]
    return {
        "id": identifier,
        "created": "2026-11-12T00:00:00Z",
        "name": f"/{project}-{service}-1",
        "image": image
        or "sha256:"
        + (
            "a"
            if service in {"schemii", "observer-init"}
            else "b"
            if service == "ingress"
            else "c"
        )
        * 64,
        "state": "exited" if service == "observer-init" else "running",
        "exitCode": 0,
        "oomKilled": False,
        "startedAt": "2026-11-12T00:00:00Z",
        "finishedAt": "2026-11-12T00:00:01Z",
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
            if "rev-parse" in argv:
                return "a" * 40
            if "status" in argv:
                return ""
            if "ls-files" in argv:
                return "testing/load/source_monitor.py\n"
            assert "diff" in argv and "--quiet" in argv
            return ""
        if argv[0] == "node":
            assert argv[1:] == [
                str(self.root / "testing/load/runtime.mjs"),
                "export-fixture",
                kwargs["environment"]["PRESSURE_DIRECTORY"],
            ]
            directory = Path(argv[-1])
            journal = c.private_read(directory / "journal.json")
            assert (
                journal["stage"] == "os-export-intent"
                and journal["osExport"]["status"] == "pending"
            )
            records = journal["osExport"]["records"]
            observed = journal["runtimeReceipt"]["application"]["observation"][
                "process"
            ]
            containers = [
                {
                    "service": record["service"],
                    "id": record["id"],
                    "image": record["image"],
                    "rootProcess": {"pid": record["pid"], "birthTick": "123"},
                    "cgroup": {
                        "path": "/owned/" + record["id"],
                        "device": 1,
                        "inode": record["pid"],
                    },
                }
                for record in records
            ]
            app = next(item for item in containers if item["service"] == "schemii")
            app.update(
                apiProcess={
                    "pid": 201,
                    "birthTick": observed["startTicks"],
                    "namespacePid": observed["pid"],
                },
                initProcess={**app["rootProcess"], "namespacePid": 1},
            )
            c.private_write(
                directory / "os-runtime.json",
                {
                    "version": 1,
                    "origin": c.LOCAL,
                    "revision": "a" * 40,
                    "bootId": "12345678-1234-1234-1234-123456789abc",
                    "topology": {"apiProcesses": 1},
                    "fixture": {
                        "run": journal["run"],
                        "nonce": journal["nonce"],
                        "profile": journal["profile"],
                        "owner": journal["owner"],
                        "sourceAssetsSha256": hashlib.sha256(
                            json.dumps(
                                journal["assets"], sort_keys=True, separators=(",", ":")
                            ).encode()
                        ).hexdigest(),
                    },
                    "containers": containers,
                },
            )
            return '{"status":"exported"}'
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
            if args[3] == c.OS_INSPECT:
                record = self.records[args[-1]]
                assert record["labels"].get(
                    c.RUN_LABEL
                )  # Never inspect a retained/foreign PID.
                pid = {"schemii": 101, "ingress": 102, "metadata": 103, "source": 104}[
                    record["labels"]["com.docker.compose.service"]
                ]
                return json.dumps(
                    {
                        "id": record["id"],
                        "pid": pid,
                        "running": record["state"] == "running",
                        "startedAt": record["startedAt"],
                    }
                )
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
            if record["labels"].get(c.RUN_LABEL):
                record["finishedAt"] = c.now()
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
            services = args[args.index("60") + 1 :] if "60" in args else args[-1:]
            for service in services:
                record = container(service, project, run)
                prefix = "PRESSURE_" + service.upper().replace("-", "_")
                record.update(
                    memory=int(env[prefix + "_MEMORY"]),
                    nanoCpus=int(float(env[prefix + "_CPUS"]) * 1_000_000_000),
                    pids=int(env[prefix + "_PIDS"]),
                )
                self.records[record["id"]] = record
            for kind, names in (
                (
                    "volume",
                    ("metadata", "source", "observer")
                    if any(x in services for x in ("observer-init", "schemii"))
                    else ("metadata", "source"),
                ),
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
    (root / "testing/load").mkdir(parents=True, exist_ok=True)
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
                "profile": self.profile.receipt(),
                "databaseSettings": self.database_settings(),
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
        + 'assert any(x in sys.argv for x in ("--qualify-metadata-pressure", "--recover-metadata-pressure", "--metadata-pressure-attach", "--hold-load-fixture"))\n'
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
            "profile": self.profile.receipt(),
            "databaseSettings": self.database_settings(),
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


def observed_sample(owned, native, *, stage="running", closed=False):
    value = {
        "version": 1,
        "run": owned.state["run"],
        "at": c.now(),
        "startupComplete": True,
        "observationFailed": False,
        "stage": stage,
        "process": {
            "pid": 7,
            "uid": 10001,
            "startTicks": "321",
            "incarnation": "a" * 32,
            "startedAt": native["startedAt"],
        },
    }
    for name, maximum in (("normal", owned.profile.normal_maximum), ("readiness", 2)):
        value[name] = {
            "status": "observed",
            "counts": {
                "maximum": maximum,
                "active": 0 if closed else maximum,
                "peak_active": maximum,
                "closed": closed,
            },
        }
    return {
        "containerId": native["id"],
        "containerStartedAt": native["startedAt"],
        "observation": value,
    }


@pytest.mark.parametrize(
    "exit_code,oom",
    [(137, False), (143, False), (1, False), (0, True), (None, False), (True, False)],
)
def test_native_forced_crash_or_unknown_stop_never_becomes_lifespan_pass(
    setup, monkeypatch, exit_code, oom
):
    owned = fixture(setup)
    owned.up(["schemii"])
    native = owned.state["containers"]["schemii"]
    setup[2].records[native["id"]].update(exitCode=exit_code, oomKilled=oom)
    sample = observed_sample(owned, native)
    calls = []

    def observation(self, **kwargs):
        calls.append(kwargs)
        return sample

    monkeypatch.setattr(c.Fixture, "observation", observation)
    with pytest.raises(c.Refused, match="application_stop_not_graceful"):
        owned.stop_application()
    assert calls == [{}]
    assert (
        len([call for call in setup[2].writes if call[:2] == ["container", "stop"]])
        == 1
    )
    assert owned.state["applicationStop"]["exitCode"] == exit_code
    assert "applicationLifespan" not in owned.state


@pytest.mark.parametrize(
    "defect",
    ["missing-complete", "active", "open-owner", "changed-process", "stale", "unknown"],
)
def test_exit_zero_and_closed_native_backends_require_stock_completion_accounting(
    setup, monkeypatch, defect
):
    owned = fixture(setup)
    owned.up(["schemii"])
    native = owned.state["containers"]["schemii"]
    before = observed_sample(owned, native)
    after = observed_sample(owned, native, stage="shutdown-complete", closed=True)
    if defect == "missing-complete":
        after["observation"]["stage"] = "shutdown-requested"
    elif defect == "active":
        after["observation"]["normal"]["counts"]["active"] = 1
    elif defect == "open-owner":
        after["observation"]["readiness"]["counts"]["closed"] = False
    elif defect == "changed-process":
        after["observation"]["process"]["incarnation"] = "b" * 32
    elif defect == "stale":
        after["observation"]["at"] = "2026-01-01T00:00:00Z"
    else:
        after["observation"]["normal"] = {"status": "unknown"}

    def observation(self, *, stopped=False):
        value = after if stopped else before
        c.validate_observation(
            value["observation"], self.state["run"], native, fresh=not stopped
        )
        return value

    monkeypatch.setattr(c.Fixture, "observation", observation)
    with pytest.raises(c.Refused):
        owned.stop_application()
    assert "applicationStop" in owned.state and "applicationLifespan" not in owned.state


def test_actual_stock_complete_and_closed_owner_receipt_can_confirm_graceful_exit(
    setup, monkeypatch
):
    owned = fixture(setup)
    owned.up(["schemii"])
    native = owned.state["containers"]["schemii"]
    before = observed_sample(owned, native)

    def observation(self, *, stopped=False):
        return (
            observed_sample(self, native, stage="shutdown-complete", closed=True)
            if stopped
            else before
        )

    monkeypatch.setattr(c.Fixture, "observation", observation)
    # Native finishedAt follows the final ASGI send, as Docker records in practice.
    original = owned.exact_state

    def stop(baseline, target):
        result = original(baseline, target)
        result["finishedAt"] = "2099-01-01T00:00:00Z"
        return result

    monkeypatch.setattr(owned, "exact_state", stop)
    result = owned.stop_application()
    assert result["status"] == "observed-stock-lifespan-drained"
    assert result["observation"]["normal"]["counts"]["closed"] is True


def test_observer_copy_is_exact_owned_fixed_path_bounded_and_never_extracts(setup):
    import io
    import tarfile

    owned = fixture(setup)
    owned.up(["schemii"])
    native = owned.state["containers"]["schemii"]
    data = json.dumps(observed_sample(owned, native)["observation"]).encode()
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        member = tarfile.TarInfo("observation.json")
        member.size, member.mode, member.uid = len(data), 0o600, 10001
        archive.addfile(member, io.BytesIO(data))
    original = owned.runtime
    copies = []

    def runtime(argv, **kwargs):
        if argv[:3] == ["docker", "container", "cp"]:
            assert argv[3:] == [
                native["id"] + ":/qualification-state/observation.json",
                "-",
            ]
            assert (
                kwargs["binary"] is True
                and kwargs["limit"] == 32768
                and kwargs["timeout"] == 5
            )
            copies.append(argv)
            return buffer.getvalue()
        return original(argv, **kwargs)

    owned.runtime = runtime
    assert owned.observation()["containerId"] == native["id"]
    assert len(copies) == 1


def held_fixture(setup):
    owned = fixture(setup)
    owned.up(["metadata", "source", "schemii"])
    owned.up(["observer-init"])
    owned.up(["ingress"])
    owned.observation = lambda: fake_observer(owned)
    c.private_write(
        owned.directory / "preparation.json",
        {
            "profile": owned.profile.receipt(),
            "databaseSettings": owned.database_settings(),
        },
    )
    owned.capture_runtime(runtime_api(owned))
    owned.state["hold"] = {
        "nonce": owned.state["nonce"],
        "expiresAt": "2099-01-01T00:00:00Z",
        "attachment": None,
        "requests": 0,
    }
    return owned


def holder_request(owned, action, **extra):
    request = {
        "version": 1,
        "run": owned.state["run"],
        "nonce": owned.state["hold"]["nonce"],
        "requestId": os.urandom(16).hex(),
        "action": action,
        **extra,
    }
    c.private_write(owned.directory / "request.json", request)
    owned.hold_request()
    return c.private_read(owned.directory / "response.json")


def test_attach_heartbeat_and_detach_are_bounded_private_owner_operations(
    setup, monkeypatch
):
    owned = held_fixture(setup)
    identity = {"pid": 1234, "start": "556", "uid": os.getuid(), "argvSha256": "c" * 64}
    monkeypatch.setattr(
        c, "consumer_identity", lambda pid, root: identity if pid == 1234 else None
    )
    writes = len(setup[2].writes)
    result = holder_request(owned, "attach", pid=1234)
    assert result["status"] == "attached"
    attachment = c.private_read(Path(result["attachmentFile"]))
    assert stat.S_IMODE(Path(result["attachmentFile"]).stat().st_mode) == 0o600
    assert attachment["consumer"] == identity
    # Exclusive lifecycle ownership remains with holder; callbacks do not write runtime.
    assert len(setup[2].writes) == writes
    assert holder_request(owned, "attach", pid=1234)["status"] == "rejected"
    assert holder_request(owned, "heartbeat", token="wrong")["status"] == "rejected"
    assert (
        holder_request(owned, "heartbeat", token=attachment["token"])["status"]
        == "attached"
    )
    joined = []
    monkeypatch.setattr(c, "join_consumer", joined.append)
    assert (
        holder_request(owned, "detach", token=attachment["token"])["status"]
        == "detaching"
    )
    assert joined == []  # Ack is observable before group termination.
    assert owned.state["hold"]["attachment"]["status"] == "detaching"
    owned.finish_consumers()
    assert joined == [identity] and owned.state["hold"]["attachment"] is None


def test_unknown_consumer_identity_prevents_all_cleanup_and_ingress_restore(
    setup, monkeypatch
):
    owned = held_fixture(setup)
    owned.stop_retained()
    owned.state["hold"]["attachment"] = {"consumer": {"pid": 1234, "start": "556"}}
    writes = deepcopy(setup[2].writes)

    def unknown(_identity):
        raise c.Refused("consumer_join_identity_unknown")

    monkeypatch.setattr(c, "join_consumer", unknown)
    with pytest.raises(c.Refused, match="consumer_join_identity_unknown"):
        owned.cleanup()
    assert setup[2].writes == writes
    assert not owned.state.get("restored")
    assert c.private_read(owned.journal)["stage"] == "consumer-join-intent"


def test_consumer_group_join_rejects_reused_leader_before_signalling(monkeypatch):
    monkeypatch.setattr(c, "group_members", lambda pid: [{"pid": pid, "start": "new"}])
    monkeypatch.setattr(c, "process_identity", lambda pid: {"pid": pid, "start": "new"})
    signals = []
    monkeypatch.setattr(c.os, "killpg", lambda *args: signals.append(args))
    with pytest.raises(c.Refused, match="consumer_join_identity_unknown"):
        c.join_consumer({"pid": 1234, "start": "old"})
    assert signals == []


def test_consumer_group_join_signals_only_exact_opted_in_group(monkeypatch):
    identity = {"pid": 1234, "start": "556"}
    states = iter([[identity], [identity], [], [], []])
    monkeypatch.setattr(c, "group_members", lambda pid: next(states))
    monkeypatch.setattr(c, "process_identity", lambda pid: identity)
    signals = []
    monkeypatch.setattr(c.os, "killpg", lambda *args: signals.append(args))
    c.join_consumer(identity)
    assert signals == [(1234, c.signal.SIGTERM)]


@pytest.mark.parametrize("seconds", [0, 59, 3601, -1, True])
def test_hold_duration_refuses_before_any_runtime_write(setup, seconds):
    owned = fixture(setup)
    with pytest.raises(c.Refused, match="hold_duration_bound"):
        owned.hold(seconds)
    assert setup[2].writes == []


def test_hold_expiry_joins_consumer_before_any_restore(setup, monkeypatch):
    owned = held_fixture(setup)
    c.private_write(
        owned.directory / "principal.json",
        {"username": "private", "userId": "own", "passwordFile": "own"},
    )
    monkeypatch.setattr(owned, "prepare_stack", lambda: None)
    owned.state["endpoints"] = {"source": "172.30.0.3"}
    monkeypatch.setattr(
        owned,
        "probe",
        lambda phase: {"phase": "bootstrapped", "principal": {"username": "own"}},
    )
    monkeypatch.setattr(owned, "capture_runtime", lambda api: None)
    clock = iter([0, 61])
    monkeypatch.setattr(c.time, "monotonic", lambda: next(clock))
    order = []
    monkeypatch.setattr(owned, "finish_consumers", lambda: order.append("join"))
    monkeypatch.setattr(owned, "cleanup", lambda: order.append("restore"))
    owned.hold(60)
    assert order == ["join", "restore"]
    assert c.private_read(owned.directory / "authority.json")["status"] == "closing"


def test_callback_dispatch_never_acquires_canonical_lease_or_checks_runtime(tmp_path):
    import subprocess

    root, common, calls, env = shell_fixture(tmp_path)
    # Fake controller records dispatch only; Docker tripwire remains untouched.
    result = subprocess.run(
        [str(root / "start.sh"), "--metadata-pressure-attach", "/private/run", "1234"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    events = list(map(json.loads, calls.read_text().splitlines()))
    assert [event[0] for event in events] == ["git", "git", "python3"]
    assert events[-1][-3:] == ["--metadata-pressure-attach", "/private/run", "1234"]
    assert (
        not (common / "qa-deployment.lock").exists()
        and not (common / "qa-startup.lock").exists()
    )


def test_missing_or_expired_holder_authority_refuses_before_ipc_or_runtime(setup):
    owned = held_fixture(setup)
    c.private_write(
        owned.directory / "principal.json", {"username": "own", "userId": "own"}
    )
    owned.state["hold"]["expiresAt"] = "2000-01-01T00:00:00Z"
    owned.save("held")
    owned.authority("held")
    writes = deepcopy(setup[2].writes)
    with pytest.raises(c.Refused, match="holder_authority_expired"):
        c.attachment_request(
            owned.root, owned.primary, owned.directory, "attach", "1234"
        )
    assert not (owned.directory / "request.json").exists()
    assert writes == setup[2].writes


def test_old_consumer_source_is_denied_even_with_known_entrypoint_same_repository(
    setup, monkeypatch
):
    root = setup[0]
    script = root / "testing/load/controller.mjs"
    script.write_text("// old controller may detach workload children\n")
    identity = {"pid": 1234, "start": "556"}
    monkeypatch.setattr(c, "process_identity", lambda pid: identity)
    import io

    original = Path.open
    argv = ("node\0" + str(script) + "\0load-example123\0").encode()
    monkeypatch.setattr(
        Path,
        "open",
        lambda path, *args, **kwargs: (
            io.BytesIO(argv)
            if str(path) == "/proc/1234/cmdline"
            else original(path, *args, **kwargs)
        ),
    )
    monkeypatch.setattr(c.os, "readlink", lambda path: "/usr/bin/node")
    monkeypatch.setattr(
        c,
        "command",
        lambda argv, **kwargs: "/fake/common" if "rev-parse" in argv else "",
    )
    with pytest.raises(c.Refused, match="reviewed_consumer_source_required"):
        c.consumer_identity(1234, root)
    fingerprint = hashlib.sha256()
    for path in sorted((root / "testing/load").glob("*")):
        fingerprint.update(
            path.relative_to(root).as_posix().encode()
            + b"\0"
            + hashlib.sha256(path.read_bytes()).digest()
        )
    monkeypatch.setattr(c, "REQUIRED_CONSUMER_TREE_SHA256", fingerprint.hexdigest())
    assert c.consumer_identity(1234, root) == identity
    script.write_text("// changed child-spawn behavior after review\n")
    with pytest.raises(c.Refused, match="reviewed_consumer_source_required"):
        c.consumer_identity(1234, root)


def test_initializer_waits_for_exact_one_shot_exit_without_service_running_wait(setup):
    owned = fixture(setup)
    owned.up(["observer-init"])
    call = setup[2].writes[-1]
    assert call[-4:] == ["up", "--detach", "--no-deps", "observer-init"]
    assert "--wait" not in call
    assert owned.state["containers"]["observer-init"]["state"] == "exited"
    assert owned.state["containers"]["observer-init"]["exitCode"] == 0


def profile_fixture(setup, profile):
    return c.begin(setup[0], setup[1], None, setup[2], profile_name=profile.name)


@pytest.mark.parametrize(
    "invalid",
    [None, True, "load", "pressure", "representative-load-v2", "pressure-v1 ", {}],
)
def test_invalid_profile_is_rejected_before_runtime_access_or_private_intent(
    setup, invalid
):
    with pytest.raises(c.Refused, match="fixed_fixture_profile_required"):
        c.begin(setup[0], setup[1], None, setup[2], profile_name=invalid)
    assert setup[2].calls == setup[2].writes == []
    assert not (setup[1] / ".schemii").exists()


@pytest.mark.parametrize("profile", c.PROFILES, ids=lambda profile: profile.name)
def test_fixed_profile_owns_exact_allocations_and_overrides_ambient_budget_inputs(
    setup, monkeypatch, profile
):
    monkeypatch.setenv("PRESSURE_SCHEMII_MEMORY", "1")
    monkeypatch.setenv("PRESSURE_METADATA_MAXIMUM", "999")
    owned = profile_fixture(setup, profile)
    assert c.private_read(owned.journal)["profile"] == profile.receipt()
    assert owned.environment["PRESSURE_METADATA_MAXIMUM"] == str(profile.normal_maximum)
    owned.up(["metadata", "source"])
    owned.up(["observer-init"])
    owned.up(["schemii", "ingress"])
    for name, record in owned.state["containers"].items():
        expected = profile.receipt()["allocations"][name]
        assert (record["memory"], record["nanoCpus"], record["pids"]) == tuple(
            expected.values()
        )
    app = owned.state["containers"]["schemii"]
    setup[2].records[app["id"]]["memory"] += 1
    with pytest.raises(c.Refused, match="disposable_runtime_limits"):
        owned.inventory()


def test_profile_values_are_immutable_and_load_budgets_are_distinct_stock_defaults():
    from dataclasses import FrozenInstanceError

    assert tuple(profile.name for profile in c.PROFILES) == (
        "pressure-v1",
        "representative-load-v1",
    )
    with pytest.raises(FrozenInstanceError):
        c.LOAD_PROFILE.normal_maximum = 2
    altered = c.LOAD_PROFILE.receipt()
    altered["metadata"]["normalMaximum"] = 2
    with pytest.raises(c.Refused, match="profile_receipt_changed"):
        c.bound_profile(altered)
    assert c.LOAD_PROFILE.receipt()["allocations"] == {
        "schemii": {"memoryBytes": 2147483648, "nanoCpus": 2000000000, "pids": 256},
        "metadata": {"memoryBytes": 1073741824, "nanoCpus": 1000000000, "pids": 256},
        "source": {"memoryBytes": 2147483648, "nanoCpus": 2000000000, "pids": 256},
        "ingress": {"memoryBytes": 134217728, "nanoCpus": 500000000, "pids": 64},
        "observer-init": {"memoryBytes": 67108864, "nanoCpus": 250000000, "pids": 16},
    }
    assert (
        c.LOAD_PROFILE.normal_maximum == 16 and c.PRESSURE_PROFILE.normal_maximum == 2
    )
    assert (
        c.LOAD_PROFILE.database_maximum == 100
        and c.PRESSURE_PROFILE.database_maximum == 24
    )
    assert (
        c.LOAD_PROFILE.receipt()["metadata"]["readinessMaximum"]
        == c.PRESSURE_PROFILE.receipt()["metadata"]["readinessMaximum"]
        == 2
    )
    assert not c.LOAD_PROFILE.receipt()["stockCapacityEligible"]


def runtime_api(owned):
    return {
        "version": 1,
        "processPid": 7,
        "sources": {
            "metadata": {
                "status": "available",
                "counts": {"maximum_connections": owned.profile.normal_maximum},
            },
            "source": {
                "status": "available",
                "counts": {"ordinary": {"maximum": 20, "maximumPerProfileRevision": 4}},
            },
        },
    }


def fake_observer(owned):
    owned.inventory()
    native = owned.state["containers"]["schemii"]
    result = observed_sample(owned, native)
    c.validate_observation(
        result["observation"],
        owned.state["run"],
        native,
        fresh=True,
        profile=owned.profile,
    )
    return result


def prepare_fake_runtime_receipt(owned, monkeypatch):
    owned.up(["metadata", "source"])
    owned.up(["observer-init"])
    owned.up(["schemii", "ingress"])
    monkeypatch.setattr(owned, "observation", lambda: fake_observer(owned))
    c.private_write(
        owned.directory / "preparation.json",
        {
            "profile": owned.profile.receipt(),
            "databaseSettings": owned.database_settings(),
        },
    )


@pytest.mark.parametrize("profile", c.PROFILES, ids=lambda profile: profile.name)
def test_authority_runtime_receipt_binds_profile_to_actual_limits_and_owner_budgets(
    setup, monkeypatch, profile
):
    owned = profile_fixture(setup, profile)
    prepare_fake_runtime_receipt(owned, monkeypatch)
    receipt = owned.capture_runtime(runtime_api(owned))
    assert receipt["profile"] == profile.receipt()
    assert receipt["metadata"] == {
        "normalMaximum": profile.normal_maximum,
        "readinessMaximum": 2,
    }
    assert receipt["databaseSettings"] == owned.database_settings()
    assert receipt["apiProcesses"] == 1 and receipt["capacityAcceptance"] is False
    assert (
        receipt["instrumentationOverheadMeasured"] is False
        and receipt["stockCapacityEligible"] is False
    )
    assert c.private_read(owned.directory / "runtime.json") == receipt
    owned.state["hold"] = {
        "nonce": owned.state["nonce"],
        "expiresAt": "2099-01-01T00:00:00Z",
        "attachment": None,
    }
    c.private_write(
        owned.directory / "principal.json", {"username": "own", "userId": "own"}
    )
    owned.save("held")
    authority = owned.authority("held")
    assert (
        authority["runtimeReceipt"] == receipt
        and authority["profile"] == profile.receipt()
    )
    authority["profile"] = (
        c.LOAD_PROFILE.receipt()
        if profile is c.PRESSURE_PROFILE
        else c.PRESSURE_PROFILE.receipt()
    )
    c.private_write(owned.directory / "authority.json", authority)
    with pytest.raises(c.Refused, match="authority_profile_changed"):
        c.attachment_request(
            owned.root, owned.primary, owned.directory, "attach", "1234"
        )


@pytest.mark.parametrize(
    "defect",
    ["metadata", "source", "source-identity", "process", "database", "profile"],
)
def test_runtime_receipt_never_adopts_declared_or_api_budget_drift(
    setup, monkeypatch, defect
):
    owned = profile_fixture(setup, c.LOAD_PROFILE)
    prepare_fake_runtime_receipt(owned, monkeypatch)
    api = runtime_api(owned)
    if defect == "metadata":
        api["sources"]["metadata"]["counts"]["maximum_connections"] = 2
    elif defect == "source":
        api["sources"]["source"]["counts"]["ordinary"]["maximum"] = 21
    elif defect == "source-identity":
        api["sources"]["source"]["counts"]["ordinary"]["maximumPerProfileRevision"] = 5
    elif defect == "process":
        api["processPid"] = 8
    else:
        preparation = c.private_read(owned.directory / "preparation.json")
        if defect == "database":
            preparation["databaseSettings"]["source"]["maximumConnections"] = 24
        else:
            preparation["profile"] = c.PRESSURE_PROFILE.receipt()
        c.private_write(owned.directory / "preparation.json", preparation)
    with pytest.raises(c.Refused):
        owned.capture_runtime(api)
    assert (
        "runtimeReceipt" not in owned.state
        and not (owned.directory / "runtime.json").exists()
    )


def test_load_profile_cannot_run_pressure_qualification_before_any_mutation(setup):
    owned = profile_fixture(setup, c.LOAD_PROFILE)
    with pytest.raises(c.Refused, match="pressure_qualification_profile_required"):
        owned.run()
    assert setup[2].writes == []


def test_load_1800_second_hold_uses_guarded_cleanup_and_restores_only_exact_retained_ingress(
    setup, monkeypatch
):
    owned = profile_fixture(setup, c.LOAD_PROFILE)
    baseline = deepcopy(setup[2].records)
    monkeypatch.setattr(owned, "secrets", lambda: owned.intent("fake-secrets"))

    def probe(phase):
        if phase == "prepare":
            return {
                "phase": "prepared",
                "profile": owned.profile.receipt(),
                "databaseSettings": owned.database_settings(),
                "sourceBaseline": {"ownedBackends": 0, "releaseVerified": False},
            }
        assert phase == "bootstrap"
        principal = {"username": "own", "userId": "own", "passwordFile": "own"}
        c.private_write(owned.directory / "principal.json", principal)
        return {
            "phase": "bootstrapped",
            "principal": principal,
            "runtimeObservation": runtime_api(owned),
        }

    monkeypatch.setattr(owned, "probe", probe)
    monkeypatch.setattr(owned, "observation", lambda: fake_observer(owned))
    # Advance the test clock only after the durable holder start, regardless of
    # read-only preflight/initializer deadline observations.
    monkeypatch.setattr(
        c.time, "monotonic", lambda: 1801 if "hold" in owned.state else 0
    )
    owned.hold(1800)
    assert owned.state["cleanup"] is owned.state["restored"] is True
    assert setup[2].records == baseline
    assert owned.state["runtimeReceipt"]["profile"]["name"] == c.LOAD_PROFILE.name
    assert c.private_read(owned.directory / "authority.json")["status"] == "closing"


def test_load_hold_dispatch_uses_canonical_fake_leases_and_rejects_tuning_flags(
    tmp_path,
):
    import subprocess

    root, _, log, environment = shell_fixture(tmp_path)
    result = subprocess.run(
        [str(root / "start.sh"), "--hold-load-fixture", "3600"],
        env=environment,
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 0, result.stderr
    events = list(map(json.loads, log.read_text().splitlines()))
    assert [event[0] for event in events] == ["git", "git", "docker", "python3"]
    assert events[-1][-3:] == ["--", "--hold-load-fixture", "3600"]
    before = log.read_text()
    result = subprocess.run(
        [str(root / "start.sh"), "--hold-load-fixture", "3600", "--memory", "1"],
        env=environment,
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 1 and log.read_text() == before


@pytest.mark.parametrize(
    "field,value", [("version", True), ("apiProcesses", 1.0), ("version", float("nan"))]
)
def test_profile_binding_rejects_json_scalar_lookalikes(field, value):
    profile = c.LOAD_PROFILE.receipt()
    profile[field] = value
    with pytest.raises(c.Refused, match="profile_receipt_changed"):
        c.bound_profile(profile)


def test_python_and_node_os_export_pin_the_same_two_immutable_profile_contracts():
    import re

    source = (
        Path(__file__).resolve().parents[1] / "testing/load/runtime.mjs"
    ).read_text()
    encoded = re.search(r"const fixtureProfileDigests = (\{[^;]+\});", source)[1]
    expected = {
        profile.name: hashlib.sha256(
            json.dumps(
                profile.receipt(), sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()
        for profile in c.PROFILES
    }
    assert json.loads(encoded) == expected


@pytest.mark.parametrize(
    "defect",
    [
        "missing-api",
        "wrong-init",
        "wrong-source",
        "unknown-boot",
        "uncertain-command",
        "pid-reused",
    ],
)
def test_missing_or_changed_os_proof_never_publishes_authority_or_replays_export(
    setup, monkeypatch, defect
):
    owned = profile_fixture(setup, c.LOAD_PROFILE)
    prepare_fake_runtime_receipt(owned, monkeypatch)
    original = owned.runtime
    node_calls = []
    changed = False

    def runtime(argv, **kwargs):
        nonlocal changed
        result = original(argv, **kwargs)
        if argv[0] == "node":
            node_calls.append(argv)
            if defect == "uncertain-command":
                raise c.Refused("command_failed")
            if defect == "pid-reused":
                changed = True
            receipt_path = owned.directory / "os-runtime.json"
            receipt = c.private_read(receipt_path)
            if defect == "missing-api":
                receipt["containers"][0].pop("apiProcess")
            elif defect == "wrong-init":
                receipt["containers"][0]["initProcess"]["birthTick"] = "wrong"
            elif defect == "wrong-source":
                receipt["fixture"]["sourceAssetsSha256"] = "0" * 64
            elif defect == "unknown-boot":
                receipt["bootId"] = None
            c.private_write(receipt_path, receipt)
        elif (
            changed
            and argv[:3] == ["docker", "container", "inspect"]
            and argv[4] == c.OS_INSPECT
        ):
            value = json.loads(result)
            value["pid"] += 1
            result = json.dumps(value)
        return result

    owned.runtime = runtime
    with pytest.raises(c.Refused):
        owned.capture_runtime(runtime_api(owned))
    assert len(node_calls) == 1
    assert owned.state["osExport"]["status"] == "pending"
    assert "osRuntimeReceiptFile" not in owned.state
    assert not (owned.directory / "authority.json").exists()
    with pytest.raises(c.Refused, match="new_os_export_required"):
        owned.export_os_runtime()
    assert len(node_calls) == 1


def test_os_pid_inspection_refuses_unknown_owner_before_reading_any_retained_or_foreign_pid(
    setup,
):
    owned = profile_fixture(setup, c.LOAD_PROFILE)
    owned.up(["metadata", "source"])
    owned.up(["observer-init"])
    owned.up(["schemii", "ingress"])
    setup[2].records[owned.state["containers"]["source"]["id"]]["labels"][
        c.RUN_LABEL
    ] = "foreign"
    calls = len(setup[2].calls)
    with pytest.raises(c.Refused, match="disposable_container_owner"):
        owned.os_records()
    assert all(c.OS_INSPECT not in call for call in setup[2].calls[calls:])


def test_node_process_roles_match_the_actual_fixed_compose_wrapper_command():
    import ast
    import csv
    import re

    root = Path(__file__).resolve().parents[1]
    compose = (root / "dev/pressure/compose.yaml").read_text()
    command = next(
        csv.reader(
            [re.search(r"command: \[(uvicorn, pressure_app[^\]]+)\]", compose)[1]],
            skipinitialspace=True,
        )
    )
    helper = (root / "testing/load/runtime.mjs").read_text()
    expected = ast.literal_eval(
        re.search(r"const fixtureArgs = (\[[^\]]+\]);", helper)[1]
    )
    assert command == ["uvicorn", *expected]


def test_held_authority_refuses_missing_or_changed_os_receipt(setup):
    owned = held_fixture(setup)
    owned.state["osExport"]["status"] = "pending"
    with pytest.raises(c.Refused, match="os_authority_receipt_required"):
        owned.authority("held")
    owned.state["osExport"]["status"] = "complete"
    path = owned.directory / "os-runtime.json"
    receipt = c.private_read(path)
    receipt["bootId"] = "changed"
    c.private_write(path, receipt)
    with pytest.raises(c.Refused, match="os_authority_receipt_changed"):
        owned.authority("held")
    assert not (owned.directory / "authority.json").exists()
