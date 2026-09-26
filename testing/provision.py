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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    initialize_parser = subparsers.add_parser("init", help="Create/extend retained persona slots without rotating credentials")
    initialize_parser.add_argument("--copies", type=int, default=20)
    provision_parser = subparsers.add_parser("app", help="Provision and verify accounts through the supported local HTTPS API")
    provision_parser.add_argument("--admin-credentials", type=Path, required=True)
    provision_parser.add_argument("--accounts", help="Optional comma-separated retained usernames")
    export_parser = subparsers.add_parser("export", help="Export private credentials without deployment-specific IDs")
    export_parser.add_argument("--output", type=Path, required=True)
    import_parser = subparsers.add_parser("import", help="Import private credentials into an empty QA state directory")
    import_parser.add_argument("--input", type=Path, required=True)
    for child in (initialize_parser, provision_parser, export_parser, import_parser):
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
