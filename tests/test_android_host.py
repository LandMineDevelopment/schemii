"""Native ownership controls using only disposable sleep/process fixtures."""

import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dev/android"))
SPEC = importlib.util.spec_from_file_location(
    "android_native_host", ROOT / "dev/android/host.py"
)
host_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(host_module)


def wait_for(predicate, seconds=5):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.02)
    pytest.fail("Disposable native lifecycle condition timed out")


@pytest.fixture
def native_stack(tmp_path):
    home = tmp_path / "home"
    source = home / ".local/share/schemii/releases/android-v0.1.0-preview.9"
    (source / "dev/android/bin").mkdir(parents=True)
    prefix = tmp_path / "prefix"
    (prefix / "bin").mkdir(parents=True)
    (prefix / "bin/python").symlink_to(sys.executable)
    (prefix / "etc/tls").mkdir(parents=True)
    (prefix / "etc/tls/cert.pem").write_text("test-only public CA placeholder")
    binary = source / "dev/android/bin/schemii-tailnet-proxy"
    shutil.copy2(shutil.which("sleep"), binary)
    control = home / ".local/share/schemii-android"
    host = host_module.Host(source, prefix, control)
    host.runtime._prepare()
    host.runtime.configure("100.64.2.3", "phone.example.ts.net")
    host.runtime.configure_tailnet("schemii-phone")
    guest = source / "guest-fixture.py"
    guest.write_text(f"""
import json,os,signal,subprocess,sys,time
from pathlib import Path
sys.path.insert(0,{str(ROOT / "dev/android")!r})
from runtime import process_identity,is_current,read_json,write_private,BASE_SERVICES
state=Path({str(host.state)!r}); action=sys.argv[1]
status=state/'run/status.json'
if action=='prepare-tailnet':
 print('{{}}');raise SystemExit(0)
if action=='status':
 value=read_json(status);value['ready']=bool(value.get('ready') and is_current(value.get('supervisor')))
 print(json.dumps(value));raise SystemExit(0)
if action=='assert-stopped':
 value=read_json(status)
 if is_current(value.get('supervisor')):raise SystemExit(1)
 print('{{}}');raise SystemExit(0)
if action=='stop':
 if (Path({str(source)!r})/'fail-stop').exists():raise SystemExit(7)
 value=read_json(status);owner=value.get('supervisor')
 if is_current(owner):
  os.kill(owner['pid'],signal.SIGTERM)
  deadline=time.monotonic()+5
  while is_current(owner) and time.monotonic()<deadline:time.sleep(.02)
 print('{{}}');raise SystemExit(0)
if (Path({str(source)!r})/'fail-guest').exists():raise SystemExit(9)
stopping=False
def stop(*args):
 global stopping;stopping=True
signal.signal(signal.SIGTERM,stop)
children={{name:subprocess.Popen([{str(binary)!r},'60'],start_new_session=True) for name in BASE_SERVICES}}
write_private(status,json.dumps({{'state':'ready','ready':True,'supervisor':process_identity(os.getpid()),'services':{{name:process_identity(p.pid) for name,p in children.items()}}}}))
try:
 while not stopping:
  record=read_json(state/'run/native-tailnet.json')
  if not (Path({str(source)!r})/'fail-stop').exists() and (not is_current(record.get('owner')) or not is_current(record.get('proxy'))):break
  time.sleep(.02)
finally:
 for p in children.values():
  p.terminate();p.wait(timeout=3)
 write_private(status,json.dumps({{'state':'stopped','ready':False,'services':{{}}}}))
""")
    driver = source / "dev/android/host.py"
    host.guest_command = lambda action: [sys.executable, str(guest), action]
    driver.write_text(f"""
import json,sys,time
from pathlib import Path
sys.path.insert(0,{str(ROOT / "dev/android")!r})
from host import Host
from runtime import write_private
class Fixture(Host):
 def guest_command(self,action):return [sys.executable,{str(guest)!r},action]
 def proxy_command(self):return [{str(binary)!r},'60']
 def _wait_proxy(self):
  write_private(self.state/'run/tailnet.json',json.dumps({{'pid':self.proxy.pid,'online':False,'backend_state':'NeedsLogin','login_url':'https://login.tailscale.com/a/test-only-private'}}))
  self._record('login_pending')
  while not (self.source/'approve').exists() and not self.stop_event.wait(.02):pass
  if self.stop_event.is_set():return
  write_private(self.state/'run/tailnet.json',json.dumps({{'pid':self.proxy.pid,'online':True,'ready':True,'hostname':'schemii-phone.example.ts.net','origin':'https://schemii-phone.example.ts.net','tailnet_ips':['100.64.2.4']}}))
  return super()._wait_proxy()
Fixture(Path({str(source)!r}),Path({str(prefix)!r}),Path({str(control)!r})).run()
""")
    import hashlib

    (source / "ANDROID_RELEASE.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "tag": source.name,
                "experimental": True,
                "files": {
                    "dev/android/host.py": {
                        "sha256": hashlib.sha256(driver.read_bytes()).hexdigest()
                    }
                },
            }
        )
    )
    process = subprocess.Popen(
        [sys.executable, str(driver), "run"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        wait_for(
            lambda: (
                host_module.read_json(control / "host.json").get("state")
                == "login_pending"
            )
        )
        yield host, process
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=12)
        record = host_module.read_json(control / "host.json")
        for key in ("proxy", "guest"):
            identity = record.get(key)
            if host_module.owns_group(identity):
                os.killpg(identity["pid"], signal.SIGKILL)
        process.communicate()


def approve(host):
    (host.source / "approve").touch()
    return wait_for(lambda: host.status() if host.status().get("ready") else None)


def test_native_pending_private_login_then_complete_stack_stop(native_stack):
    host, process = native_stack
    pending = host.status()
    assert pending["state"] == "login_pending" and not pending["ready"]
    assert "login.tailscale.com" not in json.dumps(pending)
    assert host.status(login=True)["tailnet"]["login_url"].endswith("test-only-private")
    ready = approve(host)
    assert set(ready["services"]) == {
        "metadata",
        "target",
        "pi",
        "app",
        "ingress",
        "tailnet",
    }
    identities = list(ready["services"].values())
    assert all(host_module.is_current(identity) for identity in identities)
    assert (
        host.runtime._native_tailnet_identity()["pid"]
        == ready["services"]["tailnet"]["pid"]
    )
    assert host.stop() == {"state": "stopped", "ready": False}
    assert process.wait(timeout=3) == 0
    assert not any(host_module.is_current(identity) for identity in identities)


def test_duplicate_owner_never_replaces_running_stack(native_stack):
    host, process = native_stack
    duplicate = subprocess.run(
        [sys.executable, str(host.source / "dev/android/host.py"), "run"],
        capture_output=True,
        timeout=5,
    )
    assert duplicate.returncode != 0
    assert (
        host_module.read_json(host.control / "host.json")["owner"]["pid"] == process.pid
    )
    assert process.poll() is None


def test_new_release_controller_stops_verified_previous_release(native_stack):
    host, process = native_stack
    ready = approve(host)
    next_source = host.source.with_name("android-v0.1.0-preview.10")
    next_source.mkdir()
    replacement = host_module.Host(next_source, host.prefix, host.control)
    assert replacement.verified_owner(host_module.read_json(host.control / "host.json"))
    assert replacement.stop()["state"] == "stopped"
    assert process.wait(timeout=3) == 0
    assert not any(
        host_module.is_current(value) for value in ready["services"].values()
    )


def test_failed_guest_stop_preserves_owned_guest_groups_but_stops_proxy(native_stack):
    host, process = native_stack
    ready = approve(host)
    flag = host.source / "fail-stop"
    flag.touch()
    try:
        with pytest.raises(host_module.RuntimeFailure, match="remains active"):
            host.stop()
        assert process.wait(timeout=3) != 0
        assert host_module.read_json(host.control / "host.json")["state"] == "failed"
        assert not host_module.is_current(ready["services"]["tailnet"])
        assert all(
            host_module.is_current(value)
            for name, value in ready["services"].items()
            if name != "tailnet"
        )
    finally:
        flag.unlink(missing_ok=True)
        host._call("stop")


def test_guest_failure_stops_native_proxy(native_stack):
    host, process = native_stack
    proxy = host_module.read_json(host.control / "host.json")["proxy"]
    (host.source / "fail-guest").touch()
    (host.source / "approve").touch()
    assert process.wait(timeout=10) != 0
    assert not host_module.is_current(proxy)
    assert host_module.read_json(host.control / "host.json")["state"] == "failed"
    assert host.stop()["state"] == "stopped"


def test_native_owner_sigkill_does_not_orphan_proxy_or_guest_services(native_stack):
    host, process = native_stack
    ready = approve(host)
    identities = list(ready["services"].values())
    guest = host_module.read_json(host.control / "host.json")["guest"]
    process.kill()
    process.wait(timeout=3)
    wait_for(
        lambda: (
            not any(
                host_module.is_current(identity) for identity in [guest, *identities]
            )
        )
    )
    assert host.status()["ready"] is False


def test_native_receipt_cannot_adopt_an_unrelated_process(native_stack):
    host, _process = native_stack
    ready = approve(host)
    path = host.state / "run/native-tailnet.json"
    original = path.read_bytes()
    foreign = subprocess.Popen(
        [str(host.source / "dev/android/bin/schemii-tailnet-proxy"), "60"],
        start_new_session=True,
    )
    try:
        value = json.loads(original)
        value["proxy"] = host_module.process_identity(foreign.pid)
        host_module.write_private(path, json.dumps(value))
        assert host.runtime._native_tailnet_identity() is None
        assert foreign.poll() is None and ready["ready"]
    finally:
        path.write_bytes(original)
        foreign.terminate()
        foreign.wait(timeout=3)


def test_unverified_owner_is_never_signalled(native_stack):
    host, process = native_stack
    path = host.control / "host.json"
    original = path.read_bytes()
    foreign = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        value = json.loads(original)
        value["owner"] = host_module.process_identity(foreign.pid)
        host_module.write_private(path, json.dumps(value))
        with pytest.raises(host_module.RuntimeFailure, match="unverified"):
            host.stop()
        assert foreign.poll() is None and process.poll() is None
    finally:
        path.write_bytes(original)
        foreign.terminate()
        foreign.wait(timeout=3)


def test_native_proxy_exit_stops_guest_service_groups(native_stack):
    host, process = native_stack
    ready = approve(host)
    identities = list(ready["services"].values())
    os.kill(ready["services"]["tailnet"]["pid"], signal.SIGTERM)
    assert process.wait(timeout=10) != 0
    assert not any(host_module.is_current(identity) for identity in identities)


def test_native_restart_waits_for_stop_and_preserves_state(native_stack):
    host, process = native_stack
    first = approve(host)
    state = host.state / "tailnet/node/tailscaled.state"
    state.parent.mkdir(mode=0o700, exist_ok=True)
    state.write_text("fixture-retained-node")
    host.stop()
    process.wait(timeout=3)
    replacement = subprocess.Popen(
        [sys.executable, str(host.source / "dev/android/host.py"), "run"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        second = wait_for(
            lambda: value if (value := host.status()).get("ready") else None
        )
        assert (
            second["services"]["tailnet"]["pid"] != first["services"]["tailnet"]["pid"]
        )
        assert state.read_text() == "fixture-retained-node"
        host.stop()
        assert replacement.wait(timeout=3) == 0
    finally:
        if replacement.poll() is None:
            replacement.terminate()
            replacement.wait(timeout=12)
        replacement.communicate()


def test_native_invocation_uses_public_termux_roots_and_real_state(
    tmp_path, monkeypatch
):
    prefix = tmp_path / "prefix"
    source = tmp_path / "source"
    source.mkdir()
    control = tmp_path / "home/.local/share/schemii-android"
    host = host_module.Host(source, prefix, control)
    host.runtime._prepare()
    host_module.private_directory(host.state / "tailnet")
    host_module.write_private(
        host.state / "tailnet/config.json", '{"hostname":"schemii-phone"}'
    )
    command = host.proxy_command()
    assert command[command.index("--state-dir") + 1] == str(host.state / "tailnet/node")
    assert command[command.index("--upstream-ca") + 1] == str(host.state / "tls/ca.crt")
    assert command[-2:] == ["--dns-server", "100.100.100.100:53"]
    guest = host.guest_command("run")
    assert "PROOT_DONT_SHARE_LIBANDROID_SHMEM=1" in guest
    assert f"{source}:/opt/schemii" in guest
    ca = prefix / "etc/tls/cert.pem"
    ca.parent.mkdir(parents=True)
    ca.write_text("test-only public roots")
    monkeypatch.setenv("TS_AUTHKEY", "test-only-do-not-inherit")
    monkeypatch.setenv("SSL_CERT_DIR", "/private/application/tls")
    captured = {}
    original = subprocess.Popen

    def spawn(command, **kwargs):
        environment = kwargs["env"]
        captured.update(
            ca=environment["SSL_CERT_FILE"],
            prefix=environment["PREFIX"],
            authkey_present="TS_AUTHKEY" in environment,
            cert_dir_present="SSL_CERT_DIR" in environment,
        )
        return original(command, **kwargs)

    monkeypatch.setattr(host_module.subprocess, "Popen", spawn)
    monkeypatch.setattr(host, "proxy_command", lambda: [shutil.which("sleep"), "60"])
    try:
        host._spawn_proxy()
        assert captured == {
            "ca": str(ca),
            "prefix": str(prefix),
            "authkey_present": False,
            "cert_dir_present": False,
        }
        assert host_module.is_current(host.proxy_identity)
    finally:
        host._cleanup()


@pytest.mark.parametrize("cluster", ["metadata", "target"])
@pytest.mark.parametrize(
    "lock_case",
    [
        "owned",
        "unknown",
        "foreign-path",
        "physical-path",
        "live",
        "symlink",
        "directory-symlink",
        "alias",
    ],
)
def test_native_start_checks_retained_postgres_locks_in_guest_namespace(
    tmp_path, monkeypatch, cluster, lock_case
):
    import runtime as guest_module

    monkeypatch.chdir(tmp_path)
    host = host_module.Host(tmp_path, tmp_path / "prefix", tmp_path / "control")
    host.runtime._prepare()
    host.runtime.configure("100.64.2.3", "phone.example.ts.net")
    host_module.write_private(
        host.state / "tailnet/config.json", '{"hostname":"schemii-phone"}'
    )
    guest_state = guest_module.DEFAULT_STATE
    directory = host.state / "postgres" / cluster
    directory.mkdir()
    guest_directory = guest_state / "postgres" / cluster
    lock_directory = {
        "foreign-path": "/other/postgres/metadata",
        "physical-path": str(directory),
        "alias": f"{guest_directory}/../{cluster}",
    }.get(lock_case, str(guest_directory))
    pidfile = directory / "postmaster.pid"
    contents = f"123456\n{lock_directory}\n"
    if lock_case == "symlink":
        outside = tmp_path / "foreign.pid"
        outside.write_text(contents)
        pidfile.symlink_to(outside)
    else:
        pidfile.write_text(contents)
    if lock_case == "directory-symlink":
        outside = tmp_path / "foreign-cluster"
        directory.rename(outside)
        directory.symlink_to(outside, target_is_directory=True)
    status = host.state / "run/status.json"
    status.write_text(
        json.dumps(
            {
                "services": {
                    cluster: {
                        "pid": 456789 if lock_case == "unknown" else 123456,
                        "start_ticks": 25,
                        "pgid": 123456,
                        "boot_id": "previous-boot",
                    }
                }
            }
        )
    )
    previous = status.read_bytes()
    monkeypatch.setattr(
        guest_module,
        "process_identity",
        lambda pid: (
            {"pid": pid, "start_ticks": 99, "pgid": pid, "boot_id": "current-boot"}
            if lock_case == "live"
            else None
        ),
    )
    monkeypatch.setattr(host_module.signal, "signal", lambda *_args: None)
    if lock_case == "owned":
        # This is the real native/guest mismatch, before entering guest I/O.
        with pytest.raises(host_module.RuntimeFailure, match="Unverified PostgreSQL"):
            host.runtime._assert_startable()
    calls = []

    def guest_preflight(action, _seconds):
        assert action == "prepare-tailnet"
        calls.append(action)
        # Model PRoot's filesystem namespace, mapping only filesystem I/O.
        # The real preflight still compares the guest postmaster.pid path.
        with monkeypatch.context() as guest_namespace:
            for name in ("read_text", "exists", "is_symlink"):
                original = getattr(Path, name)

                def mapped(path, *args, _original=original, **kwargs):
                    if path.is_relative_to(guest_state):
                        path = host.state / path.relative_to(guest_state)
                    return _original(path, *args, **kwargs)

                guest_namespace.setattr(Path, name, mapped)
            guest_module.Runtime(guest_state, tmp_path)._assert_startable()
        return {"prepared": True}

    def stop_before_proxy():
        calls.append("proxy")
        raise host_module.RuntimeFailure("test stopped before services")

    monkeypatch.setattr(host, "_call", guest_preflight)
    monkeypatch.setattr(host, "_spawn_proxy", stop_before_proxy)
    expected = "test stopped" if lock_case == "owned" else "Unverified PostgreSQL"
    with pytest.raises(host_module.RuntimeFailure, match=expected):
        host.run()
    expected_calls = (
        ["prepare-tailnet", "proxy"] if lock_case == "owned" else ["prepare-tailnet"]
    )
    assert calls == expected_calls
    assert status.read_bytes() == previous
    assert pidfile.read_text() == contents
    assert host.proxy is None and host.guest is None


@pytest.mark.parametrize("available_after", [0, 2, None])
def test_boot_waits_once_for_configured_vpn_address_with_bounded_deadline(
    tmp_path, monkeypatch, available_after
):
    host = host_module.Host(tmp_path, tmp_path / "prefix", tmp_path / "control")
    host.runtime._prepare()
    host.runtime.configure("100.64.2.3", "phone.example.ts.net")
    clock = [0.0]
    probes = []

    class Probe:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def bind(self, address):
            probes.append(address)
            if available_after is None or clock[0] < available_after:
                raise OSError(host_module.errno.EADDRNOTAVAIL, "VPN not ready")

    monkeypatch.setattr(host_module.socket, "socket", Probe)
    monkeypatch.setattr(host_module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        host_module.time,
        "sleep",
        lambda seconds: clock.__setitem__(0, clock[0] + seconds),
    )
    if available_after is None:
        with pytest.raises(host_module.RuntimeFailure, match="timed out; no services"):
            host.wait_network(seconds=3)
        assert clock[0] == 3
    else:
        host.wait_network(seconds=3)
        assert clock[0] == available_after
    assert probes == [("100.64.2.3", 0)] * (int(clock[0]) + 1)
    assert host.proxy is None and host.guest is None


def test_boot_network_gate_fails_immediately_for_invalid_config_or_probe_error(
    tmp_path, monkeypatch
):
    host = host_module.Host(tmp_path, tmp_path / "prefix", tmp_path / "control")
    host.runtime._prepare()
    with pytest.raises(host_module.RuntimeFailure, match="Configure the phone"):
        host.wait_network()
    host.runtime.configure("100.64.2.3", "phone.example.ts.net")

    def unavailable():
        raise OSError(host_module.errno.EACCES, "permission denied")

    monkeypatch.setattr(host_module.socket, "socket", unavailable)
    monkeypatch.setattr(
        host_module.time,
        "sleep",
        lambda _seconds: pytest.fail("must not retry a permission failure"),
    )
    with pytest.raises(host_module.RuntimeFailure, match="Cannot check"):
        host.wait_network()
