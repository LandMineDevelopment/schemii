"""Delete only app objects owned by a completed, tagged Schemii QA sweep.

Run after ``./test.sh cleanup --run RUN_ID`` and before writer-schema reset.
This preserves credentials, account grants, existing workspaces and evidence.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import re

from testing.harness.schemii_sweep import ACCOUNTS, ROOT, WRITERS
from testing.provision import Client, registry_load


def timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def owned_workspaces(workspaces: dict, run: dict, run_file: Path) -> dict[str, set[str]]:
    """Validate the complete run binding before any authenticated mutation."""
    if run.get("id") != run_file.parent.name or run_file.name != "manifest.json":
        raise ValueError("Run ID does not match its manifest path")
    if run.get("status") not in ("stopped", "passed", "finished-with-gaps"):
        raise ValueError("Cleanup requires a stopped or completed harness run")
    if run.get("fixtureVersion") not in {"schemii-full-qa-v1", "schemii-followup-qa-v1"} or run.get("fixtureMode") != "declared-retained-resources":
        raise ValueError("Run is not a Schemii sweep with declared fixtures")
    accounts = run.get("accounts", [])
    if (not isinstance(accounts, list) or not accounts or len(accounts) != len(set(accounts))
            or not set(accounts).issubset(ACCOUNTS) or not isinstance(run.get("lanes"), list)):
        raise ValueError("Run does not contain a valid subset of designer accounts")
    if set(workspaces.get("lanes", {})) != set(ACCOUNTS) or not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", workspaces.get("tag", "")):
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
            if kind == "writer" and username not in WRITERS:
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


def cleanup(state: Path, workspace_file: Path, run_file: Path) -> dict:
    workspaces = json.loads(workspace_file.read_text())
    run = json.loads(run_file.read_text())
    owned_by_account = owned_workspaces(workspaces, run, run_file)
    started = timestamp(run["createdAt"])
    slots = {slot["username"]: slot for slot in registry_load(state)["slots"]}
    totals = {"chats": 0, "workspaces": 0, "connections": 0}
    for username in owned_by_account:
        fixture = workspaces["lanes"][username]
        prefix = fixture["prefix"]
        owned_ids = owned_by_account[username]
        client = Client()
        client.login(slots[username])
        try:
            current = client.call("GET", "/api/v1/schemii/workspaces")["workspaces"]
            removable = {workspace["id"] for workspace in current
                         if workspace["id"] in owned_ids or
                         (workspace["name"].startswith(prefix)
                          and timestamp(workspace["createdAt"]) >= started)}
            for chat in client.call("GET", "/api/v1/schemii/ai/chats").get("chats", []):
                if (chat.get("status") != "deleted" and chat.get("workspaceId") in (owned_ids | removable)
                        and timestamp(chat["createdAt"]) >= started):
                    client.call("DELETE", "/api/v1/schemii/ai/chats/" + chat["id"])
                    totals["chats"] += 1
            for workspace in current:
                if workspace["id"] in removable:
                    client.call("DELETE", f"/api/v1/schemii/workspaces/{workspace['id']}?expectedRevision={workspace['revision']}", expected=204)
                    totals["workspaces"] += 1
            connections = client.call("GET", "/api/v1/connections?product=schemii")["connections"]
            for connection in connections:
                if (connection.get("ownership") == "user" and connection["name"].startswith(prefix)
                        and timestamp(connection["createdAt"]) >= started):
                    client.call("DELETE", f"/api/v1/connections/{connection['id']}?expectedRevision={connection['revision']}", expected=204)
                    totals["connections"] += 1
        finally:
            client.logout()
    return totals


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True)
    parser.add_argument("--workspace-fixtures", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, default=ROOT / ".schemii/testing")
    args = parser.parse_args()
    if not args.run.startswith("qa-") or "/" in args.run or ".." in args.run:
        parser.error("--run needs an exact harness run ID")
    result = cleanup(args.state_dir, args.workspace_fixtures, ROOT / "artifacts/qa" / args.run / "manifest.json")
    print(json.dumps({"run": args.run, "removed": result, "credentials": "preserved"}))


if __name__ == "__main__":
    main()
