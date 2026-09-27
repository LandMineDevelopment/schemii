"""Seed and record exact owned workspace prerequisites for the Schemii sweep.

The private journal is updated before and after each create call. A retry can
reconcile an interrupted call without adopting workspaces that already existed.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from testing.harness.schemii_sweep import ACCOUNTS, ROOT, WRITERS
from testing.provision import Client, private_read, registry_load, write_private


def journal_path(output: Path) -> Path:
    return output.with_name(output.name + ".journal.json")


def seed(state: Path, tag: str, output: Path) -> dict:
    journal_file = journal_path(output)
    if output.exists():
        raise ValueError("Output already exists; use a unique run tag")
    if journal_file.exists():
        result = json.loads(private_read(journal_file))
        if result.get("tag") != tag or not isinstance(result.get("lanes"), dict):
            raise ValueError("Existing ownership journal belongs to another tag")
    else:
        output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        result = {"tag": tag, "lanes": {}}
        write_private(journal_file, result, replace=False)

    def record(username: str, lane: dict) -> None:
        result["lanes"][username] = lane
        write_private(journal_file, result)

    registry = registry_load(state)
    slots = {slot["username"]: slot for slot in registry["slots"]}
    for username in ACCOUNTS:
        slot = slots[username]
        if not slot.get("provisioned") or not slot.get("connectionId"):
            raise ValueError(f"Designer account lacks retained connection: {username}")
        if username in WRITERS and not slot.get("writableConnectionId"):
            raise ValueError(f"Writer profile missing for {username}")
        prefix = f"qa_{tag}_{username[-3:]}_"
        lane = result["lanes"].get(username, {"prefix": prefix})
        if lane.get("prefix") != prefix:
            raise ValueError(f"Ownership journal prefix differs for {username}")
        client = Client()
        client.login(slot)
        try:
            known = client.call("GET", "/api/v1/schemii/workspaces")["workspaces"]

            def workspace(kind: str, match, create) -> str:
                key, created_key, pending_key = kind + "WorkspaceId", kind + "Created", kind + "Pending"
                existing = next((item for item in known if match(item)), None)
                recorded = lane.get(key)
                if recorded:
                    if not existing or existing["id"] != recorded or type(lane.get(created_key)) is not bool:
                        raise ValueError(f"Recorded {kind} workspace changed for {username}; inspect journal")
                    return recorded
                if existing:
                    # A pending create was written only after an earlier GET found no
                    # match. The returned object is the interrupted call's result.
                    created = lane.get(pending_key) is True
                    lane.update({key: existing["id"], created_key: created})
                    lane.pop(pending_key, None)
                    record(username, lane)
                    return existing["id"]
                lane[pending_key] = True
                record(username, lane)
                item = create()
                known.append(item)
                lane.update({key: item["id"], created_key: True})
                lane.pop(pending_key)
                record(username, lane)
                return item["id"]

            local_name = prefix + "seed"
            workspace("local", lambda item: item["name"] == local_name and item.get("connectionId") is None,
                      lambda: client.call("POST", "/api/v1/schemii/workspaces", {"name": local_name}, expected=201))

            def postgres_workspace(kind: str, connection_id: str, namespace: str) -> None:
                workspace(kind,
                          lambda item: item.get("connectionId") == connection_id
                          and item.get("database") == "schemii_qa" and item.get("namespace") == namespace,
                          lambda: client.call("POST", "/api/v1/schemii/workspaces/postgres",
                                              {"connectionId": connection_id, "namespace": namespace},
                                              expected=201)["workspace"])

            postgres_workspace("reader", slot["connectionId"], username)
            if username in WRITERS:
                postgres_workspace("writer", slot["writableConnectionId"], slot["writableSchema"])
            else:
                lane.update({"writerWorkspaceId": None, "writerCreated": False})
                record(username, lane)
        finally:
            client.logout()
    write_private(output, result, replace=False)
    journal_file.unlink()
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--state-dir", type=Path, default=ROOT / ".schemii/testing")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.tag or len(args.tag) > 32 or not args.tag.replace("-", "").replace("_", "").isalnum():
        parser.error("--tag must be 1..32 alphanumeric, dash or underscore characters")
    result = seed(args.state_dir, args.tag, args.output)
    print(json.dumps({"output": str(args.output), "accounts": len(result["lanes"]),
                      "createdLocal": sum(item["localCreated"] for item in result["lanes"].values()),
                      "createdReader": sum(item["readerCreated"] for item in result["lanes"].values()),
                      "createdWriter": sum(item["writerCreated"] for item in result["lanes"].values())}))


if __name__ == "__main__":
    main()
