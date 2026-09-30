"""Prepare owned Schemii designs and permissioned chat prerequisites for UI QA.

The input is the private workspace map from ``schemii_workspaces``. This tool
never executes SQL or applies a migration. It replaces only empty desired
designs in the four explicitly selected test-owned workspaces and creates two
chats for the dedicated chat-only account. Progress is saved after each API
mutation so an interrupted run can be verified and resumed safely.
"""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import re

from testing.harness.schemii_sweep import ROOT
from testing.provision import Client, registry_load


DESIGN_ACCOUNTS = ("qa_designer_005", "qa_designer_007", "qa_designer_008", "qa_designer_011")
CHAT_ACCOUNT = "qa_designer_001"
WORKSPACE_ID = re.compile(r"ws_[0-9a-f]{32}\Z")
CHAT_ID = re.compile(r"chat_[0-9a-f]{32}\Z")


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def object_id(tag: str, kind: str, name: str) -> str:
    return kind + "_" + sha256(f"{tag}:{kind}:{name}".encode()).hexdigest()[:32]


def design_content(tag: str) -> dict:
    """The exact disposable two-table oracle used by the Schemii sweep."""
    oid = lambda kind, name: object_id(tag, kind, name)
    projects, tasks = oid("table", "projects"), oid("table", "tasks")
    project_id, code, title, budget, due_on = (
        oid("column", "projects." + name)
        for name in ("project_id", "code", "title", "budget", "due_on")
    )
    task_id, task_project_id, state, effort, notes = (
        oid("column", "tasks." + name)
        for name in ("task_id", "project_id", "state", "effort_hours", "notes")
    )
    return {
        "tables": [
            {
                "id": projects, "name": "qa_projects",
                "columns": [
                    {"id": project_id, "name": "project_id", "dataType": "bigint", "nullable": False, "identity": "always"},
                    {"id": code, "name": "code", "dataType": "varchar(24)", "nullable": False},
                    {"id": title, "name": "title", "dataType": "text", "nullable": False},
                    {"id": budget, "name": "budget", "dataType": "numeric(12,2)", "nullable": False, "defaultExpression": "0"},
                    {"id": due_on, "name": "due_on", "dataType": "date", "nullable": True},
                ],
                "keys": [
                    {"id": oid("key", "projects.pk"), "name": "qa_projects_pkey", "kind": "primary", "columnIds": [project_id]},
                    {"id": oid("key", "projects.code"), "name": "qa_projects_code_key", "kind": "unique", "columnIds": [code]},
                ],
                "checks": [{"id": oid("check", "projects.budget"), "name": "qa_projects_budget_check",
                            "expression": "budget >= 0", "columnIds": [budget]}],
            },
            {
                "id": tasks, "name": "qa_tasks",
                "columns": [
                    {"id": task_id, "name": "task_id", "dataType": "bigint", "nullable": False, "identity": "always"},
                    {"id": task_project_id, "name": "project_id", "dataType": "bigint", "nullable": False},
                    {"id": state, "name": "state", "dataType": "varchar(16)", "nullable": False, "defaultExpression": "'todo'"},
                    {"id": effort, "name": "effort_hours", "dataType": "numeric(6,2)", "nullable": True},
                    {"id": notes, "name": "notes", "dataType": "text", "nullable": True},
                ],
                "keys": [{"id": oid("key", "tasks.pk"), "name": "qa_tasks_pkey", "kind": "primary", "columnIds": [task_id]}],
                "checks": [{"id": oid("check", "tasks.state"), "name": "qa_tasks_state_check",
                            "expression": "state IN ('todo','doing','done')", "columnIds": [state]}],
                "indexes": [{"id": oid("index", "tasks.project_state"), "name": "qa_tasks_project_id_state_idx",
                             "columnIds": [task_project_id, state]}],
            },
        ],
        "relationships": [{"id": oid("relationship", "tasks.projects"), "name": "qa_tasks_project_id_fkey",
                           "sourceTableId": tasks, "sourceColumnIds": [task_project_id],
                           "targetTableId": projects, "targetColumnIds": [project_id]}],
    }


def write_private(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")
    os.replace(temporary, path)


def _assert_workspace(client: Client, workspace_id: str, expected_name: str | None,
                      expected_connection: str | None) -> None:
    if not WORKSPACE_ID.fullmatch(workspace_id):
        raise ValueError("Invalid workspace ID in private fixture")
    workspace = client.call("GET", "/api/v1/schemii/workspaces/" + workspace_id)
    if expected_name is not None and workspace["name"] != expected_name:
        raise ValueError("Workspace name differs from QA ownership marker")
    if workspace.get("connectionId") != expected_connection:
        raise ValueError("Workspace target differs from the assigned QA fixture")


def _empty_design(content: dict) -> bool:
    return not any(content.get(key) for key in ("tables", "relationships", "types", "functions", "views", "triggers"))


def _capabilities(modes: dict[str, str], *, live_catalog: bool = False,
                  structured_data_read: bool = False) -> dict:
    return {"actionModes": modes, "liveCatalog": live_catalog,
            "structuredDataRead": structured_data_read}


def prepare(workspace_map: Path, output: Path, state: Path) -> dict:
    source = json.loads(workspace_map.read_text())
    tag = source.get("tag")
    if not isinstance(tag, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", tag):
        raise ValueError("Workspace fixture has an invalid tag")
    if output.exists():
        result = json.loads(output.read_text())
        if result.get("tag") != tag or result.get("lanes") != source.get("lanes"):
            raise ValueError("Existing preseed output differs from the workspace fixture")
    else:
        result = {**source, "preseed": {}, "chat": {}}
        write_private(output, result)
    slots = {slot["username"]: slot for slot in registry_load(state)["slots"]}
    content = design_content(tag)
    digest = sha256(canonical(content)).hexdigest()
    for username in DESIGN_ACCOUNTS:
        fixture = source["lanes"][username]
        marker = f"qa_{tag}_{username[-3:]}_"
        if fixture.get("prefix") != marker:
            raise ValueError(f"Ownership marker mismatch for {username}")
        writer = username == "qa_designer_011"
        key = "writerWorkspaceId" if writer else "localWorkspaceId"
        workspace_id = fixture[key]
        client = Client()
        client.login(slots[username])
        try:
            _assert_workspace(client, workspace_id, None if writer else marker + "seed",
                              slots[username].get("writableConnectionId") if writer else None)
            current = client.call("GET", f"/api/v1/schemii/workspaces/{workspace_id}/design")
            recorded = result.setdefault("preseed", {}).get(username)
            if recorded:
                if (recorded.get("workspaceId") != workspace_id or recorded.get("designHash") != digest
                        or current.get("fingerprint") != recorded.get("fingerprint")):
                    raise ValueError(f"Previously seeded design changed: {username}")
                continue
            if not _empty_design(current["content"]):
                raise ValueError(f"Refusing to replace nonempty design for {username}")
            saved = client.call("PUT", f"/api/v1/schemii/workspaces/{workspace_id}/design",
                                {"expectedDesignRevision": current["revision"], "content": content})
            result["preseed"][username] = {"workspaceId": workspace_id, "marker": marker,
                                            "designHash": digest, "fingerprint": saved["fingerprint"],
                                            "tables": ["qa_projects", "qa_tasks"]}
            write_private(output, result)
        finally:
            client.logout()

    fixture = source["lanes"][CHAT_ACCOUNT]
    marker = f"qa_{tag}_001_"
    if fixture.get("prefix") != marker:
        raise ValueError("Chat workspace ownership marker differs")
    slot = slots[CHAT_ACCOUNT]
    client = Client()
    client.login(slot)
    try:
        reader_id, local_id = fixture["readerWorkspaceId"], fixture["localWorkspaceId"]
        _assert_workspace(client, reader_id, None, slot["connectionId"])
        _assert_workspace(client, local_id, marker + "seed", None)
        settings = client.call("GET", "/api/v1/schemii/ai/settings")
        provider, model = settings.get("defaultProviderId"), settings.get("defaultModelId")
        if not settings.get("enabled") or not provider or model != "gpt-6-luna":
            raise ValueError("Chat-only QA account lacks the exact active gpt-6-luna grant")
        chats = result.setdefault("chat", {}).setdefault(CHAT_ACCOUNT, {
            "readerWorkspaceId": reader_id, "localWorkspaceId": local_id, "chats": {},
        })
        if chats["readerWorkspaceId"] != reader_id or chats["localWorkspaceId"] != local_id:
            raise ValueError("Recorded chat workspace differs from the fixture")
        definitions = {
            "reader": (reader_id, _capabilities({"query.read": "ask", "query.browse": "ask",
                                                   "query.draft": "automatic"}, live_catalog=True,
                                                  structured_data_read=True)),
            "design": (local_id, _capabilities({key: "ask" for key in (
                "tables.create", "tables.update", "columns.create", "columns.update", "keys.create",
                "checks.create", "indexes.create", "relationships.create")}, live_catalog=True)),
        }
        for purpose, (workspace_id, capabilities) in definitions.items():
            expected_title = marker + purpose + "_chat"
            recorded = chats["chats"].get(purpose)
            if recorded:
                if recorded.get("workspaceId") != workspace_id or not CHAT_ID.fullmatch(recorded.get("id", "")):
                    raise ValueError("Recorded chat ownership differs")
                existing = client.call("GET", "/api/v1/schemii/ai/chats/" + recorded["id"])
                if existing["title"] != expected_title or existing["capabilities"]["actionModes"] != {
                    **existing["capabilities"]["actionModes"], **capabilities["actionModes"]}:
                    raise ValueError("Recorded chat policy differs")
                continue
            existing = next((chat for chat in client.call("GET", "/api/v1/schemii/ai/chats?workspaceId=" + workspace_id)["chats"]
                             if chat["title"] == expected_title and chat["status"] != "deleted"), None)
            if existing is None:
                chats.setdefault("pending", {})[purpose] = {"workspaceId": workspace_id, "title": expected_title}
                write_private(output, result)
                existing = client.call("POST", f"/api/v1/schemii/workspaces/{workspace_id}/ai/chats",
                                       {"title": expected_title, "providerId": provider,
                                        "modelId": model, "reasoningEffort": "default",
                                        "capabilities": capabilities}, expected=201)
            else:
                raise ValueError("Unledgered chat name collision; reconcile without adopting another creation")
            if existing["capabilities"]["actionModes"] != {
                **existing["capabilities"]["actionModes"], **capabilities["actionModes"]}:
                raise ValueError("Existing chat has different required permissions")
            chats["chats"][purpose] = {"id": existing["id"], "workspaceId": workspace_id}
            chats["pending"].pop(purpose, None)
            write_private(output, result)
    finally:
        client.logout()
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-fixtures", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, default=ROOT / ".schemii/testing")
    args = parser.parse_args()
    result = prepare(args.workspace_fixtures, args.output, args.state_dir)
    print(json.dumps({"output": str(args.output), "preseeded": sorted(result["preseed"]),
                      "chatPrepared": sorted(result["chat"])}))


if __name__ == "__main__":
    main()
