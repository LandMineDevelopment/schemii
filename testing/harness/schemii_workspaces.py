"""Seed and record exact owned workspace prerequisites for the Schemii sweep.

The private journal is updated before and after each create call. A returned ID
is an ownership receipt; a name matching an interrupted unreceipted call is
ambiguous and must be reconciled without automatic adoption.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re

from testing.harness.schemii_sweep import ACCOUNTS, ROOT, WRITERS
from testing.provision import Client, private_read, registry_load, write_private


def journal_path(output: Path) -> Path:
    return output.with_name(output.name + ".journal.json")


def seed(state: Path, tag: str, output: Path, accounts=ACCOUNTS) -> dict:
    if (not accounts or len(set(accounts)) != len(accounts)
            or any(not re.fullmatch(r"qa_designer_[0-9]{3}", account) for account in accounts)):
        raise ValueError("Seed accounts must be unique exact retained designer usernames")
    journal_file = journal_path(output)
    if output.exists():
        raise ValueError("Output already exists; use a unique run tag")
    if journal_file.exists():
        result = json.loads(private_read(journal_file))
        if (result.get("tag") != tag or not isinstance(result.get("lanes"), dict)
                or not set(result["lanes"]).issubset(accounts)):
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
    for username in accounts:
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
                matches = [item for item in known if match(item)]
                if len(matches) > 1:
                    raise ValueError(f"Ambiguous {kind} workspace matches for {username}; inspect journal")
                existing = matches[0] if matches else None
                recorded = lane.get(key)
                if recorded:
                    if not existing or existing["id"] != recorded or type(lane.get(created_key)) is not bool:
                        raise ValueError(f"Recorded {kind} workspace changed for {username}; inspect journal")
                    return recorded
                if existing:
                    if lane.get(pending_key) is True:
                        raise ValueError(f"Ambiguous unreceipted {kind} creation for {username}; reconcile journal without adopting a name match")
                    if kind == "local":
                        raise ValueError(f"Unowned local workspace name collision for {username}; use a fresh tag")
                    lane.update({key: existing["id"], created_key: False})
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
    parser.add_argument("--accounts", help="Exact retained designer seed subset, comma separated")
    args = parser.parse_args()
    if not args.tag or len(args.tag) > 32 or not args.tag.replace("-", "").replace("_", "").isalnum():
        parser.error("--tag must be 1..32 alphanumeric, dash or underscore characters")
    accounts = tuple(args.accounts.split(",")) if args.accounts else ACCOUNTS
    result = seed(args.state_dir, args.tag, args.output, accounts)
    print(json.dumps({"output": str(args.output), "accounts": len(result["lanes"]),
                      "createdLocal": sum(item["localCreated"] for item in result["lanes"].values()),
                      "createdReader": sum(item["readerCreated"] for item in result["lanes"].values()),
                      "createdWriter": sum(item["writerCreated"] for item in result["lanes"].values())}))


if __name__ == "__main__":
    main()
