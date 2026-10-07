"""Run the existing change plan locally with private, source-bound evidence."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import secrets
import signal
import ssl
import stat
import subprocess
import sys
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPSHandler, HTTPRedirectHandler, Request, build_opener
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.ci.classify_changes import git  # noqa: E402
from scripts.ci.summary import META, load, summarize  # noqa: E402
from scripts.ci.test_selection import commands, layers, valid_scope  # noqa: E402
from scripts.ci.validate_reports import validate_file  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "local_change_plan", ROOT / "scripts/test-changes.py"
)
assert _spec and _spec.loader
planner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(planner)

LOCAL_URL = "https://localhost:8001"
PREVIEW_URL = "https://omarchy.taile4f57f.ts.net"
# Acceptance owns its inventory. Development filters belong in focused commands,
# not in an apparently complete local CI run. Values are never serialized.
SCOPE_ENV = (
    "PYTEST_ADDOPTS",
    "PYTEST_PLUGINS",
    "PYTEST_DISABLE_PLUGIN_AUTOLOAD",
    "NODE_OPTIONS",
    "SCHEMII_E2E_FILE_MANIFEST",
    "SCHEMII_E2E_SOURCE_ROOT",
    "SCHEMII_E2E_PARALLEL_ACCOUNTS_FILE",
    "SCHEMII_E2E_PROCESS_INDEX",
    "SCHEMII_QA_LEASE_FD",
    "SCHEMII_QA_GATE_FD",
    "SCHEMII_QA_LEASE_MODE",
)


def source_identity(root: Path) -> dict:
    """Hash actual tracked/untracked source, including concealed tracked edits."""
    names = sorted(
        {
            name
            for name in git(
                root, "ls-files", "-z", "--cached", "--others", "--exclude-standard"
            ).split(b"\0")
            if name
        }
    )
    digest = hashlib.sha256()
    for name in names:
        digest.update(name + b"\0")
        path = root / os.fsdecode(name)
        try:
            info = path.lstat()
        except FileNotFoundError:
            digest.update(b"deleted\0")
            continue
        digest.update(str(info.st_mode).encode() + b"\0")
        if stat.S_ISLNK(info.st_mode):
            digest.update(os.fsencode(os.readlink(path)))
        elif stat.S_ISREG(info.st_mode):
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
        else:
            raise ValueError("Source inventory contains a non-file")
        digest.update(b"\0")
    return {
        "commit": git(root, "rev-parse", "HEAD").decode().strip(),
        "fingerprint": digest.hexdigest(),
        "index_fingerprint": hashlib.sha256(
            git(root, "ls-files", "--stage", "-z")
        ).hexdigest(),
        "dirty": bool(
            git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all")
        ),
    }


def selected_plan(root: Path, base: str, full: bool = False) -> dict:
    before = source_identity(root)
    selected = planner.plan(root, base)
    selected["source"] = source_identity(root)
    if selected["source"] != before:
        selected["classification"].update(
            valid=False, reason="source-changed-during-planning"
        )
    if full:
        selected["classification"].update(
            profile="full", lane="source", reason="explicit-local-full"
        )
        prefix = [argv for argv in selected["commands"] if "--native-documents" in argv]
        selected["commands"] = prefix + commands("full", base)
        selected["layers"] = sorted(layers("full"))
        selected["feedback"] = {
            "commands": planner.feedback_commands(selected["commands"]),
            "pending_layers": ["browser", "postgres"],
            "acceptance": False,
        }
    return selected


def private_directory(path: Path) -> None:
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise ValueError("Private evidence path contains a symlink")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)


def write_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(".tmp")
    with open(
        temporary,
        "w",
        opener=lambda name, flags: os.open(name, flags | os.O_NOFOLLOW, 0o600),
    ) as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def lane_for(argv: list[str]) -> tuple[str, str, int] | None:
    if argv == ["npm", "test"] or argv[:2] == ["node", "--test"]:
        return "node", "none", 0
    if len(argv) > 1 and argv[1] == "scripts/ci/python-tests.py":
        return "python", "none", 0
    if argv == planner.POSTGRES_COMMAND:
        return "postgres", "none", 0
    if len(argv) > 1 and argv[1] == "scripts/ci/run-browser-shard.mjs":
        project = next(
            value.removeprefix("--project=")
            for value in argv
            if value.startswith("--project=")
        )
        shard = next(
            int(value.removeprefix("--shard=").split("/")[0])
            for value in argv
            if value.startswith("--shard=")
        )
        return "browser", project, shard
    return None


def layer_for(argv: list[str]) -> str | None:
    key = lane_for(argv)
    if key:
        return key[0]
    if "scripts/check_python_quality.py" in argv:
        return "static"
    if "compileall" in argv:
        return "python"
    if argv == planner.BROWSER_BOUNDARY or argv[0] == "./start.sh":
        return "browser"
    return None


def prerequisites(argv: list[str], environment: dict[str, str]) -> str | None:
    if argv == planner.POSTGRES_COMMAND and not all(
        environment.get(key)
        for key in ("SCHEMII_TEST_METADATA_DSN", "SCHEMII_TEST_METADATA_PASSWORD")
    ):
        return "PostgreSQL acceptance needs both SCHEMII_TEST_METADATA_DSN and SCHEMII_TEST_METADATA_PASSWORD for an owned disposable database."
    if argv == planner.BROWSER_BOUNDARY:
        if any(
            name in environment
            for name in ("SCHEMII_E2E_USERNAME", "SCHEMII_E2E_PASSWORD")
        ) and not all(
            environment.get(name)
            for name in ("SCHEMII_E2E_USERNAME", "SCHEMII_E2E_PASSWORD")
        ):
            return "Browser acceptance needs both SCHEMII_E2E_USERNAME and SCHEMII_E2E_PASSWORD when either is explicitly supplied."
        if (
            environment.get("SCHEMII_E2E_BASE_URL", LOCAL_URL) != LOCAL_URL
            or environment.get("SCHEMII_TEST_APP_PORT", "8001") != "8001"
        ):
            return "Browser acceptance requires https://localhost:8001 and the canonical launcher port."
        if not (
            environment.get("SCHEMII_E2E_BOOTSTRAP") == "1"
            or environment.get("SCHEMII_E2E_CREDENTIALS_FILE")
            or environment.get("SCHEMII_E2E_USERNAME")
            and environment.get("SCHEMII_E2E_PASSWORD")
        ):
            return "Browser acceptance needs explicitly owned credentials or SCHEMII_E2E_BOOTSTRAP=1 for a disposable first-account setup."
    return None


def quality_interpreter(root: Path, environment: dict[str, str]) -> str | None:
    """Keep the existing quality-only installed graph separate from test deps."""
    if "SCHEMII_CI_QUALITY_PYTHON" in environment:
        candidate = environment["SCHEMII_CI_QUALITY_PYTHON"]
        if not candidate:
            return None
        if "/" in candidate:
            path = Path(candidate)
            candidate = str(path if path.is_absolute() else root / path)
        return shutil.which(candidate)
    candidate = root / ".venv-quality/bin/python"
    if candidate.exists():
        return str(candidate) if os.access(candidate, os.X_OK) else None
    return sys.executable


class Interrupted(Exception):
    def __init__(self, signum: int):
        self.signum = signum


def group_alive(pid: int) -> bool:
    for path in Path("/proc").glob("[0-9]*/stat"):
        try:
            fields = path.read_text().rsplit(")", 1)[1].split()
        except (FileNotFoundError, ProcessLookupError):
            continue
        if int(fields[2]) == pid and fields[0] not in {"Z", "X", "x"}:
            return True
    return False


def stop_group(pid: int, grace: float = 5) -> None:
    """Only groups created by this runner; do not kill browsers by process name."""
    for signum, timeout in ((signal.SIGTERM, grace), (signal.SIGKILL, 1)):
        if not group_alive(pid):
            return
        try:
            os.killpg(pid, signum)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + timeout
        while group_alive(pid) and time.monotonic() < deadline:
            time.sleep(0.02)
    if group_alive(pid):
        raise RuntimeError("Owned command group survived cleanup")


def invoke(
    root: Path,
    argv: list[str],
    environment: dict[str, str],
    lease: tuple[int, int] | None = None,
) -> int:
    requested = 0

    def interrupt(signum, _frame):
        nonlocal requested
        requested = requested or signum

    handlers = {
        signum: signal.signal(signum, interrupt)
        for signum in (signal.SIGINT, signal.SIGTERM)
    }
    child = None
    try:
        child_argv = argv
        inherited = ()
        if argv[0] == "./start.sh" and lease:
            inherited = lease
            # Remap high-numbered owned descriptors only in the child. This
            # never replaces the caller's descriptors 3/4 or any peer's locks.
            child_argv = [
                "bash",
                "-c",
                'exec 3<&"$1" 4<&"$2"; shift 2; exec "$@"',
                "local-ci",
                *map(str, lease),
                *argv,
            ]
        child = subprocess.Popen(
            child_argv,
            cwd=root,
            env=environment,
            start_new_session=True,
            pass_fds=inherited,
        )
        while child.poll() is None and not requested:
            time.sleep(0.02)
        return 128 + requested if requested else child.returncode
    finally:
        for signum in handlers:
            signal.signal(signum, signal.SIG_IGN)
        try:
            if child is not None:
                stop_group(child.pid)
                child.wait()
        finally:
            for signum, handler in handlers.items():
                signal.signal(signum, handler)


@contextmanager
def deployment_lease(root: Path):
    common = Path(
        git(root, "rev-parse", "--path-format=absolute", "--git-common-dir")
        .decode()
        .strip()
    )
    descriptors = []
    try:
        # Match the launcher's gate-then-deployment acquisition order.
        for name in ("qa-startup.lock", "qa-deployment.lock"):
            original = os.open(common / name, os.O_CREAT | os.O_RDWR, 0o600)
            try:
                descriptor = fcntl.fcntl(original, fcntl.F_DUPFD_CLOEXEC, 10)
            finally:
                os.close(original)
            descriptors.append(descriptor)
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield descriptors[1], descriptors[0]
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


class SameOriginRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        if urlsplit(newurl)[:2] != urlsplit(request.full_url)[:2]:
            raise ValueError("Deployment redirected outside its canonical HTTPS origin")
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def verify_deployment() -> list[dict]:
    results = []
    for origin in (LOCAL_URL, PREVIEW_URL):
        context = ssl.create_default_context()
        if origin == LOCAL_URL:
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        opener = build_opener(HTTPSHandler(context=context), SameOriginRedirect())
        for suffix in ("", "/api-map"):
            url = origin + suffix
            try:
                with opener.open(Request(url), timeout=20) as response:
                    status = response.status
            except HTTPError as error:
                status = error.code
                error.close()
            if not (200 <= status < 300 or suffix == "/api-map" and status == 401):
                raise ValueError("Canonical deployment HTTPS verification failed")
            results.append({"url": url, "status": status})
    return results


def evidence(
    path: Path, key: tuple[str, str, int], profile: str, identity: dict, run_id: int
) -> dict:
    if path.is_symlink() or not path.is_file():
        raise ValueError("Missing timing evidence")
    records = load(path)
    summary = summarize(records)
    expected = dict(zip(("lane", "project", "shard"), key)) | {
        "schema": 1,
        "source_sha": identity["commit"],
        "run_id": run_id,
        "run_attempt": 1,
    }
    if (
        any(summary[field] != expected[field] for field in META)
        or not summary["complete"]
        or summary["outcome"] != "passed"
        or not summary["first_attempt_eligible"]
        or summary["first_attempt_failures"] != 0
        or summary["retry_recovered"] != 0
    ):
        raise ValueError("Incomplete or mismatched timing evidence")
    if key[0] == "postgres" and summary["attempt_outcomes"]["skipped"]:
        raise ValueError("Real PostgreSQL acceptance cannot be skipped")
    scope = {
        "profile": profile,
        "planned": sorted(
            record["test_id"] for record in records if record["kind"] == "plan"
        ),
        "observed": [
            {
                field: record[field]
                for field in ("test_id", "source_id", "outcome", "attempt")
            }
            for record in records
            if record["kind"] == "attempt"
        ],
    }
    if not valid_scope(profile, key, scope):
        raise ValueError("Invalid selected acceptance inventory")
    return summary


def run_selected(
    root: Path, selected: dict, *, feedback: bool, base: str, full: bool = False
) -> tuple[int, Path]:
    directory = root / ".schemii/local-ci"
    private_directory(directory)
    run_id = time.time_ns() // 1_000_000
    run = directory / (
        "run-"
        + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + "-"
        + uuid.uuid4().hex[:12]
    )
    run.mkdir(mode=0o700)
    receipt_path = run / "receipt.json"
    required = selected["commands"]
    executed = planner.feedback_commands(required) if feedback else required
    entries = [
        {"command": argv, "status": "pending", "duration_ms": None} for argv in required
    ]
    receipt = {
        "schema": 1,
        "run_id": run_id,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "base_ref": base,
        "classification": selected["classification"],
        "mode": "feedback" if feedback else "full" if full else "selected",
        "profile": selected["classification"]["profile"],
        "source": None,
        "status": "running",
        "acceptance": False,
        "commands": entries,
        "pending_layers": selected["layers"],
        "pending_commands": list(range(len(required))),
    }
    write_json(receipt_path, receipt)
    print(f"Local CI receipt: {receipt_path}", flush=True)
    started = time.perf_counter()
    lease_context = None
    lease = None
    code = 2

    def interrupt(signum, _frame):
        raise Interrupted(signum)

    handlers = {
        signum: signal.signal(signum, interrupt)
        for signum in (signal.SIGINT, signal.SIGTERM)
    }
    try:
        receipt["source"] = identity = source_identity(root)
        write_json(receipt_path, receipt)
        if not selected["classification"]["valid"]:
            receipt.update(status="pending", reason="invalid-git-comparison")
            return code, receipt_path
        if selected["source"] != identity:
            receipt.update(
                status="source-changed", reason="source-changed-since-planning"
            )
            return 1, receipt_path
        environment = dict(os.environ)
        if any(environment.get(name) for name in SCOPE_ENV):
            receipt.update(status="pending", reason="ambient-scope-override")
            print(
                "Clear ambient test filters, discovery overrides and inherited QA leases before local CI.",
                file=sys.stderr,
            )
            return code, receipt_path
        markdown = sorted(
            path
            for path in selected.get("changed_paths", [])
            if path.endswith(".md") and (root / path).exists()
        )
        if markdown:
            validation = {"paths": markdown, "status": "running", "duration_ms": None}
            receipt["markdown_validation"] = validation
            write_json(receipt_path, receipt)
            validation_started = time.perf_counter()
            try:
                validation["checked_links"] = sum(
                    validate_file(root, path) for path in markdown
                )
                validation["status"] = "passed"
            except (ValueError, OSError):
                validation["status"] = "failed"
                receipt.update(status="failed", reason="markdown-validation-failed")
                print(
                    "Changed Markdown failed the existing title/link/code-fence checks.",
                    file=sys.stderr,
                )
                return 1, receipt_path
            finally:
                validation["duration_ms"] = round(
                    (time.perf_counter() - validation_started) * 1000, 3
                )
                write_json(receipt_path, receipt)
        if selected["classification"]["profile"] == "reports":
            path = root / ".schemii/test-selection/classification.json"
            private_directory(path.parent)
            write_json(path, selected["classification"])
        for index, argv in enumerate(executed):
            entry = entries[index]
            if source_identity(root) != identity:
                receipt.update(
                    status="source-changed", reason="source-changed-before-command"
                )
                return 1, receipt_path
            missing = prerequisites(argv, environment)
            if missing:
                entry.update(status="pending", reason="missing-prerequisite")
                receipt.update(status="pending", reason="missing-prerequisite")
                print(missing, file=sys.stderr)
                return 2, receipt_path
            if argv == planner.BROWSER_BOUNDARY:
                try:
                    lease_context = deployment_lease(root)
                    lease = lease_context.__enter__()
                except BlockingIOError:
                    entry.update(status="pending", reason="deployment-leased")
                    receipt.update(status="pending", reason="deployment-leased")
                    print(
                        "Browser acceptance pending: another launch or QA run holds the deployment lease.",
                        file=sys.stderr,
                    )
                    return 2, receipt_path
                if not environment.get("SCHEMII_SECRET_DIRECTORY"):
                    primary = (
                        git(root, "worktree", "list", "--porcelain")
                        .decode()
                        .splitlines()[0]
                    )
                    environment["SCHEMII_SECRET_DIRECTORY"] = str(
                        Path(primary.removeprefix("worktree ")) / ".schemii/secrets"
                    )
                if environment.get("SCHEMII_E2E_BOOTSTRAP") == "1" and not any(
                    environment.get(name)
                    for name in (
                        "SCHEMII_E2E_CREDENTIALS_FILE",
                        "SCHEMII_E2E_USERNAME",
                        "SCHEMII_E2E_PASSWORD",
                    )
                ):
                    credential_file = run / "browser-credentials.json"
                    write_json(
                        credential_file,
                        {
                            "username": "e2e_admin_" + run.name.rsplit("-", 1)[1],
                            "password": secrets.token_urlsafe(32),
                        },
                    )
                    environment["SCHEMII_E2E_CREDENTIALS_FILE"] = str(credential_file)
                    # Keep the one generated account usable across same-stack
                    # shards. Existing installations still require a successful
                    # login; bootstrap never resets or claims an existing admin.
                    receipt["browser_credentials"] = credential_file.name
                    write_json(receipt_path, receipt)
            command = list(argv)
            if command[0] in {"python", "python3"}:
                command[0] = sys.executable
            if len(command) > 1 and command[1] == "scripts/check_python_quality.py":
                interpreter = quality_interpreter(root, environment)
                if interpreter is None:
                    entry.update(
                        status="pending", reason="quality-interpreter-unavailable"
                    )
                    receipt.update(
                        status="pending", reason="quality-interpreter-unavailable"
                    )
                    print(
                        "The selected quality interpreter is unavailable; prepare .venv-quality or set SCHEMII_CI_QUALITY_PYTHON to its executable.",
                        file=sys.stderr,
                    )
                    return 2, receipt_path
                command[0] = interpreter
                command[2] = selected["classification"]["base"]
            if command[0] == "./start.sh" and len(command) == 3:
                command[2] = str(run / "recovery")
            child_env = environment | {
                "CI_TELEMETRY_SHA": identity["commit"],
                "CI_TELEMETRY_RUN_ID": str(run_id),
                "CI_TELEMETRY_RUN_ATTEMPT": "1",
            }
            # Ignore inherited reporter destinations; every command owns fresh
            # files. A caller cannot substitute an older successful receipt.
            for key in tuple(child_env):
                if key.startswith("CI_TELEMETRY_") and key not in {
                    "CI_TELEMETRY_SHA",
                    "CI_TELEMETRY_RUN_ID",
                    "CI_TELEMETRY_RUN_ATTEMPT",
                }:
                    del child_env[key]
            key = lane_for(argv)
            if key and key[0] == "python":
                # Default discovery includes the opt-in integration family.
                # Its real execution belongs exclusively to the PostgreSQL
                # lane, even when the operator configured those prerequisites.
                for name in (
                    "SCHEMII_TEST_METADATA_DSN",
                    "SCHEMII_TEST_METADATA_PASSWORD",
                ):
                    child_env.pop(name, None)
            telemetry = None
            if key:
                command_dir = run / f"command-{index:02d}"
                command_dir.mkdir(mode=0o700)
                telemetry = command_dir / "timing.jsonl"
                # Reporters truncate this owned file while preserving its mode.
                # Child test fixtures retain the caller's ordinary umask.
                telemetry.touch(mode=0o600, exist_ok=False)
                child_env.update(
                    CI_TELEMETRY_FILE=str(telemetry),
                    CI_TELEMETRY_LANE=key[0],
                    CI_TELEMETRY_PROJECT=key[1],
                    CI_TELEMETRY_SHARD=str(key[2]),
                )
                if argv[:2] == ["node", "--test"]:
                    command[2:2] = [
                        "--test-reporter=spec",
                        "--test-reporter-destination=stdout",
                        "--test-reporter=./scripts/ci/node-reporter.mjs",
                        f"--test-reporter-destination={telemetry}",
                    ]
                if key[0] == "browser":
                    for output in (
                        "artifacts",
                        "artifacts/playwright-results",
                        "artifacts/playwright-auth",
                    ):
                        private_directory(root / output)
                    child_env["CI"] = (
                        "1"  # Existing retry/reporting policy, with every attempt retained.
                    )
            if lease:
                child_env.update(
                    SCHEMII_E2E_BASE_URL=LOCAL_URL,
                    SCHEMII_TEST_APP_PORT="8001",
                    SCHEMII_QA_LEASE_FD="3",
                    SCHEMII_QA_GATE_FD="4",
                    SCHEMII_QA_LEASE_MODE="exclusive",
                )
            entry.update(status="running", executed_command=command)
            if telemetry:
                entry["timing_evidence"] = str(telemetry.relative_to(run))
            write_json(receipt_path, receipt)
            print("+ " + shlex.join(command), flush=True)
            command_started = time.perf_counter()
            try:
                code = invoke(root, command, child_env, lease)
            finally:
                entry["duration_ms"] = round(
                    (time.perf_counter() - command_started) * 1000, 3
                )
            entry.update(
                exit_code=code,
                status="passed"
                if code == 0
                else "cancelled"
                if code < 0 or code in {130, 143}
                else "failed",
            )
            # Keep failed original reporter files; only success is eligible for
            # validated acceptance summaries.
            if code:
                receipt.update(status=entry["status"], reason="command-failed")
                return (128 - code if code < 0 else code), receipt_path
            if argv == ["./start.sh"]:
                receipt["deployment"] = {
                    "source": identity,
                    "endpoints": verify_deployment(),
                }
                write_json(run / "deployment.json", receipt["deployment"])
            if telemetry:
                try:
                    summary = evidence(
                        telemetry, key, receipt["profile"], identity, run_id
                    )
                    write_json(telemetry.with_suffix(".summary.json"), summary)
                except (OSError, ValueError):
                    entry.update(
                        status="incomplete", reason="invalid-or-missing-evidence"
                    )
                    receipt.update(
                        status="incomplete", reason="invalid-or-missing-evidence"
                    )
                    return 1, receipt_path
            if source_identity(root) != identity:
                entry.update(status="source-changed")
                receipt.update(
                    status="source-changed", reason="source-changed-during-command"
                )
                return 1, receipt_path
            write_json(receipt_path, receipt)
        receipt.update(
            status="feedback-passed" if feedback else "passed", acceptance=not feedback
        )
        code = 0
        return code, receipt_path
    except Interrupted as error:
        receipt.update(status="cancelled", reason="interrupted")
        return 128 + error.signum, receipt_path
    except (OSError, ValueError, subprocess.SubprocessError, RuntimeError):
        receipt.update(status="incomplete", reason="execution-or-deployment-error")
        print(
            "Local CI is incomplete; inspect the private receipt and original command output.",
            file=sys.stderr,
        )
        return 2, receipt_path
    finally:
        for signum in handlers:
            signal.signal(signum, signal.SIG_IGN)
        try:
            if lease_context:
                lease_context.__exit__(None, None, None)
            receipt["pending_commands"] = [
                index
                for index, entry in enumerate(entries)
                if entry["status"] != "passed"
            ]
            for entry in entries:
                if entry["status"] == "running":
                    entry["status"] = (
                        "cancelled"
                        if receipt["status"] == "cancelled"
                        else "incomplete"
                    )
            receipt["pending_layers"] = sorted(
                {
                    layer
                    for entry in entries
                    if entry["status"] != "passed"
                    if (layer := layer_for(entry["command"])) is not None
                }
            )
            receipt["duration_ms"] = round((time.perf_counter() - started) * 1000, 3)
            receipt["finished_at"] = datetime.now(timezone.utc).isoformat()
            write_json(receipt_path, receipt)
            print(
                f"Local CI: {receipt['status']}; acceptance={receipt['acceptance']}; receipt={receipt_path}",
                flush=True,
            )
        finally:
            for signum, handler in handlers.items():
                signal.signal(signum, handler)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="origin/main")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--plan",
        action="store_true",
        help="Print the selected checks without running them.",
    )
    modes.add_argument(
        "--feedback",
        action="store_true",
        help="Run deterministic feedback; never establishes acceptance.",
    )
    parser.add_argument(
        "--full", action="store_true", help="Require all existing acceptance layers."
    )
    args = parser.parse_args()
    selected = selected_plan(ROOT, args.base, args.full)
    if args.plan:
        print(json.dumps(selected, indent=2))
        return 0 if selected["classification"]["valid"] else 2
    code, _ = run_selected(
        ROOT, selected, feedback=args.feedback, base=args.base, full=args.full
    )
    return code


if __name__ == "__main__":
    raise SystemExit(main())
