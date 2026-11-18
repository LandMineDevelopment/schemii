"""Delete only app objects owned by a completed, tagged Schemii QA sweep.

Run after ``./test.sh cleanup --run RUN_ID`` and before writer-schema reset.
This preserves credentials, account grants, existing workspaces and evidence.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess

from testing.harness.schemii_sweep import ROOT, WRITERS
from testing.provision import Client, registry_load, write_private


def process_birth(pid: int) -> str | None:
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()
    except FileNotFoundError:
        return None
    return None if fields[0] == "Z" else fields[19]


def require_stopped(run: dict, run_file: Path) -> None:
    """Completion is not transport shutdown; verify recorded process identities."""
    try:
        owner = json.loads(run_file.with_name("controller-owner.json").read_text())
    except FileNotFoundError as error:
        raise ValueError("Controller ownership record is missing; confirm stopped ownership before cleanup") from error
    records = [("controller", owner)]
    for lane in run.get("lanes", []):
        for kind in ("browser", "worker"):
            if lane.get(kind + "LaunchPending"):
                raise ValueError(f"{kind} launch ownership is unresolved for {lane.get('id')}")
            if lane.get(kind) is not None:
                records.append((f"{kind} for {lane.get('id')}", lane[kind]))
        native = lane.get("native")
        if native is not None:
            if not isinstance(native, dict):
                raise ValueError(f"Native ownership record is incomplete for {lane.get('id')}")
            cleanup = native.get("cleanup")
            if (not isinstance(native.get("closedAt"), str) or not native["closedAt"]
                    or not isinstance(cleanup, dict)
                    or cleanup.get("context") != "closed-observed"
                    or cleanup.get("transport") != "stopped"
                    or cleanup.get("guardian") != "stopped"
                    or cleanup.get("temporaryOutput") != "removed-observed"):
                raise ValueError(f"Native cleanup is unresolved for {lane.get('id')}; close and release the owned transport first")
            directory = native.get("directory")
            if not isinstance(directory, str) or not os.path.isabs(directory):
                raise ValueError(f"Native output ownership is incomplete for {lane.get('id')}")
            if os.path.lexists(directory):
                raise ValueError(f"Native temporary output still exists for {lane.get('id')}; wait for owned transport cleanup")
            for kind, pid, tick in (
                ("supervisor", "pid", "birthTick"),
                ("child", "childPid", "childBirthTick"),
                ("guardian", "guardianPid", "guardianBirthTick"),
            ):
                records.append((f"native {kind} for {lane.get('id')}",
                                {"pid": native.get(pid), "birthTick": native.get(tick)}))
            browsers = native.get("browserProcesses")
            if not isinstance(browsers, list):
                raise ValueError(f"Native browser ownership is incomplete for {lane.get('id')}")
            records.extend((f"native browser for {lane.get('id')}", owner) for owner in browsers)
    for kind, record in records:
        if (not isinstance(record, dict) or type(record.get("pid")) is not int or record["pid"] < 1
                or not isinstance(record.get("birthTick"), str) or not record["birthTick"].isdigit()):
            raise ValueError(f"{kind} ownership record is incomplete; confirm stopped ownership before cleanup")
        if process_birth(record["pid"]) == record["birthTick"]:
            raise ValueError(f"Owned {kind} is still live; stop it before cleanup")


@contextmanager
def file_lock(path: Path):
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield descriptor
    finally:
        os.close(descriptor)


@contextmanager
def cleanup_guard(run_file: Path, *, lease=None):
    """Use the same flock files/order as CLI fixture cleanup and allocation.

    Lifecycle comes first, as in resume/cleanup. Deployment is then exclusive,
    followed by reservations, as in setup/reset/author cleanup. No reservation
    is released here, including an old run's or a newly allocated owner's.
    """
    common = Path(subprocess.check_output(
        ["git", "-C", str(ROOT), "rev-parse", "--path-format=absolute", "--git-common-dir"],
        text=True).strip())
    with file_lock(run_file.with_name("lifecycle.lock")):
        run = json.loads(run_file.read_text())
        require_stopped(run, run_file)
        with file_lock(common / "qa-startup.lock") as gate, file_lock(common / "qa-deployment.lock") as deployment:
            with file_lock(common / "qa-account-reservations.lock"):
                run = json.loads(run_file.read_text())
                require_stopped(run, run_file)
                for username in run.get("accounts", []):
                    if not isinstance(username, str) or not re.fullmatch(r"[A-Za-z0-9_.@-]{1,64}", username):
                        raise ValueError("Invalid run account")
                    if os.path.lexists(common / "qa-account-leases" / (username.lower() + ".json")):
                        raise ValueError(f"Account {username} is reserved by an active or unresolved run; clean up that run first")
                if lease is not None:
                    lease.extend((deployment, gate))
                yield run


def timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def owned_workspaces(workspaces: dict, run: dict, run_file: Path) -> dict[str, set[str]]:
    """Validate the complete run binding before any authenticated mutation."""
    if run.get("id") != run_file.parent.name or run_file.name != "manifest.json":
        raise ValueError("Run ID does not match its manifest path")
    if run.get("status") not in ("stopped", "passed", "finished-with-gaps"):
        raise ValueError("Cleanup requires a stopped or completed harness run")
    if run.get("fixtureVersion") not in {
        "schemii-full-qa-v1", "schemii-followup-qa-v1", "schemii-native-wave-v1",
    } or run.get("fixtureMode") != "declared-retained-resources":
        raise ValueError("Run is not a Schemii sweep with declared fixtures")
    accounts = run.get("accounts", [])
    if (not isinstance(accounts, list) or not accounts or len(accounts) != len(set(accounts))
            or any(not isinstance(account, str) or not re.fullmatch(r"qa_designer_[0-9]{3}", account) for account in accounts)
            or not isinstance(run.get("lanes"), list)):
        raise ValueError("Run does not contain a valid subset of designer accounts")
    if (not isinstance(workspaces.get("lanes"), dict) or not set(accounts).issubset(workspaces["lanes"])
            or any(not re.fullmatch(r"qa_designer_[0-9]{3}", account) for account in workspaces["lanes"])
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", workspaces.get("tag", ""))):
        raise ValueError("Workspace fixture accounts or tag are invalid")
    lanes = run["lanes"]
    if len(lanes) != len(accounts) or [lane.get("username") for lane in lanes] != accounts:
        raise ValueError("Run lanes do not match the run accounts")
    result = {}
    seen = set()
    for lane in lanes:
        username = lane["username"]
        fixture = workspaces["lanes"][username]
        resources = lane.get("resources", {})
        prefix = f"qa_{workspaces['tag']}_{username[-3:]}_"
        if fixture.get("prefix") != prefix or resources.get("scratchPrefix") != prefix:
            raise ValueError(f"Workspace tag or prefix differs from run fixture for {username}")
        if resources.get("workspaceTag") is not None and resources["workspaceTag"] != workspaces["tag"]:
            raise ValueError(f"Workspace tag differs from run resources for {username}")
        seed_ownership = resources.get("seedOwnership")
        if seed_ownership is not None and (
            not isinstance(seed_ownership, dict)
            or set(seed_ownership) != {"localCreated", "readerCreated", "writerCreated"}
            or any(type(seed_ownership[key]) is not bool or seed_ownership[key] is not fixture.get(key)
                   for key in seed_ownership)
        ):
            raise ValueError(f"Workspace ownership differs from run fixture for {username}")
        owned = set()
        for kind in ("local", "reader", "writer"):
            key, created_key = kind + "WorkspaceId", kind + "Created"
            value = fixture.get(key)
            expected = resources.get(key)
            declared_writer = (username in workspaces.get("writerAccounts", [])
                               and resources.get("writableSchema") == "qa_write_designer_" + username[-3:]
                               and isinstance(resources.get("writableConnectionId"), str)
                               and resources["writableConnectionId"] == fixture.get("writerConnectionId"))
            if kind == "writer" and username not in WRITERS and not declared_writer:
                if value is not None or expected is not None or fixture.get(created_key) is not False:
                    raise ValueError(f"Unexpected writer fixture for {username}")
                continue
            if not isinstance(value, str) or not re.fullmatch(r"ws_[0-9a-f]{32}", value) or expected != value:
                raise ValueError(f"Workspace ID differs from run fixture for {username}: {kind}")
            if value in seen or type(fixture.get(created_key)) is not bool:
                raise ValueError(f"Duplicate workspace ID or missing ownership flag for {username}: {kind}")
            seen.add(value)
            if fixture[created_key]:
                owned.add(value)
        result[username] = owned
    return result


def cleanup(state: Path, workspace_file: Path, run_file: Path, *, reset_writers=False) -> dict:
    lease = []
    with cleanup_guard(run_file, lease=lease) as run:
        if reset_writers:
            reset_owned_writers(state, workspace_file, run_file, run, lease, validate_only=True)
        result = _cleanup(state, workspace_file, run_file, run)
        if reset_writers:
            if result["pending"]:
                raise ValueError("Reconcile unledgered app objects before writer reset")
            result["writers"] = reset_owned_writers(state, workspace_file, run_file, run, lease)
        return result


def reset_owned_writers(state, workspace_file, run_file, run, lease, *, invoke=None, validate_only=False):
    """Only caller-held stopped-run, deployment and account guards authorize reset."""
    workspaces = json.loads(workspace_file.read_text())
    owned_workspaces(workspaces, run, run_file)
    slots = {slot["username"]: slot for slot in registry_load(state)["slots"]}
    targets = []
    for lane in run["lanes"]:
        resources, username = lane.get("resources", {}), lane["username"]
        if not resources.get("writerWorkspaceId"):
            continue
        slot = slots.get(username, {})
        if (resources.get("writableSchema") != "qa_write_designer_" + username[-3:]
                or slot.get("writableSchema") != resources["writableSchema"]
                or not re.fullmatch(r"pg_[0-9a-f]{32}", resources.get("writableConnectionId", ""))
                or slot.get("writableConnectionId") != resources["writableConnectionId"]
                or workspaces["lanes"][username].get("writerCreated") is not True):
            raise ValueError("Writer reset requires exact owned marked profile/schema/workspace")
        targets.append(username)
    if validate_only:
        return targets
    report_path = run_file.with_name("writer-cleanup.json")
    report = json.loads(report_path.read_text()) if report_path.exists() else {"run": run["id"], "attempts": []}
    if report.get("run") != run["id"]:
        raise ValueError("Writer cleanup receipt belongs to another run")
    for username in targets:
        attempt = {"account": username, "schema": slots[username]["writableSchema"], "status": "pending", "commands": []}
        report["attempts"].append(attempt)
        write_private(report_path, report)
        try:
            for action in ("--reset-testing-writable", "--verify-testing-writable"):
                command = ["./start.sh", action, username]
                entry = {"command": command, "status": "pending"}
                attempt["commands"].append(entry)
                write_private(report_path, report)
                if invoke is not None:
                    invoke(command)
                else:
                    log_path = run_file.with_name(f"writer-cleanup-{username}-{len(report['attempts'])}-{len(attempt['commands'])}.log")
                    with log_path.open("w", opener=lambda name, flags: os.open(name, flags | os.O_EXCL, 0o600)) as log:
                        subprocess.run(["bash", "-c", 'exec 3<&"$1" 4<&"$2"; shift 2; exec "$@"', "owned-writer-cleanup",
                                        *map(str, lease), *command], cwd=ROOT, check=True, stdout=log, stderr=log,
                                       pass_fds=tuple(lease), env={**os.environ, "SCHEMII_QA_LEASE_FD": "3",
                                                               "SCHEMII_QA_GATE_FD": "4", "SCHEMII_QA_LEASE_MODE": "exclusive",
                                                               "SCHEMII_QA_STATE_DIRECTORY": str(state.resolve())})
                    entry["log"] = str(log_path)
                entry["status"] = "passed"
                write_private(report_path, report)
            attempt["status"] = "verified-empty"
        except Exception:
            attempt["status"] = "failed"
            raise
        finally:
            write_private(report_path, report)
    return {"verifiedEmpty": targets, "receipt": str(report_path)}


def _cleanup(state: Path, workspace_file: Path, run_file: Path, run: dict) -> dict:
    workspaces = json.loads(workspace_file.read_text())
    owned_by_account = owned_workspaces(workspaces, run, run_file)
    started = timestamp(run["createdAt"])
    slots = {slot["username"]: slot for slot in registry_load(state)["slots"]}
    if not set(owned_by_account).issubset(slots) or any(not slots[username].get("provisioned") for username in owned_by_account):
        raise ValueError("Cleanup accounts must be provisioned in the retained registry")
    receipts_by_account = {}
    for lane in run["lanes"]:
        username = lane["username"]
        receipts = lane.get("resources", {}).get("cleanupReceipts", [])
        if not isinstance(receipts, list):
            raise ValueError(f"Invalid creation receipts for {username}")
        receipted = {kind: set() for kind in ("workspace", "connection", "chat")}
        for receipt in receipts:
            if (not isinstance(receipt, dict) or receipt.get("kind") not in receipted
                    or not isinstance(receipt.get("id"), str) or not receipt["id"]
                    or not isinstance(receipt.get("createdAt"), str)
                    or timestamp(receipt["createdAt"]) < started):
                raise ValueError(f"Invalid creation receipt for {username}")
            receipted[receipt["kind"]].add(receipt["id"])
        receipts_by_account[username] = receipted
    totals = {"chats": 0, "workspaces": 0, "connections": 0, "pending": 0}
    report_file = run_file.with_name("sweep-cleanup.json")
    report = {"run": run["id"], "tag": workspaces["tag"], "status": "cleanup-pending",
              "workspaceFixtures": str(workspace_file.resolve()), "removed": [], "unledgered": []}
    if report_file.exists():
        previous = json.loads(report_file.read_text())
        if previous.get("run") != report["run"] or previous.get("tag") != report["tag"]:
            raise ValueError("Cleanup receipt belongs to a different run/tag")
        report["removed"] = previous.get("removed", [])
    write_private(report_file, report)

    def removed(username, kind, object_id):
        report["removed"].append({"username": username, "kind": kind, "id": object_id})
        totals[kind + "s"] += 1
        write_private(report_file, report)

    for username in owned_by_account:
        fixture = workspaces["lanes"][username]
        prefix = fixture["prefix"]
        owned_ids = owned_by_account[username]
        receipted = receipts_by_account[username]
        client = Client()
        client.login(slots[username])
        try:
            current = client.call("GET", "/api/v1/schemii/workspaces")["workspaces"]
            removable = {workspace["id"] for workspace in current
                         if workspace["id"] in owned_ids or
                         (workspace["id"] in receipted["workspace"] and workspace["name"].startswith(prefix)
                          and timestamp(workspace["createdAt"]) >= started)}
            for workspace in current:
                if (workspace["name"].startswith(prefix) and workspace["id"] not in removable
                        and timestamp(workspace["createdAt"]) >= started):
                    report["unledgered"].append({"username": username, "kind": "workspace", "id": workspace["id"]})
            for chat in client.call("GET", "/api/v1/schemii/ai/chats").get("chats", []):
                if (chat.get("status") != "deleted" and (chat.get("workspaceId") in (owned_ids | removable)
                        or (chat["id"] in receipted["chat"] and chat.get("workspaceId") in {
                            fixture.get(key + "WorkspaceId") for key in ("local", "reader", "writer")}))
                        and timestamp(chat["createdAt"]) >= started):
                    client.call("DELETE", "/api/v1/schemii/ai/chats/" + chat["id"])
                    removed(username, "chat", chat["id"])
            for workspace in current:
                if workspace["id"] in removable:
                    client.call("DELETE", f"/api/v1/schemii/workspaces/{workspace['id']}?expectedRevision={workspace['revision']}", expected=204)
                    removed(username, "workspace", workspace["id"])
            connections = client.call("GET", "/api/v1/connections?product=schemii")["connections"]
            for connection in connections:
                if (connection.get("ownership") == "user" and connection["name"].startswith(prefix)
                        and timestamp(connection["createdAt"]) >= started):
                    if connection["id"] not in receipted["connection"]:
                        report["unledgered"].append({"username": username, "kind": "connection", "id": connection["id"]})
                        continue
                    client.call("DELETE", f"/api/v1/connections/{connection['id']}?expectedRevision={connection['revision']}", expected=204)
                    removed(username, "connection", connection["id"])
        except Exception as error:
            report["error"] = str(error)
            write_private(report_file, report)
            raise
        finally:
            client.logout()
    totals["pending"] = len(report["unledgered"])
    report["status"] = "cleanup-pending" if totals["pending"] else "cleaned"
    write_private(report_file, report)
    return totals


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True)
    parser.add_argument("--workspace-fixtures", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, default=ROOT / ".schemii/testing")
    parser.add_argument("--reset-writers", action="store_true", help="After owned app cleanup, reset and verify only exact marked owned writer targets")
    args = parser.parse_args()
    if not args.run.startswith("qa-") or "/" in args.run or ".." in args.run:
        parser.error("--run needs an exact harness run ID")
    result = cleanup(args.state_dir, args.workspace_fixtures, ROOT / "artifacts/qa" / args.run / "manifest.json", reset_writers=args.reset_writers)
    print(json.dumps({"run": args.run, "removed": result, "credentials": "preserved"}))
    if result["pending"]:
        raise SystemExit(4)


if __name__ == "__main__":
    main()
