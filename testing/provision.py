#!/usr/bin/env python3
"""Durable QA identities and supported-API provisioning; never reset app metadata."""
import argparse
import fcntl
import http.cookiejar
import json
import os
from pathlib import Path
import re
import secrets
import ssl
import stat
import sys
import tempfile
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
DEFINITIONS = json.loads((Path(__file__).parent / "personas.json").read_text())
PERSONAS = {p["id"]: p for p in DEFINITIONS["personas"]}
POOL = "user_schemii_connection_pool"
SOURCE_PATHS = {"schemoo": "/api/v1/schemoo/models", "schemii": "/api/v1/schemii/workspaces", "schemer": "/api/v1/schemer/dashboards"}


def private_read(path):
    path = Path(path)
    mode = path.stat().st_mode
    if path.is_symlink() or not stat.S_ISREG(mode) or mode & 0o077:
        raise ValueError(f"Private file must be a regular mode-0600 file: {path.name}")
    return path.read_text()


def write_private(path, value, *, replace=True):
    path = Path(path)
    if path.is_symlink():
        raise ValueError(f"Refusing symlink output: {path.name}")
    content = json.dumps(value, indent=2) + "\n" if not isinstance(value, str) else value
    fd, temporary = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if replace:
            os.replace(temporary, path)
        else:
            # Atomic no-clobber publication, including concurrent export attempts.
            os.link(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def registry_load(directory):
    value = json.loads(private_read(directory / "registry.json"))
    return validate_registry(value)


def validate_registry(value):
    if not isinstance(value, dict):
        raise ValueError("QA registry must be an object")
    if value.get("version") != 1 or value.get("fixtureVersion") != DEFINITIONS["fixtureVersion"] or not isinstance(value.get("slots"), list):
        raise ValueError("Unsupported QA registry version; refusing to replace retained identities")
    usernames = set()
    for slot in value["slots"]:
        if not isinstance(slot, dict):
            raise ValueError("QA registry slots must be objects")
        name = slot.get("username", "")
        if not isinstance(slot.get("persona"), str) or slot["persona"] not in PERSONAS or not isinstance(name, str) or not re.fullmatch(r"qa_[a-z_]+_[0-9]{3}", name) or slot.get("schema") != name or name in usernames:
            raise ValueError("Invalid or duplicate QA registry slot")
        if not name.startswith("qa_" + slot["persona"] + "_"):
            raise ValueError("QA slot persona does not match its identity")
        for key in ("password", "dbPassword"):
            if not isinstance(slot.get(key), str) or not re.fullmatch(r"[0-9a-f]{64}", slot[key]):
                raise ValueError("Invalid retained QA credential; refusing regeneration")
        chat = slot.get("chatProvider")
        if chat is not None and (
            not isinstance(chat, dict) or set(chat) != {"providerId", "modelId", "reasoningEffort"}
            or chat["providerId"] != "instance-codex"
            or not isinstance(chat["modelId"], str) or not chat["modelId"]
            or not isinstance(chat["reasoningEffort"], str) or not chat["reasoningEffort"]
        ):
            raise ValueError("Invalid retained QA chat provider policy")
        writer = slot.get("writableConnectionId")
        if writer is not None and (
            slot["persona"] != "designer" or not re.fullmatch(r"pg_[0-9a-f]{32}", writer)
            or slot.get("writableSchema") != "qa_write_designer_" + name[-3:]
        ):
            raise ValueError("Invalid retained QA writer profile")
        usernames.add(name)
    return value


PORTABLE_SLOT_KEYS = {"username", "persona", "schema", "password", "dbPassword"}
BUNDLE_FORMAT = "schemii-testing-credentials-v1"


def export_credentials(directory, output):
    registry = registry_load(directory)
    admin_password = private_read(directory / "database-admin-password").rstrip("\n")
    if not re.fullmatch(r"[0-9a-f]{64}", admin_password):
        raise ValueError("Invalid retained database admin credential")
    output = Path(output).absolute()
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if output.parent.stat().st_mode & 0o077:
        raise ValueError("Credential export parent directory must be private mode 0700")
    portable_registry = {"version": registry["version"], "fixtureVersion": registry["fixtureVersion"],
        "slots": [{key: slot[key] for key in sorted(PORTABLE_SLOT_KEYS)} for slot in registry["slots"]]}
    write_private(output, {"format": BUNDLE_FORMAT, "registry": portable_registry,
        "databaseAdminPassword": admin_password}, replace=False)
    return {"exportedSlots": len(registry["slots"]), "output": str(output)}


def import_credentials(directory, source):
    bundle = json.loads(private_read(source))
    if not isinstance(bundle, dict) or set(bundle) != {"format", "registry", "databaseAdminPassword"} or bundle.get("format") != BUNDLE_FORMAT:
        raise ValueError("Unsupported QA credential bundle")
    registry = validate_registry(bundle["registry"])
    if set(registry) != {"version", "fixtureVersion", "slots"} or not registry["slots"] or any(set(slot) != PORTABLE_SLOT_KEYS for slot in registry["slots"]):
        raise ValueError("Credential bundle must contain credential-only slots without deployment IDs")
    admin_password = bundle["databaseAdminPassword"]
    if not isinstance(admin_password, str) or not re.fullmatch(r"[0-9a-f]{64}", admin_password):
        raise ValueError("Invalid database admin credential in bundle")
    if any(path.name != ".provision.lock" for path in directory.iterdir()):
        raise ValueError("Credential import requires an empty QA state directory; existing state will not be replaced")
    # All bundle validation and collision checks precede credential publication.
    write_private(directory / "database-admin-password", admin_password + "\n", replace=False)
    write_private(directory / "registry.json", registry, replace=False)
    derived_files(directory, registry)
    return {"importedSlots": len(registry["slots"]), "stateDir": str(directory), "requiresProvisioning": True}


def derived_files(directory, registry):
    slots = registry["slots"]
    write_private(directory / "credentials.json", {"accounts": [{"username": s["username"], "password": s["password"]} for s in slots]})
    write_private(directory / "database-credentials.tsv", "".join(f"{s['username']}\t{s['schema']}\t{s['dbPassword']}\n" for s in slots))
    lanes = {}
    for slot in slots:
        persona = PERSONAS[slot["persona"]]
        capabilities = sorted(persona["capabilities"] + (["accounts:provision"] if persona["isAdmin"] else []))
        denied = [p for p in SOURCE_PATHS if f"{p}:access" not in capabilities]
        checks = [{"path": "/api/v1/auth/me", "status": 200, "equals": {"user.username": slot["username"], "is_admin": persona["isAdmin"]}}]
        checks.append({"path": "/api/v1/admin/accounts", "status": 200 if persona["isAdmin"] else 403})
        checks += [{"path": endpoint, "status": 403 if product in denied else 200} for product, endpoint in SOURCE_PATHS.items()]
        lanes[slot["username"]] = {"persona": slot["persona"], "expectedCapabilities": capabilities, "deniedProducts": denied, "defaultProducts": persona["defaultProducts"],
            "resources": {"schema": slot["schema"], "connectionId": slot.get("connectionId"), "connectionOwnerId": POOL,
                "database": "schemii_qa", "host": "qa-postgres", "fixtureVersion": registry["fixtureVersion"],
                "scope": "viewer empty-state and access denial only" if persona.get("viewerOnly") else "stable QA target; no seeded saved model or dashboard"},
            "checks": checks}
        if slot.get("writableConnectionId"):
            lanes[slot["username"]]["resources"].update(
                writableConnectionId=slot["writableConnectionId"], writableSchema=slot["writableSchema"])
        if slot.get("chatProvider"):
            lanes[slot["username"]]["chatProvider"] = slot["chatProvider"]
        if not persona["product"]:
            lanes[slot["username"]]["scenarios"] = [{"id": "access-denial", "title": "Expected product access denial", "instructions": "This persona deliberately lacks product access. Verify the selected product denies entry and exposes no product controls or protected data. An access-denied response is the expected functional result; capture each viewport. Do not require an ordinary product interaction on a denied page."}]
    write_private(directory / "fixtures.json", {"fixtureVersion": registry["fixtureVersion"], "lanes": lanes})


def initialize(directory, copies):
    registry_path = directory / "registry.json"
    if registry_path.exists():
        registry = registry_load(directory)
        if not (directory / "database-admin-password").exists():
            raise ValueError("Retained database admin credential is missing; restore it instead of regenerating")
    else:
        if any((directory / name).exists() for name in ("credentials.json", "database-credentials.tsv", "fixtures.json")):
            raise ValueError("QA registry is missing but derived identity files exist; restore the registry")
        registry = {"version": 1, "fixtureVersion": DEFINITIONS["fixtureVersion"], "slots": []}
    admin_path = directory / "database-admin-password"
    if admin_path.exists():
        if not re.fullmatch(r"[0-9a-f]{64}\n?", private_read(admin_path)):
            raise ValueError("Invalid retained database admin credential")
    else:
        write_private(admin_path, secrets.token_hex(32) + "\n")
    current = {slot["username"] for slot in registry["slots"]}
    added = 0
    for persona in PERSONAS:
        for index in range(1, copies + 1):
            username = f"qa_{persona}_{index:03d}"
            if username not in current:
                registry["slots"].append({"username": username, "persona": persona, "schema": username,
                    "password": secrets.token_hex(32), "dbPassword": secrets.token_hex(32)})
                added += 1
    write_private(registry_path, registry)
    derived_files(directory, registry)
    return {"slots": len(registry["slots"]), "added": added, "personas": len(PERSONAS), "stateDir": str(directory)}


class Client:
    def __init__(self):
        self.base = "https://localhost:8001"
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
            urllib.request.HTTPSHandler(context=ssl._create_unverified_context()))

    def call(self, method, path, payload=None, expected=200):
        request = urllib.request.Request(self.base + path, method=method,
            data=json.dumps(payload).encode() if payload is not None else None,
            headers={"Origin": self.base, "Content-Type": "application/json"})
        try:
            response = self.opener.open(request, timeout=30)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            if response.status != expected:
                if method == "POST" and path == "/api/v1/admin/schemii-connections" and response.status == 409:
                    raise ValueError("Managed QA connection capacity reached or profile conflict; reduce selected author slots or review existing managed profiles. Saved account/connection IDs and credentials remain intact; the provisioner does not raise global limits")
                raise ValueError(f"QA API {method} {path} expected {expected}, received {response.status}")
            content = response.read()
            return json.loads(content) if content and expected < 400 else None

    def login(self, credentials):
        return self.call("POST", "/api/v1/auth/login", {key: credentials[key] for key in ("username", "password")})

    def logout(self):
        self.call("POST", "/api/v1/auth/logout", {})


def provision(directory, admin_file, selected):
    registry = registry_load(directory)
    admin_credentials = json.loads(private_read(admin_file))
    admin_credentials = admin_credentials.get("admin", admin_credentials)
    client = Client()
    identity = client.login(admin_credentials)
    if not identity.get("is_admin"):
        raise ValueError("Provisioning requires an application administrator")
    counts = {"createdAccounts": 0, "createdConnections": 0, "verified": 0}
    try:
        accounts = {a["username"]: a for a in client.call("GET", "/api/v1/admin/accounts")}
        profiles = client.call("GET", "/api/v1/admin/schemii-connections")["connections"]
        by_id = {p["id"]: p for p in profiles}
        names = {p["name"] for p in profiles}
        slots = [s for s in registry["slots"] if not selected or s["username"] in selected]
        if selected and len(slots) != len(selected):
            raise ValueError("Unknown selected QA account")
        # Check all collisions before any account/profile mutation.
        for slot in slots:
            existing = accounts.get(slot["username"])
            if existing and slot.get("accountId") != existing["id"]:
                raise ValueError(f"Unowned existing account collision: {slot['username']}; restore registry, do not adopt")
            if slot.get("accountId") and not existing:
                raise ValueError(f"Retained account missing: {slot['username']}; explicit recovery required")
            if not slot.get("connectionId") and "QA " + slot["username"] in names:
                raise ValueError(f"Unowned managed profile collision: {slot['username']}; restore registry")
        for slot in slots:
            persona = PERSONAS[slot["persona"]]
            slot["provisioned"] = False
            write_private(directory / "registry.json", registry)
            needs_profile = bool(persona["product"] and not persona.get("viewerOnly"))
            if needs_profile or slot.get("connectionId"):
                profile_body = {"name": "QA " + slot["username"], "host": "qa-postgres", "port": 5432,
                    "database": "schemii_qa", "username": slot["username"], "sslMode": "disable", "connectTimeout": 10}
                if slot.get("connectionId"):
                    profile = by_id.get(slot["connectionId"])
                    if not profile or any(profile.get(k) != v for k, v in profile_body.items()) or profile.get("ownerId") != POOL or not profile.get("credentialStored"):
                        raise ValueError(f"Managed profile drift: {slot['username']}")
                else:
                    profile = client.call("POST", "/api/v1/admin/schemii-connections", {**profile_body, "password": slot["dbPassword"]}, expected=201)
                    slot["connectionId"] = profile["id"]
                    write_private(directory / "registry.json", registry)
                    counts["createdConnections"] += 1
                client.call("POST", f"/api/v1/admin/schemii-connections/{slot['connectionId']}/test", {})
            grants = [{"connection_id": slot["connectionId"], "owner_id": POOL, "allow_authoring": True}] if needs_profile else []
            if slot.get("writableConnectionId"):
                grants.append({"connection_id": slot["writableConnectionId"], "owner_id": POOL, "allow_authoring": True})
            access = {"capabilities": persona["capabilities"], "connections": grants, "dashboards": []}
            expected_capabilities = sorted(persona["capabilities"] + (["accounts:provision"] if persona["isAdmin"] else []))
            if slot.get("accountId"):
                existing = accounts[slot["username"]]
                if existing["is_admin"] != persona["isAdmin"] or existing["disabled"] or existing["role_ids"] or existing["direct_access"] != access or sorted(existing["effective_capabilities"]) != expected_capabilities:
                    raise ValueError(f"Account permission drift: {slot['username']}")
            else:
                account = client.call("POST", "/api/v1/admin/accounts", {"username": slot["username"],
                    "display_name": "QA " + slot["username"], "password": slot["password"], "is_admin": persona["isAdmin"],
                    "role_ids": [], "direct_access": access}, expected=201)
                slot["accountId"] = account["id"]
                write_private(directory / "registry.json", registry)
                counts["createdAccounts"] += 1
            user = Client()
            try:
                actual = user.login(slot)
                if actual["user"]["id"] != slot["accountId"] or sorted(actual["capabilities"]) != expected_capabilities or actual["is_admin"] != persona["isAdmin"]:
                    raise ValueError(f"Effective identity drift: {slot['username']}")
                user.call("GET", "/api/v1/admin/accounts", expected=200 if persona["isAdmin"] else 403)
                for product, endpoint in SOURCE_PATHS.items():
                    user.call("GET", endpoint, expected=200 if f"{product}:access" in expected_capabilities else 403)
                if grants:
                    user.call("POST", f"/api/v1/connections/{slot['connectionId']}/test?product={persona['product']}", {})
            finally:
                user.logout()
            slot["provisioned"] = True
            write_private(directory / "registry.json", registry)
            counts["verified"] += 1
            print(json.dumps({"verifiedAccount": slot["username"], "persona": slot["persona"]}), flush=True)
        derived_files(directory, registry)
    finally:
        client.logout()
    return counts


def provision_chat(directory, admin_file, username, model_id, reasoning_effort):
    """Give one retained Schemii designer exact shared-Codex QA scopes."""
    registry = registry_load(directory)
    slot = next((item for item in registry["slots"] if item["username"] == username), None)
    if not slot or slot["persona"] != "designer" or not slot.get("provisioned") or not slot.get("accountId") or not slot.get("connectionId"):
        raise ValueError("Chat provisioning requires a provisioned designer QA account and connection")
    policy = {"providerId": "instance-codex", "modelId": model_id, "reasoningEffort": reasoning_effort}
    if slot.get("chatProvider") and slot["chatProvider"] != policy:
        raise ValueError("Retained chat model policy differs; review it before changing grants")
    admin_credentials = json.loads(private_read(admin_file))
    admin_credentials = admin_credentials.get("admin", admin_credentials)
    client = Client()
    created = 0
    try:
        if not client.login(admin_credentials).get("is_admin"):
            raise ValueError("Chat provisioning requires an application administrator")
        state = client.call("GET", "/api/v1/admin/ai/shared-codex")
        if not state.get("connected"):
            raise ValueError("Shared ChatGPT Codex is not connected; connect and test it in admin settings")
        verified = client.call("POST", "/api/v1/admin/ai/shared-codex/test", {})
        model = next((item for item in verified.get("models", []) if item.get("id") == model_id), None)
        if not model or reasoning_effort not in model.get("reasoningLevels", ["default"]):
            raise ValueError("Shared ChatGPT Codex model or reasoning is not available to this installation")
        state = client.call("GET", "/api/v1/admin/ai/shared-codex")
        for connection_owner_id, connection_id in ((None, None), (POOL, slot["connectionId"])):
            body = {"userId": slot["accountId"], "product": "schemii",
                    "connectionOwnerId": connection_owner_id, "connectionId": connection_id,
                    "modelId": model_id, "reasoningEffort": reasoning_effort}
            existing = [grant for grant in state.get("grants", []) if all(
                grant.get(key) == body[key] for key in ("userId", "product", "connectionOwnerId", "connectionId"))]
            if existing:
                if len(existing) != 1 or any(existing[0].get(key) != body[key] for key in ("modelId", "reasoningEffort")):
                    raise ValueError("Existing chat grant policy differs; review it before changing grants")
                continue
            client.call("PUT", "/api/v1/admin/ai/shared-codex/grants", body)
            created += 1
        user = Client()
        try:
            identity = user.login(slot)
            if identity["user"]["id"] != slot["accountId"]:
                raise ValueError("Retained designer account identity changed")
            status = user.call("GET", "/api/v1/ai/status")
            provider = next((item for item in status.get("providers", []) if item.get("id") == "instance-codex"), None)
            if not provider or not provider.get("available") or not any(
                item.get("id") == model_id and item.get("status") == "active"
                for item in provider.get("models", [])
            ):
                raise ValueError("Designer chat model is still unavailable after granting access")
        finally:
            try:
                user.logout()
            except Exception:
                pass
        slot["chatProvider"] = policy
        write_private(directory / "registry.json", registry)
        derived_files(directory, registry)
        return {"account": username, "provider": policy, "createdGrants": created, "verified": True}
    finally:
        try:
            client.logout()
        except Exception:
            pass


def provision_writer(directory, admin_file, username):
    """Attach one separately marked QA writer target to a retained designer."""
    registry = registry_load(directory)
    slot = next((item for item in registry["slots"] if item["username"] == username), None)
    if not slot or slot["persona"] != "designer" or not slot.get("provisioned") or not slot.get("accountId"):
        raise ValueError("Writer provisioning requires a provisioned designer QA account")
    writer = "qa_write_designer_" + username[-3:]
    rows = [row.split("\t") for row in private_read(directory / "writable-credentials.tsv").splitlines() if row]
    if any(len(row) != 4 or not re.fullmatch(r"qa_write_designer_[0-9]{3}", row[0])
           or row[0] != row[1] or not re.fullmatch(r"[0-9a-f]{64}", row[2])
           or row[3] != "qa_designer_" + row[0][-3:] for row in rows):
        raise ValueError("Invalid private QA writer credential registry")
    match = [row for row in rows if row[0] == writer and row[3] == username]
    if len(match) != 1 or len({row[0] for row in rows}) != len(rows):
        raise ValueError("Exact prepared QA writer target is missing or duplicated")
    if slot.get("writableSchema") not in (None, writer):
        raise ValueError("Retained writer target differs; review before changing")
    admin_credentials = json.loads(private_read(admin_file))
    admin_credentials = admin_credentials.get("admin", admin_credentials)
    client = Client()
    created = 0
    try:
        if not client.login(admin_credentials).get("is_admin"):
            raise ValueError("Writer provisioning requires an application administrator")
        profiles = client.call("GET", "/api/v1/admin/schemii-connections")["connections"]
        by_id = {profile["id"]: profile for profile in profiles}
        profile_body = {"name": "QA writable " + username, "host": "qa-postgres", "port": 5432,
                        "database": "schemii_qa", "username": writer, "sslMode": "disable", "connectTimeout": 10}
        connection_id = slot.get("writableConnectionId")
        if connection_id:
            profile = by_id.get(connection_id)
            if not profile or any(profile.get(key) != value for key, value in profile_body.items()) or profile.get("ownerId") != POOL or not profile.get("credentialStored"):
                raise ValueError("Retained QA writer profile drift")
        else:
            if any(profile.get("name") == profile_body["name"] for profile in profiles):
                raise ValueError("Unowned QA writer profile collision; restore the retained registry")
            profile = client.call("POST", "/api/v1/admin/schemii-connections",
                                  {**profile_body, "password": match[0][2]}, expected=201)
            connection_id = profile["id"]
            slot["writableConnectionId"] = connection_id
            slot["writableSchema"] = writer
            write_private(directory / "registry.json", registry)
            created = 1
        client.call("POST", f"/api/v1/admin/schemii-connections/{connection_id}/test", {})
        account = next((item for item in client.call("GET", "/api/v1/admin/accounts")
                        if item["id"] == slot["accountId"] and item["username"] == username), None)
        if not account or account["disabled"] or account["role_ids"]:
            raise ValueError("Retained QA designer identity changed")
        base_grant = {"connection_id": slot["connectionId"], "owner_id": POOL, "allow_authoring": True}
        writer_grant = {"connection_id": connection_id, "owner_id": POOL, "allow_authoring": True}
        expected_base = {"capabilities": ["schemii:access"], "connections": [base_grant], "dashboards": []}
        expected_full = {"capabilities": ["schemii:access"], "connections": [base_grant, writer_grant], "dashboards": []}
        if account["direct_access"] == expected_base:
            client.call("PATCH", f"/api/v1/admin/accounts/{slot['accountId']}", {"direct_access": expected_full})
        elif account["direct_access"] != expected_full:
            raise ValueError("Retained QA designer has unrelated access; refusing to replace grants")
        user = Client()
        try:
            identity = user.login(slot)
            if identity["user"]["id"] != slot["accountId"] or "schemii:access" not in identity["capabilities"]:
                raise ValueError("QA writer designer login drift")
            user.call("POST", f"/api/v1/connections/{connection_id}/test?product=schemii", {})
        finally:
            user.logout()
        slot["writableConnectionId"] = connection_id
        slot["writableSchema"] = writer
        write_private(directory / "registry.json", registry)
        derived_files(directory, registry)
        return {"account": username, "writableSchema": writer, "connectionId": connection_id,
                "createdProfiles": created, "verified": True}
    finally:
        client.logout()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    initialize_parser = subparsers.add_parser("init", help="Create/extend retained persona slots without rotating credentials")
    initialize_parser.add_argument("--copies", type=int, default=20)
    provision_parser = subparsers.add_parser("app", help="Provision and verify accounts through the supported local HTTPS API")
    provision_parser.add_argument("--admin-credentials", type=Path, required=True)
    provision_parser.add_argument("--accounts", help="Optional comma-separated retained usernames")
    chat_parser = subparsers.add_parser("chat", help="Provision one retained designer for shared-Codex chat testing")
    chat_parser.add_argument("--admin-credentials", type=Path, required=True)
    chat_parser.add_argument("--account", required=True)
    chat_parser.add_argument("--model", default="gpt-6-luna")
    chat_parser.add_argument("--reasoning", default="default")
    writer_parser = subparsers.add_parser("writer", help="Provision one exact marked QA writer target in Schemii")
    writer_parser.add_argument("--admin-credentials", type=Path, required=True)
    writer_parser.add_argument("--account", required=True)
    export_parser = subparsers.add_parser("export", help="Export private credentials without deployment-specific IDs")
    export_parser.add_argument("--output", type=Path, required=True)
    import_parser = subparsers.add_parser("import", help="Import private credentials into an empty QA state directory")
    import_parser.add_argument("--input", type=Path, required=True)
    for child in (initialize_parser, provision_parser, chat_parser, writer_parser, export_parser, import_parser):
        child.add_argument("--state-dir", type=Path, default=Path(os.environ.get("SCHEMII_QA_STATE_DIRECTORY", ROOT / ".schemii/testing")))
    args = parser.parse_args()
    if args.command == "init" and not 1 <= args.copies <= 100:
        parser.error("--copies must be between 1 and 100")
    directory = args.state_dir.resolve()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if directory.stat().st_mode & 0o077:
        raise ValueError("QA state directory must be private mode 0700")
    lock_fd = os.open(directory / ".provision.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(lock_fd, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        selected = set(args.accounts.split(",")) if args.command == "app" and args.accounts else None
        if args.command == "init":
            result = initialize(directory, args.copies)
        elif args.command == "app":
            result = provision(directory, args.admin_credentials, selected)
        elif args.command == "chat":
            result = provision_chat(directory, args.admin_credentials, args.account, args.model, args.reasoning)
        elif args.command == "writer":
            result = provision_writer(directory, args.admin_credentials, args.account)
        elif args.command == "export":
            result = export_credentials(directory, args.output)
        else:
            result = import_credentials(directory, args.input)
        print(json.dumps(result))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, KeyError, urllib.error.URLError) as error:
        # Never echo response bodies or secret-containing request payloads.
        print(f"QA provisioning failed: {error}", file=sys.stderr)
        sys.exit(2)
