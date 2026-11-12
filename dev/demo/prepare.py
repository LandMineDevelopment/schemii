"""Prepare an owned interview demo; invoked only by the canonical launcher.

Host request/verify commands use the standard library. The provision command runs
inside the application image, using its existing services and authorization store.
"""

from __future__ import annotations

import argparse
import hmac
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
import urllib.parse
import urllib.request

USERNAME = "demo-admin"
CONNECTION_NAME = "Schemii interview demo"
CAPABILITIES = {
    "accounts:provision",
    "schemii:access",
    "schemoo:access",
    "schemer:access",
    "schemer:author",
}
PRODUCTS = sorted(CAPABILITIES - {"accounts:provision"})
PREVIEW_ORIGIN = "https://omarchy.taile4f57f.ts.net"


class DemoError(RuntimeError):
    """A safe actionable failure, containing no credentials or server response."""


def _no_symlinks(path: Path) -> None:
    if not path.is_absolute():
        raise DemoError("Demo state and secret paths must be absolute.")
    for part in [*reversed(path.parents), path]:
        if part.is_symlink():
            raise DemoError(
                "Demo state and secret paths must not contain symbolic links."
            )


def state_directory(path: Path) -> Path:
    _no_symlinks(path)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.stat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_IMODE(info.st_mode) != 0o700
        or info.st_uid != os.getuid()
    ):
        raise DemoError("Demo state must be an owned directory with mode 700.")
    return path


def read_private(path: Path, *, secret: bool = False) -> str:
    _no_symlinks(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "r", encoding="utf-8") as source:
        info = os.fstat(source.fileno())
        allowed = {0o600, 0o640} if secret else {0o600}
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_IMODE(info.st_mode) not in allowed
            or info.st_uid != os.getuid()
            or info.st_size > 16_384
        ):
            raise DemoError(
                "Demo credential files must be owned, private regular files."
            )
        return source.read()


def write_private(path: Path, text: str, *, create: bool = False) -> None:
    _no_symlinks(path)
    if create:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as destination:
            destination.write(text)
        return
    if path.exists():
        read_private(path)
    fd, temporary = tempfile.mkstemp(prefix=".demo-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as destination:
            destination.write(text)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def credentials(directory: Path) -> dict:
    path = directory / "credentials.json"
    if not path.exists() and not path.is_symlink():
        write_private(
            path,
            json.dumps({"username": USERNAME, "password": secrets.token_urlsafe(24)})
            + "\n",
            create=True,
        )
    value = json.loads(read_private(path))
    if (
        not isinstance(value, dict)
        or set(value) != {"username", "password"}
        or value["username"] != USERNAME
        or not isinstance(value["password"], str)
        or not 12 <= len(value["password"]) <= 256
    ):
        raise DemoError(
            "Retained demo credentials are invalid; they were not replaced."
        )
    return value


def validate_result(result: dict) -> dict:
    if not isinstance(result, dict) or not isinstance(result.get("user_id"), str):
        raise DemoError("Demo provisioning did not return a valid account.")
    for name, prefix in (("connection_id", "pg"), ("workspace_id", "ws")):
        if not isinstance(result.get(name), str) or not re.fullmatch(
            prefix + r"_[0-9a-f]{32}", result[name]
        ):
            raise DemoError("Demo provisioning did not return valid saved resources.")
    if (
        result.get("namespace") != "bookstore"
        or not isinstance(result.get("database"), str)
        or not result["database"]
    ):
        raise DemoError("Demo provisioning did not return the bookstore target.")
    return {
        key: result[key]
        for key in ("user_id", "connection_id", "workspace_id", "database", "namespace")
    }


def request_payload(
    directory: Path, database: str, username: str, password_file: Path
) -> dict:
    directory = state_directory(directory)
    saved = credentials(directory)
    password = read_private(password_file, secret=True).strip()
    if not password or "\n" in password or not database or not username:
        raise DemoError("The retained demo database identity is invalid.")
    resources = directory / "resources.json"
    return {
        "credentials": saved,
        "target": {
            "host": "demo-postgres",
            "port": 5432,
            "database": database,
            "username": username,
            "password": password,
            "namespace": "bookstore",
        },
        "resources": validate_result(json.loads(read_private(resources)))
        if resources.exists() or resources.is_symlink()
        else None,
    }


def provision(payload: dict, *, services=None, auth=None) -> dict:
    from schemii.common.auth.routes import AccountProvision, RoleAccess
    from schemii.common.auth.service import AuthService, password_matches
    from schemii.common.connections.models import PostgresConnectionCreate
    from schemii.schemii.designs.importer import import_postgres_catalog
    from schemii.schemii.workspaces.models import WorkspaceCreateRecord

    owned_services = services is None
    if owned_services:
        from schemii.main import app

        services = app.state.services
        auth = app.state.auth
    elif auth is None:
        auth = AuthService(services.metadata.connection_factory)
    try:
        if not auth.enabled:
            raise DemoError("The demo requires authenticated application mode.")
        saved, target = payload["credentials"], payload["target"]
        if (
            saved.get("username") != USERNAME
            or target.get("host") != "demo-postgres"
            or target.get("port") != 5432
            or target.get("namespace") != "bookstore"
        ):
            raise DemoError("The demo provisioning request has an invalid identity.")
        retained = (
            validate_result(payload["resources"])
            if payload.get("resources") is not None
            else None
        )
        with auth.store.transaction() as state:
            user = next(
                (
                    value.copy()
                    for value in state["users"].values()
                    if value["username"] == USERNAME
                ),
                None,
            )
        if user is None:
            if retained:
                raise DemoError(
                    "The retained demo account is missing; no replacement was created."
                )
            account = AccountProvision(
                username=USERNAME,
                display_name="Schemii demo administrator",
                password=saved["password"],
                is_admin=True,
                direct_access=RoleAccess(capabilities=PRODUCTS),
            )
            user = (
                auth.create_user(account, setup_token=auth.setup_token)
                if auth.setup_required()
                else auth.create_user(account)
            )
        elif (
            not password_matches(saved["password"], user["password_hash"])
            or user["disabled"]
            or not CAPABILITIES <= set(auth.capabilities(user["id"]))
        ):
            raise DemoError(
                "The demo account already exists with different credentials or permissions; it was not changed."
            )
        owner = user["id"]
        if retained and retained["user_id"] != owner:
            raise DemoError(
                "The retained demo account identity changed; existing data was preserved."
            )
        if retained:
            connection = services.connections.get(owner, retained["connection_id"])
        else:
            matches = [
                item
                for item in services.connections.list(owner)
                if item.name == CONNECTION_NAME
            ]
            if len(matches) > 1:
                raise DemoError(
                    "Multiple demo connections exist; choose one explicitly before continuing."
                )
            connection = (
                matches[0]
                if matches
                else services.connections.create(
                    owner,
                    PostgresConnectionCreate(
                        name=CONNECTION_NAME,
                        host=target["host"],
                        port=target["port"],
                        database=target["database"],
                        username=target["username"],
                        password=target["password"],
                        ssl_mode="disable",
                    ),
                )
            )
        if (
            any(
                getattr(connection, key) != target[key]
                for key in ("host", "port", "database", "username")
            )
            or connection.ssl_mode != "disable"
            or (connection.owner_id or owner) != owner
        ):
            raise DemoError(
                "The saved demo connection targets a different database; it was not changed."
            )
        with services.connections.use(owner, connection.id) as resolved:
            if resolved.password is None or not hmac.compare_digest(
                resolved.password.get_secret_value(), target["password"]
            ):
                raise DemoError(
                    "The saved demo database credential changed; it was not replaced."
                )
            tested = services.postgres.test_connection(resolved)
            if not tested.ok or tested.database != target["database"]:
                raise DemoError(
                    "The demo database connection did not pass its readiness check."
                )
            catalog = services.postgres.introspect(resolved, "bookstore")
            if (
                catalog.database != target["database"]
                or catalog.namespace != "bookstore"
                or not {"orders", "books", "customers"}
                <= {table.name for table in catalog.tables}
            ):
                raise DemoError(
                    "The bookstore demo schema is missing its seeded tables."
                )
            if retained:
                workspace = services.workspaces.get(owner, retained["workspace_id"])
            else:
                workspace = services.workspaces.find_by_target(
                    owner, connection.id, target["database"], "bookstore"
                )
            if workspace is None:
                if services.migrations is None:
                    raise DemoError("The demo requires the workspace import service.")
                workspace = services.migrations.create_import_workspace(
                    owner,
                    WorkspaceCreateRecord(
                        name="Bookstore demo",
                        connection_id=connection.id,
                        connection_owner_id=owner,
                        database=target["database"],
                        namespace="bookstore",
                    ),
                    import_postgres_catalog(catalog),
                    connection.revision,
                    catalog,
                )
            if (
                workspace.connection_id != connection.id
                or workspace.database != target["database"]
                or workspace.namespace != "bookstore"
                or (workspace.connection_owner_id or owner) != owner
            ):
                raise DemoError(
                    "The saved demo workspace targets a different database; it was not changed."
                )
        return validate_result(
            {
                "user_id": owner,
                "connection_id": connection.id,
                "workspace_id": workspace.id,
                "database": target["database"],
                "namespace": "bookstore",
            }
        )
    finally:
        if owned_services:
            services.metadata.close()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise DemoError(
            "The demo origin redirected unexpectedly; credentials were not forwarded."
        )


class Api:
    def __init__(self, origin: str, *, local: bool):
        parsed = urllib.parse.urlsplit(origin)
        if (
            parsed.scheme != "https"
            or parsed.username
            or parsed.password
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            raise DemoError("Demo verification requires exact HTTPS origins.")
        if (local and (parsed.hostname != "localhost" or parsed.port is None)) or (
            not local and origin != PREVIEW_ORIGIN
        ):
            raise DemoError(
                "Demo verification only accepts the canonical local and Tailscale origins."
            )
        self.origin = origin
        context = (
            ssl._create_unverified_context() if local else ssl.create_default_context()
        )
        self.client = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=context),
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
            NoRedirect(),
        )

    def call(self, path: str, data=None):
        body = json.dumps(data).encode() if data is not None else None
        request = urllib.request.Request(
            self.origin + path,
            data=body,
            headers={"Origin": self.origin, "Content-Type": "application/json"},
        )
        try:
            with self.client.open(request, timeout=30) as response:
                if response.status != 200:
                    raise DemoError("A demo verification request failed.")
                return json.load(response)
        except DemoError:
            raise
        except Exception:
            raise DemoError(
                "Demo verification could not complete an authenticated HTTPS request."
            ) from None


def verify(
    directory: Path, result: dict, origin: str, preview_origin: str, *, api_factory=Api
) -> dict:
    directory = state_directory(directory)
    saved = credentials(directory)
    result = validate_result(result)
    resources_path = directory / "resources.json"
    if (
        resources_path.exists()
        and validate_result(json.loads(read_private(resources_path))) != result
    ):
        raise DemoError(
            "The retained demo resource identities changed; existing state was preserved."
        )
    write_private(resources_path, json.dumps(result, indent=2) + "\n")
    observations = []
    for address, local in ((origin, True), (preview_origin, False)):
        api = api_factory(address, local=local)
        signed_in = False
        try:
            ready = api.call("/api/v1/readiness")
            if ready.get("ready") is not True or ready.get("persistence") != "durable":
                raise DemoError(
                    "The application metadata backend is not ready and durable."
                )
            login = api.call("/api/v1/auth/login", saved)
            signed_in = True
            if (
                login.get("is_admin") is not True
                or login.get("user", {}).get("id") != result["user_id"]
                or not CAPABILITIES <= set(login.get("capabilities", []))
            ):
                raise DemoError(
                    "The demo login does not have the expected administrator and product access."
                )
            tested = api.call(
                "/api/v1/connections/" + result["connection_id"] + "/test", {}
            )
            if (
                tested.get("ok") is not True
                or tested.get("database") != result["database"]
            ):
                raise DemoError(
                    "The demo account cannot connect to its seeded PostgreSQL database."
                )
            root = "/api/v1/schemii/workspaces/" + result["workspace_id"]
            workspace = api.call(root)
            if (
                workspace.get("id") != result["workspace_id"]
                or workspace.get("connectionId") != result["connection_id"]
                or workspace.get("database") != result["database"]
                or workspace.get("namespace") != "bookstore"
            ):
                raise DemoError(
                    "The demo account cannot open its saved bookstore workspace."
                )
            listing = api.call(root + "/relations?pageSize=250")
            relations = listing.get("relations", [])
            if listing.get("workspaceId") != result["workspace_id"] or not {
                "orders",
                "books",
                "customers",
            } <= {
                item.get("name") for item in relations if item.get("kind") == "table"
            }:
                raise DemoError("The authenticated bookstore catalog is incomplete.")
            orders = next(
                item
                for item in relations
                if item["name"] == "orders" and item.get("kind") == "table"
            )
            ref = orders.get("ref", "")
            if not re.fullmatch(r"rel_[A-Za-z0-9_-]{16,256}", ref):
                raise DemoError(
                    "The demo catalog returned an invalid relation identity."
                )
            page = api.call(root + "/relations/" + ref + "/rows?pageSize=5")
            if (
                page.get("workspaceId") != result["workspace_id"]
                or page.get("relationRef") != ref
                or not page.get("rows")
            ):
                raise DemoError(
                    "The demo account could not read seeded bookstore order rows."
                )
            observations.append(
                {
                    "origin": address,
                    "relations": len(relations),
                    "sample_rows": len(page["rows"]),
                }
            )
        finally:
            if signed_in:
                api.call("/api/v1/auth/logout", {})
    links = [
        address + "/?workspace=" + result["workspace_id"]
        for address in (origin, preview_origin)
    ]
    write_private(
        directory / "login.txt",
        "Schemii demo\n\nLocal workspace: "
        + links[0]
        + "\nTailscale workspace: "
        + links[1]
        + "\nUsername: "
        + saved["username"]
        + "\nPassword: "
        + saved["password"]
        + "\n",
    )
    return {
        "workspace_urls": links,
        "credentials_file": str(directory / "login.txt"),
        "checks": observations,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    request = actions.add_parser("request")
    request.add_argument("--state-directory", type=Path, required=True)
    request.add_argument("--target-database", required=True)
    request.add_argument("--target-username", required=True)
    request.add_argument("--target-password-file", type=Path, required=True)
    actions.add_parser("provision")
    check = actions.add_parser("verify")
    check.add_argument("--state-directory", type=Path, required=True)
    check.add_argument("--origin", required=True)
    check.add_argument("--preview-origin", required=True)
    args = parser.parse_args(argv)
    try:
        if args.action == "request":
            print(
                json.dumps(
                    request_payload(
                        args.state_directory,
                        args.target_database,
                        args.target_username,
                        args.target_password_file,
                    )
                )
            )
        elif args.action == "provision":
            print(json.dumps(provision(json.load(sys.stdin))))
        else:
            result = verify(
                args.state_directory,
                json.load(sys.stdin),
                args.origin,
                args.preview_origin,
            )
            print(
                "Schemii demo is ready: authenticated administrator, live bookstore catalog and seeded order rows verified through both HTTPS origins."
            )
            print("Demo workspace: " + result["workspace_urls"][0])
            print("Remote demo workspace: " + result["workspace_urls"][1])
            print("Admin credentials: " + result["credentials_file"])
        return 0
    except DemoError as error:
        print("Schemii demo error: " + str(error), file=sys.stderr)
    except Exception:
        print(
            "Schemii demo error: preparation failed; retained accounts, credentials and saved work were not reset.",
            file=sys.stderr,
        )
    return 1


if __name__ == "__main__":
    sys.exit(main())
