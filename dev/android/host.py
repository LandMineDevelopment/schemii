#!/usr/bin/env python3
"""Native Termux owner for the optional proxy and the PRoot backend.

Only start.sh invokes this module. No database is started outside the guest.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import threading
import time

from runtime import (
    LogPump,
    append_log,
    Runtime,
    RuntimeFailure,
    is_current,
    matching_executable,
    owns_group,
    private_directory,
    process_arguments,
    process_identity,
    read_json,
    verified_parent_chain,
    write_private,
)


def parent_death_signal(parent: int) -> None:
    """Called before exec, before any log-pump threads exist in the parent."""
    if ctypes.CDLL(None, use_errno=True).prctl(1, signal.SIGTERM, 0, 0, 0) != 0:
        os._exit(71)
    if os.getppid() != parent:
        os._exit(72)


class Host:
    def __init__(self, source: Path, prefix: Path, control: Path):
        self.source, self.prefix, self.control = source, prefix, control
        self.state = (
            prefix
            / "var/lib/proot-distro/containers/schemii-android/rootfs/home/schemii/.local/share/schemii"
        )
        self.runtime = Runtime(self.state, source)
        self.stop_event = threading.Event()
        self.proxy: subprocess.Popen | None = None
        self.guest: subprocess.Popen | None = None
        self.proxy_identity: dict = {}
        self.guest_identity: dict = {}
        self.pumps: list[LogPump] = []
        self._last_record: str | None = None

    def guest_command(self, action: str) -> list[str]:
        return [
            "proot-distro",
            "login",
            "--isolated",
            "--user",
            "schemii",
            "--env",
            "PROOT_DONT_SHARE_LIBANDROID_SHMEM=1",
            "--bind",
            f"{self.source}:/opt/schemii",
            "--bind",
            f"{self.control}:/mnt/schemii-control",
            "--work-dir",
            "/opt/schemii",
            "schemii-android",
            "--",
            "/opt/schemii-venv/bin/python",
            "/opt/schemii/dev/android/runtime.py",
            action,
        ]

    def proxy_command(self) -> list[str]:
        return [
            str(self.source / "dev/android/bin/schemii-tailnet-proxy"),
            "--state-dir",
            str(self.state / "tailnet/node"),
            "--status-file",
            str(self.state / "run/tailnet.json"),
            "--hostname",
            self.runtime.tailnet_config()["hostname"],
            "--upstream",
            "https://127.0.0.1:8001",
            "--upstream-ca",
            str(self.state / "tls/ca.crt"),
            "--dns-server",
            "100.100.100.100:53",
        ]

    def _record(self, state: str, error: str | None = None) -> None:
        fingerprint = json.dumps(
            [state, error, self.proxy_identity, self.guest_identity], sort_keys=True
        )
        if fingerprint == self._last_record:
            return
        value = {
            "schema": 1,
            "state": state,
            "source": str(self.source),
            "owner": process_identity(os.getpid()),
            "owner_arguments": process_arguments(process_identity(os.getpid()) or {}),
            "proxy": self.proxy_identity,
            "guest": self.guest_identity,
            "proxy_command": self.proxy_command(),
            "error": error,
            "updated_at": time.time(),
        }
        write_private(self.control / "host.json", json.dumps(value) + "\n")
        write_private(self.state / "run/native-tailnet.json", json.dumps(value) + "\n")
        self._last_record = fingerprint

    def _call(self, action: str, seconds: float = 20) -> dict:
        process = subprocess.Popen(
            self.guest_command(action),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        identity = process_identity(process.pid)
        deadline = time.monotonic() + seconds
        try:
            while True:
                try:
                    output, _error = process.communicate(timeout=0.2)
                    break
                except subprocess.TimeoutExpired:
                    if time.monotonic() >= deadline or (
                        self.stop_event.is_set() and action != "stop"
                    ):
                        raise RuntimeFailure(
                            f"Guest {action} did not finish; inspect private logs"
                        )
            if len(output) > 65536:
                raise RuntimeFailure(
                    "Guest control response exceeded its private budget"
                )
            if _error:
                private_directory(self.state / "logs")
                safe = re.sub(
                    rb"https://login\.tailscale\.com/[^\s\"']+",
                    b"[private enrollment URL]",
                    _error[-16384:],
                )
                append_log(self.state / "logs/native-control.log", safe)
            value = json.loads(output or b"{}")
            if process.returncode and action != "status":
                raise RuntimeFailure(
                    f"Guest {action} failed; inspect schemii logs native-control"
                )
            return value
        finally:
            if process.poll() is None and owns_group(identity):
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    if owns_group(identity):
                        os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=2)

    def _spawn_proxy(self) -> None:
        ca = self.prefix / "etc/tls/cert.pem"
        if not ca.is_file():
            raise RuntimeFailure(
                "Termux public CA bundle is missing; update Termux ca-certificates"
            )
        environment = {
            key: value
            for key, value in os.environ.items()
            if key
            not in {
                "TS_AUTHKEY",
                "TS_AUTH_KEY",
                "TS_CLIENT_SECRET",
                "TS_CLIENT_ID",
                "TS_ID_TOKEN",
                "TS_AUDIENCE",
                "TSNET_FORCE_LOGIN",
                "TS_CONTROL_URL",
                "TS_LOGIN_SERVER",
                "TSNET_LOGIN_SERVER",
            }
        }
        environment.update(PREFIX=str(self.prefix), SSL_CERT_FILE=str(ca))
        environment.pop("SSL_CERT_DIR", None)
        parent = os.getpid()
        self.proxy = subprocess.Popen(
            self.proxy_command(),
            cwd=self.source,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            preexec_fn=lambda: parent_death_signal(parent),
        )
        self.proxy_identity = process_identity(self.proxy.pid) or {}
        if self.proxy_identity.get("pgid") != self.proxy.pid:
            raise RuntimeFailure("Cannot establish native proxy process ownership")
        self.pumps.append(LogPump(self.proxy.stdout, self.state / "logs/tailnet.log"))

    def _wait_proxy(self) -> None:
        while not self.stop_event.is_set():
            if self.proxy is None or self.proxy.poll() is not None:
                raise RuntimeFailure(
                    "Native tailnet proxy exited; inspect schemii logs tailnet"
                )
            status = self.runtime._tailnet_status()
            if status.get("error"):
                raise RuntimeFailure(status["error"])
            if status.get("pid") != self.proxy.pid:
                self.stop_event.wait(0.2)
                continue
            if (
                status.get("online") is True
                and status.get("ready") is True
                and status.get("origin")
            ):
                return
            self._record(
                "login_pending"
                if status.get("login_required")
                or status.get("backend_state") == "NeedsMachineAuth"
                else "starting"
            )
            self.stop_event.wait(0.3)
        raise RuntimeFailure("Native startup stopped; identity and databases preserved")

    def _cleanup(self) -> None:
        failure = None
        try:
            if self.guest is not None:
                self._call("stop", 100)
                self.guest.wait(timeout=10)
        except (
            OSError,
            ValueError,
            subprocess.TimeoutExpired,
            RuntimeFailure,
        ) as error:
            failure = error
        finally:
            if (
                self.proxy is not None
                and self.proxy.poll() is None
                and owns_group(self.proxy_identity)
            ):
                os.killpg(self.proxy.pid, signal.SIGTERM)
                try:
                    self.proxy.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    if owns_group(self.proxy_identity):
                        os.killpg(self.proxy.pid, signal.SIGKILL)
                    self.proxy.wait(timeout=2)
            for pump in self.pumps:
                pump.thread.join(timeout=2)
        if failure:
            raise RuntimeFailure(
                "Guest shutdown incomplete; native proxy stopped, retained guest ownership preserved"
            ) from None

    def run(self) -> None:
        os.chdir(self.source)
        private_directory(self.control)
        self.runtime._prepare()
        with self.runtime._file_lock("native-host", blocking=False):
            old = read_json(self.control / "host.json")
            if any(is_current(old.get(name)) for name in ("owner", "proxy", "guest")):
                raise RuntimeFailure(
                    "A native stack owner or retained child is active; stop it first"
                )
            self.runtime._assert_startable()
            for sig in (signal.SIGTERM, signal.SIGINT):
                signal.signal(sig, lambda _sig, _frame: self.stop_event.set())
            error = None
            self._record("starting")
            try:
                self._call("prepare-tailnet", 90)
                (self.state / "run/tailnet.json").unlink(missing_ok=True)
                self._spawn_proxy()
                if self.proxy is None:
                    raise RuntimeFailure("Native proxy was not created")
                self._record("starting")
                self._wait_proxy()
                if self.stop_event.is_set():
                    raise RuntimeFailure(
                        "Startup stopped before databases were started"
                    )
                self.guest = subprocess.Popen(
                    self.guest_command("run"),
                    cwd=self.source,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
                self.guest_identity = process_identity(self.guest.pid) or {}
                if self.guest_identity.get("pgid") != self.guest.pid:
                    raise RuntimeFailure("Cannot establish guest runner ownership")
                self.pumps.append(
                    LogPump(self.guest.stdout, self.control / "launcher.log")
                )
                self._record("starting")
                while not self.stop_event.wait(0.3):
                    if self.proxy.poll() is not None or self.guest.poll() is not None:
                        raise RuntimeFailure(
                            "Native proxy or guest exited; inspect private logs before restart"
                        )
                    value = read_json(self.state / "run/status.json")
                    self._record(
                        "ready"
                        if value.get("ready") and is_current(value.get("supervisor"))
                        else "starting"
                    )
            except Exception as caught:
                error = (
                    None
                    if self.stop_event.is_set()
                    else (
                        str(caught)
                        if isinstance(caught, RuntimeFailure)
                        else "Native startup failed; inspect private logs"
                    )
                )
            finally:
                try:
                    self._cleanup()
                except RuntimeFailure as caught:
                    error = str(caught)
                self._record("failed" if error else "stopped", error)
            if error:
                raise RuntimeFailure(error)

    def verified_owner(self, value: dict) -> bool:
        if not isinstance(value.get("source"), str):
            return False
        owner, source = value.get("owner"), Path(value["source"])
        if not isinstance(owner, dict):
            return False
        home = self.control.parents[2]
        releases = home / ".local/share/schemii/releases"
        if source.parent.resolve() != releases.resolve() or not re.fullmatch(
            r"android-v\d+\.\d+\.\d+-preview\.\d+", source.name
        ):
            return False
        try:
            manifest = read_json(source / "ANDROID_RELEASE.json")
            if (
                manifest.get("schema") != 1
                or manifest.get("tag") != source.name
                or manifest.get("experimental") is not True
            ):
                return False
            script = source / "dev/android/host.py"
            expected = manifest["files"]["dev/android/host.py"]["sha256"]
            return (
                not source.is_symlink()
                and not script.is_symlink()
                and hashlib.sha256(script.read_bytes()).hexdigest() == expected
                and is_current(owner)
                and matching_executable(owner, self.prefix / "bin/python")
                and process_arguments(owner) == value.get("owner_arguments")
                and process_arguments(owner)[-2:] == [str(script), "run"]
                and os.path.samefile(f"/proc/{owner['pid']}/cwd", source)
            )
        except (OSError, KeyError, TypeError):
            return False

    def status(self, *, login: bool = False) -> dict:
        value = read_json(self.control / "host.json")
        if not self.verified_owner(value):
            return {
                "schema": 1,
                "state": "failed"
                if value.get("state") not in (None, "stopped")
                else "stopped",
                "ready": False,
                "supervisor_alive": False,
                "services": {},
                "retained_children_active": any(
                    is_current(value.get(name)) for name in ("proxy", "guest")
                ),
                "error": value.get("error"),
            }
        proxy = value.get("proxy", {})
        proxy_live = (
            is_current(proxy)
            and verified_parent_chain((value["owner"], proxy))
            and matching_executable(proxy, Path(value["proxy_command"][0]))
            and process_arguments(proxy) == value["proxy_command"]
        )
        if value.get("guest") and is_current(value["guest"]):
            try:
                result = self._call("status", 15)
            except (RuntimeFailure, ValueError, OSError):
                result = {
                    "schema": 1,
                    "state": "starting",
                    "ready": False,
                    "services": {},
                    "error": "Guest readiness probe unavailable; inspect private logs",
                }
        else:
            result = {
                "schema": 1,
                "state": value.get("state", "starting"),
                "ready": False,
                "services": {},
            }
        tailnet = self.runtime._tailnet_status(include_login=login)
        if not proxy_live:
            tailnet.update(online=False, ready=False, login_required=False)
            tailnet.pop("login_url", None)
        result.update(
            host_supervisor=value["owner"],
            supervisor_alive=True,
            tailnet=tailnet,
            ready=bool(result.get("ready") and proxy_live),
        )
        result["services"]["tailnet"] = {**proxy, "running": proxy_live}
        return result

    def stop(self) -> dict:
        value = read_json(self.control / "host.json")
        if is_current(value.get("owner")) and not self.verified_owner(value):
            raise RuntimeFailure(
                "Native owner identity/source is unverified; its process was preserved"
            )
        if self.verified_owner(value):
            os.kill(value["owner"]["pid"], signal.SIGTERM)
            deadline = time.monotonic() + 120
            while is_current(value["owner"]) and time.monotonic() < deadline:
                time.sleep(0.1)
            if is_current(value["owner"]):
                raise RuntimeFailure(
                    "Native owner did not stop; retained identities preserved"
                )
        if any(is_current(value.get(name)) for name in ("proxy", "guest")):
            raise RuntimeFailure(
                "Captured native or guest child remains active; inspect private ownership before recovery"
            )
        if read_json(self.control / "host.json").get("state") == "failed":
            self._call("assert-stopped")
            final = read_json(self.control / "host.json")
            final.update(state="stopped", last_failure=final.get("error"), error=None)
            write_private(self.control / "host.json", json.dumps(final) + "\n")
            write_private(
                self.state / "run/native-tailnet.json", json.dumps(final) + "\n"
            )
        return {"state": "stopped", "ready": False}


def main() -> int:
    os.umask(0o077)
    source = Path(__file__).resolve().parents[2]
    prefix = Path(os.environ["PREFIX"])
    host = Host(source, prefix, Path.home() / ".local/share/schemii-android")
    action = sys.argv[1] if len(sys.argv) == 2 else ""
    try:
        if action == "enabled":
            return 0 if host.runtime.tailnet_config() else 1
        if action == "active":
            value = read_json(host.control / "host.json")
            return (
                0
                if any(
                    is_current(value.get(name)) for name in ("owner", "proxy", "guest")
                )
                else 1
            )
        if action == "run":
            host.run()
            return 0
        if action == "stop":
            value = host.stop()
        elif action in ("status", "tailnet-login"):
            value = host.status(login=action == "tailnet-login")
        else:
            raise RuntimeFailure("Use the supported schemii lifecycle commands")
        print(json.dumps(value, indent=2))
        return 0 if action != "status" or value.get("ready") else 1
    except (RuntimeFailure, OSError, ValueError) as error:
        print(f"Android native owner: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
