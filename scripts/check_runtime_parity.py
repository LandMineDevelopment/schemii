#!/usr/bin/env python3
"""Check the supported deployment adapters without starting services or reading state.

The Compose reader deliberately accepts only the checked-in indented mapping
surface we own. It does not interpolate ambient environment or execute Docker.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import tempfile
import tomllib

ROOT = Path(__file__).resolve().parents[1]


class ParityError(ValueError):
    """Deployment source differs from the reviewed runtime contract."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ParityError(message)


def sections(text: str) -> dict[str, str]:
    """Read direct service blocks, rejecting duplicate names and unknown syntax."""
    body = text.split("\nservices:\n", 1)[1]
    body = re.split(r"\n(?=[^\s#])", body, maxsplit=1)[0]
    matches = list(re.finditer(r"^  ([\w-]+):\s*$", body, re.M))
    result = {}
    for i, match in enumerate(matches):
        name = match[1]
        require(name not in result, f"duplicate service: {name}")
        result[name] = body[
            match.end() : matches[i + 1].start() if i + 1 < len(matches) else len(body)
        ]
    require(bool(result), "no Compose services discovered")
    return result


def environment(service: str) -> dict[str, str]:
    match = re.search(r"^    environment:\n((?:^      .*\n)+)", service, re.M)
    require(match is not None, "missing Compose environment mapping")
    assert match
    result = {}
    for line in match[1].splitlines():
        entry = re.fullmatch(r"      ([A-Z][A-Z0-9_]*): (.+)", line)
        require(entry is not None, "unsupported Compose environment syntax")
        assert entry
        key, value = entry.groups()
        require(key not in result, f"duplicate environment key: {key}")
        value = value.strip("\"'")
        # Only accept a single default expression, never arbitrary interpolation.
        default = re.fullmatch(r"\$\{" + key + r":-([^{}]+)\}", value)
        result[key] = default[1] if default else value
    return result


def postgres_settings(text: str) -> dict[str, str]:
    """Read the complete generated cluster config, rejecting duplicate overrides."""
    settings = {}
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = re.fullmatch(r"([a-z_]+)\s*=\s*(.+)", line)
        require(match is not None, "unsupported PostgreSQL configuration")
        assert match
        name, value = match.groups()
        require(name not in settings, f"duplicate PostgreSQL setting: {name}")
        settings[name] = value.strip()
    return settings


def source_module(root: Path, name: str):
    spec = importlib.util.spec_from_file_location(
        "parity_" + name.replace("/", "_"), root / name
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check(root: Path = ROOT) -> dict:
    contract = json.loads((root / "dev/runtime-contract.json").read_text())
    require(contract["schema"] == 1, "unsupported runtime contract")
    policy = contract["postgresql_policy"]
    require(
        policy
        == {
            "cluster_names": ["metadata", "target"],
            "settings": {
                "fsync": "on",
                "synchronous_commit": "on",
                "full_page_writes": "on",
                "password_encryption": "'scram-sha-256'",
            },
            "application_role_flags": [
                "LOGIN",
                "NOSUPERUSER",
                "NOCREATEDB",
                "NOCREATEROLE",
                "NOINHERIT",
                "NOREPLICATION",
            ],
        },
        "shared PostgreSQL safety policy drift",
    )
    for platform in ("docker", "android"):
        profile = contract["platforms"][platform]
        require(
            bool(profile["reason"].strip()), f"{platform}: exception reason required"
        )
        admission = profile["target_admission"]
        require(
            1 <= admission["maximum_per_identity"] <= admission["maximum_total"],
            "invalid target admission",
        )
        metadata = int(
            contract["shared_environment"]["SCHEMII_METADATA_MAXIMUM_CONNECTIONS"]
        )
        require(
            profile["metadata_reserve"] >= 8
            and metadata + profile["metadata_reserve"]
            <= profile["postgres_max_connections"],
            f"{platform}: metadata/recovery headroom",
        )
    for name, digest in contract["dependency_inputs"].items():
        require(
            hashlib.sha256((root / name).read_bytes()).hexdigest() == digest,
            f"dependency input drift: {name}",
        )
    metadata_source = (root / "src/schemii/common/metadata/config.py").read_text()
    require(
        f"maximum_connections: int = {metadata}" in metadata_source
        and f'integer("SCHEMII_METADATA_MAXIMUM_CONNECTIONS", {metadata})'
        in metadata_source,
        "shared metadata default drift",
    )
    compose = sections((root / "compose.test.yaml").read_text())
    require(
        set(compose) == set(contract["services"]["docker"]),
        "Docker service inventory drift",
    )
    actual = environment(compose["schemii"])
    expected = (
        contract["shared_environment"] | contract["platforms"]["docker"]["environment"]
    )
    for key, value in expected.items():
        require(actual.get(key) == value, f"Docker setting drift: {key}")
    require(
        "host=metadata-postgres port=5432 " in actual["SCHEMII_METADATA_DSN"],
        "Docker metadata endpoint drift",
    )
    require(
        actual["SCHEMII_METADATA_TARGET_HOST_ALIASES"] == "metadata-postgres",
        "Docker metadata fence alias drift",
    )
    for name in ("metadata-postgres", "demo-postgres"):
        require(
            "    ports:" not in compose[name], f"{name}: database must stay private"
        )
        require(
            f'    command: ["postgres", "-c", "max_connections={contract["platforms"]["docker"]["postgres_max_connections"]}"]'
            in compose[name],
            f"{name}: PostgreSQL connection budget drift",
        )
    for name, dependency in [
        ("schemii", "metadata-bootstrap"),
        ("schemii", "postgres-seed"),
        ("postgres-seed", "demo-bootstrap"),
    ]:
        require(
            re.search(
                r"^      "
                + dependency
                + r":\n        condition: service_completed_successfully$",
                compose[name],
                re.M,
            )
            is not None,
            f"{name}: missing bootstrap dependency {dependency}",
        )
    require(
        "ai-prototype-runtime:\n        condition: service_healthy"
        in compose["schemii"],
        "Pi readiness dependency missing",
    )
    for name, script in [
        ("metadata-bootstrap", "metadata-bootstrap.sh"),
        ("demo-bootstrap", "demo-bootstrap.sh"),
        ("postgres-seed", "seed.sh"),
    ]:
        require(
            '"/bin/sh", "/' in compose[name] and script in compose[name],
            f"{name}: bootstrap command drift",
        )
    require(
        contract["readiness"] in (root / "Dockerfile").read_text()
        and contract["readiness"] in compose["ingress"],
        "Docker readiness drift",
    )
    require(
        "schemii.main:app" in (root / "Dockerfile").read_text(),
        "Docker application fork",
    )
    config = tomllib.loads((root / "dev/schemii.toml").read_text())
    require(
        all(
            config["postgres"]["connections"][k] == v
            for k, v in contract["platforms"]["docker"]["target_admission"].items()
        ),
        "Docker target admission drift",
    )

    for path, value in contract["platforms"]["docker"]["configuration"].items():
        parts = path.split(".")
        require(
            config[parts[0]][parts[1]][parts[2]] == value
            if len(parts) == 3
            else config[parts[0]][parts[1]] == value,
            f"Docker configuration drift: {path}",
        )

    android = source_module(root, "dev/android/runtime.py")
    require(
        set(android.BASE_SERVICES) == set(contract["services"]["android"]),
        "Android service inventory drift",
    )
    require(
        set(android.SERVICES)
        == set(android.BASE_SERVICES) | set(contract["services"]["android_optional"]),
        "Android optional service drift",
    )
    with tempfile.TemporaryDirectory(prefix="schemii-parity-") as temporary:
        runtime = android.Runtime(Path(temporary) / "state")
        runtime.source = root
        runtime._prepare()
        configuration = {"tailnet_ip": "100.64.0.2", "hostname": "parity-fixture"}
        android_env = runtime._environment(configuration)
        expected = (
            contract["shared_environment"]
            | contract["platforms"]["android"]["environment"]
        )
        for key, value in expected.items():
            require(android_env.get(key) == value, f"Android setting drift: {key}")
        require(
            "host=127.0.0.1 port=5433 " in android_env["SCHEMII_METADATA_DSN"],
            "Android metadata endpoint drift",
        )
        require(
            android_env["SCHEMII_METADATA_TARGET_HOST_ALIASES"]
            == "localhost,127.0.0.1,::1",
            "Android metadata fence alias drift",
        )
        runtime._write_runtime_config(configuration)
        configured = tomllib.loads((runtime.state / "run/schemii.toml").read_text())
        require(
            all(
                configured["postgres"]["connections"][k] == v
                for k, v in contract["platforms"]["android"]["target_admission"].items()
            ),
            "Android target admission drift",
        )
        for path, value in contract["platforms"]["android"]["configuration"].items():
            parts = path.split(".")
            require(
                configured[parts[0]][parts[1]][parts[2]] == value
                if len(parts) == 3
                else configured[parts[0]][parts[1]] == value,
                f"Android configuration drift: {path}",
            )
        for name, port in zip(policy["cluster_names"], (5433, 5434), strict=True):
            directory = runtime.state / "postgres" / name
            (directory / "global").mkdir(parents=True)
            (directory / "PG_VERSION").write_text("17")
            (directory / "global/pg_control").touch()
            launches = []
            runtime._spawn = lambda *args, **kwargs: launches.append(args)
            runtime._wait = lambda *args, **kwargs: None
            runtime._cluster(name, port)
            require(
                launches
                == [(name, [str(android.PG_BIN / "postgres"), "-D", str(directory)])],
                "Android PostgreSQL cluster launch drift",
            )
            pg = postgres_settings((directory / "postgresql.conf").read_text())
            require(
                pg.get("max_connections")
                == str(contract["platforms"]["android"]["postgres_max_connections"]),
                "Android PostgreSQL budget drift",
            )
            require(pg.get("port") == str(port), "Android PostgreSQL endpoint drift")
            for setting, value in (
                contract["postgresql_policy"]["settings"]
                | {"listen_addresses": "'127.0.0.1'"}
            ).items():
                require(
                    pg.get(setting) == value,
                    f"Android durability/security drift: {setting}",
                )
    runtime_source = (root / "dev/android/runtime.py").read_text()
    for marker in (
        contract["readiness"],
        "schemii.main:app",
        "server.js",
        "seed.sql",
        "migration-demo.sql",
        "organization.sql",
    ):
        require(
            marker in runtime_source, f"Android shared startup source missing: {marker}"
        )
    # Detect overrides that would silently undo contract values, including duplicates.
    tree = ast.parse(runtime_source)
    bootstrap = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_bootstrap_database"
    )
    role_sql = [
        node.args[0].value
        for node in ast.walk(bootstrap)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "sql"
        and node.func.attr == "SQL"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
        and node.args[0].value.startswith("ALTER ROLE ")
    ]
    flags = " ".join(policy["application_role_flags"])
    require(
        role_sql == ["ALTER ROLE {} " + flags + " PASSWORD {}"],
        "Android application role policy drift",
    )
    for name in ("metadata-bootstrap.sh", "demo-bootstrap.sh"):
        sql = (root / "dev/postgres" / name).read_text()
        declarations = re.findall(r"(?:CREATE|ALTER) ROLE %I (.+) PASSWORD %L", sql)
        require(
            declarations == [flags, flags], f"{name}: application role policy drift"
        )
    spawned = {
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in ("_spawn", "_cluster")
        and node.args
        and isinstance(node.args[0], ast.Constant)
    }
    require(
        set(contract["services"]["android"]) <= spawned,
        "Android required service not launched",
    )
    require("tailnet" not in spawned, "native proxy must stay outside PRoot")
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            keys = [
                key.value
                for key in node.keys
                if isinstance(key, ast.Constant) and isinstance(key.value, str)
            ]
            require(len(keys) == len(set(keys)), "duplicate Android configuration key")
    for source, snippets in {
        "dev/android/install-guest.sh": [
            '--constraint "$work/python-source/constraints.docker.txt"',
            "--only-binary=:all:",
            "npm ci --omit=dev --ignore-scripts",
        ],
        "Dockerfile": ["--constraint constraints.docker.txt", "COPY src ./src"],
        "ai/prototype/Dockerfile": [
            "npm ci --omit=dev --ignore-scripts",
            "COPY ai/prototype/*.js ./",
        ],
        "dev/android/host.py": ["native", "proxy"],
    }.items():
        text = (root / source).read_text()
        for snippet in snippets:
            require(
                snippet in text,
                f"{source}: shared dependency/source boundary missing: {snippet}",
            )
    pins = dict(
        line.split("==")
        for line in (root / "constraints.docker.txt").read_text().splitlines()
        if line and not line.startswith("#")
    )
    require(
        pins == contract["dependencies"]["python"], "Python dependency identity drift"
    )
    package = json.loads((root / "ai/prototype/package.json").read_text())
    lock = json.loads((root / "ai/prototype/package-lock.json").read_text())
    pi = contract["dependencies"]["pi"]
    require(
        package["dependencies"] == {"@earendil-works/pi-ai": pi},
        "Pi dependency inventory/version drift",
    )
    require(
        lock["packages"][""]["dependencies"] == package["dependencies"]
        and lock["packages"]["node_modules/@earendil-works/pi-ai"]["version"] == pi,
        "Pi lock identity drift",
    )
    images = contract["images"]
    dockerfile = (root / "Dockerfile").read_text()
    require(
        re.findall(r"^FROM (\S+)", dockerfile, re.M) == [images["python"]] * 2,
        "Python image identity drift",
    )
    require(
        (root / "ai/prototype/Dockerfile").read_text().splitlines()[0]
        == "FROM " + images["node"],
        "Node image identity drift",
    )
    for name in (
        "metadata-postgres",
        "metadata-bootstrap",
        "demo-postgres",
        "demo-bootstrap",
        "postgres-seed",
    ):
        require(
            f"    image: {images['postgres']}\n" in compose[name],
            f"{name}: PostgreSQL image identity drift",
        )
    require(
        f"    image: {images['ingress']}\n" in compose["ingress"],
        "ingress image identity drift",
    )
    for source in ("dev/android/termux.sh", "dev/android/install-guest.sh"):
        require(
            images["android"] in (root / source).read_text(),
            f"{source}: Android image identity drift",
        )
        require(
            "dev/runtime-contract.json" in (root / source).read_text(),
            f"{source}: installed contract identity missing",
        )
    for source in ("dev/android/termux.sh", "dev/android/host.py"):
        require(
            "PROOT_DONT_SHARE_LIBANDROID_SHMEM="
            + contract["platforms"]["android"]["proot_environment"][
                "PROOT_DONT_SHARE_LIBANDROID_SHMEM"
            ]
            in (root / source).read_text(),
            f"{source}: PRoot IPC startup guard drift",
        )
    require(
        package["engines"]["node"]
        == ">=" + contract["dependencies"]["pi_node_minimum"],
        "Pi Node engine requirement drift",
    )
    require(
        tuple(map(int, contract["dependencies"]["node"].split(".")))
        >= tuple(map(int, contract["dependencies"]["pi_node_minimum"].split("."))),
        "Node does not satisfy Pi",
    )
    installer = (root / "dev/android/install-guest.sh").read_text()
    require(
        f"NODE_VERSION={contract['dependencies']['node']}\n" in installer
        and f"NODE_SHA256={contract['node_arm64_sha256']}\n" in installer,
        "Android Node identity drift",
    )
    require(
        f"== {contract['platforms']['android']['python']} ]]" in installer,
        "Android Python minor drift",
    )
    require(
        str(android.PG_BIN)
        == "/usr/lib/postgresql/"
        + contract["dependencies"]["postgresql_major"]
        + "/bin",
        "Android PostgreSQL major drift",
    )
    go = (root / "dev/android/tailnet-proxy/go.mod").read_text()
    require(
        f"\ngo {contract['dependencies']['go']}\n" in go
        and f"tailscale.com {contract['dependencies']['tailscale']}\n" in go,
        "native proxy dependency drift",
    )
    host = ast.parse((root / "dev/android/host.py").read_text())
    proxy_start = [
        node
        for node in ast.walk(host)
        if isinstance(node, ast.FunctionDef) and node.name == "_spawn_proxy"
    ]
    require(
        len(proxy_start) == 1
        and any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "Popen"
            and node.args
            and isinstance(node.args[0], ast.Call)
            and isinstance(node.args[0].func, ast.Attribute)
            and node.args[0].func.attr == "proxy_command"
            for node in ast.walk(proxy_start[0])
        ),
        "proxy must be launched directly by native Termux host",
    )
    recovery = source_module(root, "dev/android/recovery.py")
    require(
        set(recovery.ROOTS) == set(contract["private_state"]["android_backup"]),
        "Android backup boundary drift",
    )
    require(
        set(recovery.SECRETS) == set(android.SECRET_NAMES),
        "Android original secret backup drift",
    )
    require(
        '(*ROOTS, "tailnet")' in (root / "dev/android/recovery.py").read_text(),
        "Android native identity backup missing",
    )
    desktop_backup = source_module(root, "dev/recovery/bundle.py")
    require(
        set(desktop_backup.PAYLOADS)
        == set(contract["private_state"]["docker_backup_payloads"]),
        "Docker backup boundary drift",
    )
    builder = source_module(root, "scripts/build-android-release.py")
    require(
        "dev/runtime-contract.json" in builder.REQUIRED,
        "Android release contract missing",
    )
    require(
        set(contract["shared_sources"]) <= set(builder.PREFIXES),
        "Android shared release sources missing",
    )
    require(
        set(builder.PRIVATE_NAMES)
        == set(contract["private_state"]["release_private_names"]),
        "release private-name policy drift",
    )
    require(
        list(builder.PRIVATE_SUFFIXES)
        == contract["private_state"]["release_private_suffixes"],
        "release private-suffix policy drift",
    )
    context_rules = (root / ".dockerignore").read_text().splitlines()
    for suffix in contract["private_state"]["release_private_suffixes"]:
        require(
            "**/*" + suffix in context_rules,
            f"Docker private suffix exclusion missing: {suffix}",
        )
    for component in contract["private_state"]["release_excludes"]:
        for prefix in ("src", "dev/android", "ai/prototype"):
            require(
                not builder.selected(f"{prefix}/{component}/state"),
                f"private release boundary missing: {component}",
            )
    # Package metadata remains the sole owner of migrations and web assets.
    project = tomllib.loads((root / "pyproject.toml").read_text())
    data = project["tool"]["setuptools"]["package-data"]
    for family in ("common", "schemii", "schemoo", "schemer"):
        require(
            "migrations/*.sql" in data["schemii." + family + ".metadata"],
            f"{family}: migrations excluded from package",
        )
        require(
            any("web/" in pattern for pattern in data["schemii." + family]),
            f"{family}: assets excluded from package",
        )
    return {
        "schema": 1,
        "status": "passed",
        "contract_sha256": hashlib.sha256(
            (root / "dev/runtime-contract.json").read_bytes()
        ).hexdigest(),
        "live_acceptance": "pending",
    }


def apk_source_manifest(root: Path, commit: str) -> dict:
    """Bind the shared payload to committed bytes using the existing release owner.

    This is a source manifest, not an APK/runtime acceptance receipt. The caller
    must obtain the expected commit independently of the packaged manifest.
    """
    require(bool(re.fullmatch(r"[0-9a-f]{40}", commit)), "APK source commit required")
    builder = source_module(ROOT, "scripts/build-android-release.py")
    try:
        resolved, files = builder.source_files(root, commit)
    except (builder.BuildError, OSError) as error:
        raise ParityError(f"APK committed source: {error}") from error
    require(resolved == commit, "APK source commit mismatch")
    contract_bytes = files["dev/runtime-contract.json"][0]
    contract = json.loads(contract_bytes)
    require(contract["schema"] == 1, "unsupported runtime contract")
    prefixes = tuple(contract["shared_sources"])
    require(
        set(prefixes) == {"src/", "ai/prototype/", "dev/postgres/"},
        "APK shared source ownership drift",
    )
    required = {"dev/runtime-contract.json", *contract["dependency_inputs"]}
    require(required <= files.keys(), "APK dependency source missing")
    hashes = {
        name: hashlib.sha256(data).hexdigest()
        for name, (data, _) in sorted(files.items())
        if name in required or name.startswith(prefixes)
    }
    for name, digest in contract["dependency_inputs"].items():
        require(hashes[name] == digest, f"APK dependency input drift: {name}")
    return {
        "schema": 1,
        "platform": "android-apk",
        "commit": commit,
        "source_files": hashes,
        "runtime_contract": contract,
    }


def check_apk_sources(
    root: Path, payload: Path, record: dict, expected_commit: str
) -> dict:
    """Verify an extracted *shared source* payload, including unexpected files.

    Native libraries and toolchain artifacts belong to the APK build manifest,
    outside this directory. Never follow links into runtime/private state.
    """
    require(isinstance(record, dict), "APK source manifest must be an object")
    expected = apk_source_manifest(root, expected_commit)
    require(
        json.dumps(record, sort_keys=True) == json.dumps(expected, sort_keys=True),
        "APK source manifest drift",
    )
    require(
        payload.is_dir()
        and not any(path.is_symlink() for path in (payload, *payload.parents)),
        "APK payload missing or linked",
    )
    actual = {}

    def walk_error(error):
        raise ParityError("APK payload is unreadable") from error

    for directory, directories, filenames in os.walk(
        payload, followlinks=False, onerror=walk_error
    ):
        for name in directories + filenames:
            path = Path(directory) / name
            require(not path.is_symlink(), "APK payload cannot contain links")
            if path.is_dir():
                continue
            require(path.is_file(), "APK payload must contain regular files")
            relative = path.relative_to(payload).as_posix()
            require(relative in expected["source_files"], "APK unexpected payload file")
            with path.open("rb") as stream:
                actual[relative] = hashlib.file_digest(stream, "sha256").hexdigest()
    require(actual == expected["source_files"], "APK payload inventory/content drift")
    return {
        "schema": 1,
        "status": "passed",
        "commit": expected_commit,
        "contract_sha256": expected["source_files"]["dev/runtime-contract.json"],
        "shared_file_count": len(actual),
        "apk_runtime_acceptance": "pending",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--apk-source-manifest", type=Path)
    parser.add_argument("--apk-shared-payload", type=Path)
    parser.add_argument("--source-commit")
    args = parser.parse_args()
    apk_arguments = (
        args.apk_source_manifest,
        args.apk_shared_payload,
        args.source_commit,
    )
    if any(apk_arguments) and not all(apk_arguments):
        parser.error(
            "APK validation requires manifest, shared payload and source commit"
        )
    previous = os.umask(0o077)
    try:
        result = check(args.root)
        if args.apk_source_manifest:
            result["apk_sources"] = check_apk_sources(
                args.root,
                args.apk_shared_payload,
                json.loads(args.apk_source_manifest.read_text()),
                args.source_commit,
            )
        print(json.dumps(result, sort_keys=True))
    except (ParityError, OSError, ValueError, KeyError, IndexError) as error:
        parser.exit(1, f"Runtime parity: {error}\n")
    finally:
        os.umask(previous)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
