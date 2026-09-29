#!/usr/bin/env python3
"""Synchronous Codex assignment guardrail; never launches agents or runs plan commands.

Hook trust, tool coverage and fail-open runtime errors limit enforcement. In particular,
checking a prepared QA lane does not claim it, authenticate a child, or prove acceptance.
"""

from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
import re
import sys

MAX_INPUT = 128 * 1024
MAX_MANIFEST = 2 * 1024 * 1024
PREFIX = "SCHEMII_ASSIGNMENT "
CONTEXT = (
    "Delegate through native T3/Codex tools. Every worker prompt needs exactly one "
    "SCHEMII_ASSIGNMENT JSON line declaring kind, task, workspace, owned_paths, "
    "owned_resources and focused verification. Development needs its own Git worktree "
    "and branch, or explicit exclusive shared-path ownership. Preserve other workers' edits. "
    "For manual UI QA use ./test.sh prepare/run, a ready isolated lane, then claim the "
    "returned actual worker ID before actions; use only that session and record/view "
    "fresh evidence. Native tabs are cooperative and do not prove cookie isolation. "
    "Keep credentials private; reserve an independent reviewer. This hook is a guardrail, "
    "not a security boundary or an acceptance pass. Read testing/agents/README.md."
)
KINDS = {"development", "research", "ui-testing", "review"}
FIELDS = {
    "kind",
    "task",
    "workspace",
    "owned_paths",
    "owned_resources",
    "verification",
    "shared_checkout_exclusive",
    "qa",
}


class InvalidAssignment(ValueError):
    """Only fixed diagnostic text is surfaced; never echo hook input or file content."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise InvalidAssignment(message)


def text(value: object, name: str, limit: int = 2000) -> str:
    require(
        isinstance(value, str) and bool(value.strip()) and len(value) <= limit,
        f"{name} must be nonblank bounded text.",
    )
    require(
        not any(ord(char) < 32 for char in value), f"{name} must be single-line text."
    )
    return value


def strings(value: object, name: str, *, empty: bool = False) -> list[str]:
    require(
        isinstance(value, list) and len(value) <= 32 and (empty or bool(value)),
        f"{name} must be a bounded list of explicit entries.",
    )
    entries = [text(item, name) for item in value]
    require(len(set(entries)) == len(entries), f"{name} must not repeat entries.")
    return entries


def read_bounded(path: Path, maximum: int) -> str:
    # Only repository metadata and the fixed public run manifest are read. Never
    # open controller/session/credential files supplied by an assignment.
    require(path.is_file(), "Required workspace or QA metadata is missing.")
    with path.open("rb") as handle:
        data = handle.read(maximum + 1)
    require(len(data) <= maximum, "Workspace or QA metadata is too large.")
    return data.decode("utf-8")


def git_identity(workspace: Path) -> tuple[Path, str, bool]:
    marker = workspace / ".git"
    linked = marker.is_file()
    if linked:
        content = read_bounded(marker, 4096).strip()
        require(
            content.startswith("gitdir: "),
            "Workspace has invalid Git worktree metadata.",
        )
        git_dir = (workspace / content.removeprefix("gitdir: ")).resolve()
        common = (git_dir / read_bounded(git_dir / "commondir", 4096).strip()).resolve()
        backlink = Path(read_bounded(git_dir / "gitdir", 4096).strip()).resolve()
        require(
            backlink == marker.resolve() and git_dir.parent == common / "worktrees",
            "Workspace is not a registered linked Git worktree.",
        )
    else:
        require(marker.is_dir(), "workspace must be an existing Git checkout.")
        git_dir = common = marker.resolve()
    head = read_bounded(git_dir / "HEAD", 4096).strip()
    branch = (
        head.removeprefix("ref: refs/heads/")
        if head.startswith("ref: refs/heads/")
        else ""
    )
    return common, branch, linked


def owned_path(value: str, workspace: Path, git_common: Path | None = None) -> str:
    require(
        value == value.strip()
        and "\\" not in value
        and not any(c in value for c in "*?[]"),
        "owned_paths must be literal relative paths without glob patterns.",
    )
    path = PurePosixPath(value)
    require(
        not path.is_absolute()
        and value not in {".", ""}
        and all(part not in {".", "..", ""} for part in value.split("/")),
        "owned_paths must be narrow relative paths without traversal.",
    )
    target = (workspace / value).resolve()
    # Canonical ownership also excludes aliases into common metadata and all
    # registered worktrees' Git metadata, plus this worktree's .git pointer.
    common = git_common if git_common is not None else git_identity(workspace)[0]
    require(
        path.parts[0] != ".git"
        and not target.is_relative_to(common)
        and not target.is_relative_to((workspace / ".git").resolve()),
        "Git internals cannot be assigned as owned paths, including aliases.",
    )
    require(
        target.is_relative_to(workspace),
        "owned_paths must stay inside workspace, including symlinks.",
    )
    return value


def unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        require(key not in result, "JSON must not contain duplicate keys.")
        result[key] = value
    return result


def decode_json(raw: str) -> object:
    return json.loads(
        raw,
        object_pairs_hook=unique_object,
        parse_constant=lambda _: (_ for _ in ()).throw(
            InvalidAssignment("JSON must be finite.")
        ),
    )


def qa_artifact_path(relative: str, workspace: Path) -> Path:
    """Keep the artifact chain literal; same-checkout aliases can target source."""
    path = workspace
    for part in PurePosixPath(relative).parts:
        path = path / part
        require(not path.is_symlink(), "QA artifact paths must not contain symlinks.")
    require(
        path.resolve() == path and path.is_relative_to(workspace / "artifacts" / "qa"),
        "QA artifacts must stay in their actual run and lane region.",
    )
    return path


def validate_qa(qa: object, assignment: dict, workspace: Path) -> None:
    require(
        isinstance(qa, dict) and set(qa) == {"run", "lane", "claim", "browser"},
        "qa requires exactly run, lane, claim and browser.",
    )
    run = text(qa["run"], "qa.run", 90)
    lane_id = text(qa["lane"], "qa.lane", 90)
    require(
        re.fullmatch(r"qa-[a-z0-9-]{6,80}", run) is not None
        and re.fullmatch(r"lane-[1-9][0-9]*", lane_id) is not None,
        "qa.run and qa.lane must identify an existing ./test.sh lane.",
    )
    require(
        qa["claim"] == "after-spawn-before-actions",
        "Coordinator must claim the actual spawned worker ID before browser actions.",
    )
    require(
        qa["browser"] == "isolated",
        "Manual account QA requires an isolated ./test.sh lane; native-cooperative tabs do not prove cookie isolation.",
    )
    require(
        assignment["owned_resources"] == [f"qa:{run}:{lane_id}"],
        "QA owns exactly its qa:RUN:LANE resource; other lane resources cannot be assigned.",
    )
    evidence = f"artifacts/qa/{run}/{lane_id}"
    require(
        any(
            path == evidence or path.startswith(evidence + "/")
            for path in assignment["owned_paths"]
        ),
        "QA needs its exact lane evidence directory in owned_paths.",
    )
    require(
        all(
            path == evidence or path.startswith(evidence + "/")
            for path in assignment["owned_paths"]
        ),
        "UI QA owns only its assigned lane evidence, not application source.",
    )
    for path in assignment["owned_paths"]:
        qa_artifact_path(path, workspace)
    manifest_path = qa_artifact_path(f"artifacts/qa/{run}/manifest.json", workspace)
    manifest = decode_json(read_bounded(manifest_path, MAX_MANIFEST))
    require(
        isinstance(manifest, dict)
        and manifest.get("id") == run
        and manifest.get("controller") == "t3"
        and manifest.get("status") in {"ready", "running"},
        "Use ./test.sh prepare/run --controller t3; the run must be ready or running.",
    )
    require(
        isinstance(manifest.get("root"), str)
        and Path(manifest["root"]).resolve() == workspace,
        "QA run must belong to the assigned workspace.",
    )
    deployment = manifest.get("deployment")
    require(
        isinstance(deployment, dict)
        and isinstance(deployment.get("identity"), dict)
        and bool(deployment["identity"].get("fingerprint"))
        and bool(deployment.get("verifiedAt")),
        "QA run needs its verified deployment from ./test.sh prepare.",
    )
    owner_path = qa_artifact_path(
        f"artifacts/qa/{run}/controller-owner.json", workspace
    )
    owner = decode_json(read_bounded(owner_path, 4096))
    require(
        isinstance(owner, dict)
        and type(owner.get("pid")) is int
        and owner["pid"] > 0
        and isinstance(owner.get("birthTick"), str)
        and owner["birthTick"].isdigit(),
        "QA needs its recorded live controller; resume an interrupted run first.",
    )
    process = read_bounded(Path("/proc") / str(owner["pid"]) / "stat", 16384)
    process_fields = process[process.rfind(")") + 2 :].split()
    require(
        len(process_fields) > 19
        and process_fields[0] != "Z"
        and process_fields[19] == owner["birthTick"],
        "QA controller is stopped or replaced; resume the run before assigning workers.",
    )
    lanes = manifest.get("lanes")
    require(isinstance(lanes, list), "QA run must contain prepared lanes.")
    matching = [
        lane for lane in lanes if isinstance(lane, dict) and lane.get("id") == lane_id
    ]
    require(
        len(matching) == 1
        and matching[0].get("status") == "ready"
        and not matching[0].get("agent"),
        "Only a ready unclaimed lane may be assigned; do not adopt another worker's claim.",
    )
    require(
        any(
            "./test.sh" in step and "checkpoint" in step
            for step in assignment["verification"]
        )
        and any(
            "./test.sh" in step and "finish" in step
            for step in assignment["verification"]
        ),
        "QA verification must include ./test.sh checkpoint evidence and finish.",
    )


def validate_assignment(prompt: str, repository: Path) -> None:
    lines = [
        line for line in prompt.splitlines() if line.startswith("SCHEMII_ASSIGNMENT")
    ]
    require(
        len(lines) == 1 and lines[0].startswith(PREFIX),
        "Worker prompt needs exactly one standalone SCHEMII_ASSIGNMENT JSON line.",
    )
    assignment = decode_json(lines[0][len(PREFIX) :])
    require(
        isinstance(assignment, dict) and set(assignment) <= FIELDS,
        "SCHEMII_ASSIGNMENT has unknown fields or is not an object.",
    )
    require(
        assignment.get("kind") in KINDS,
        "kind must be development, research, ui-testing or review.",
    )
    text(assignment.get("task"), "task")
    raw_workspace = text(assignment.get("workspace"), "workspace", 4096)
    require(
        Path(raw_workspace).is_absolute(),
        "workspace must be an actual absolute checkout path.",
    )
    workspace = Path(raw_workspace).resolve()
    require(workspace.is_dir(), "workspace must exist before delegation.")
    common, branch, linked = git_identity(workspace)
    require(
        common == git_identity(repository.resolve())[0],
        "workspace must belong to this project repository.",
    )
    paths = strings(assignment.get("owned_paths"), "owned_paths", empty=True)
    for path in paths:
        owned_path(path, workspace, common)
    resources = strings(
        assignment.get("owned_resources"), "owned_resources", empty=True
    )
    require(bool(paths or resources), "Declare explicit owned paths or resources.")
    require(
        all(
            resource.strip().lower() not in {"all", "*", "everything"}
            and "*" not in resource
            for resource in resources
        ),
        "owned_resources must be explicit rather than global ownership.",
    )
    verification = strings(assignment.get("verification"), "verification")
    require(
        all(
            step.strip().lower()
            not in {
                "test",
                "tests",
                "all",
                "everything",
                "full suite",
                "full test suite",
            }
            for step in verification
        ),
        "verification must identify focused checks or observable results.",
    )
    exclusive = assignment.get("shared_checkout_exclusive", False)
    require(type(exclusive) is bool, "shared_checkout_exclusive must be a boolean.")
    if assignment["kind"] == "development":
        require(bool(paths), "Development needs explicit owned relative paths.")
        require(
            exclusive or (linked and bool(branch) and branch not in {"main", "master"}),
            "Development needs its own linked Git worktree/branch, or explicit exclusive shared-path assignment.",
        )
    require(
        assignment["kind"] == "development" or not exclusive,
        "Exclusive shared-checkout writes apply only to development assignments.",
    )
    if assignment["kind"] == "ui-testing" or "qa" in assignment:
        require(
            assignment["kind"] in {"ui-testing", "review"},
            "qa context is only for UI testing or QA review.",
        )
        validate_qa(assignment.get("qa"), assignment, workspace)


def denial(reason: str) -> dict:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def handle_event(event: object, repository: Path) -> dict:
    try:
        require(isinstance(event, dict), "Hook input must be an object.")
        name = event.get("hook_event_name")
        require(
            name in {"PreToolUse", "SessionStart", "SubagentStart", "UserPromptSubmit"},
            "Unsupported hook event; check the project hook configuration.",
        )
        if name != "PreToolUse":
            return {
                "hookSpecificOutput": {
                    "hookEventName": name,
                    "additionalContext": CONTEXT,
                }
            }
        tool = text(event.get("tool_name"), "tool_name", 200)
        if tool != "Agent" and not re.search(r"(?:^|[._])spawn_agent$", tool):
            return {}
        arguments = event.get("tool_input")
        require(isinstance(arguments, dict), "Spawn tool_input must be an object.")
        prompt_fields = [field for field in ("message", "prompt") if field in arguments]
        require(
            len(prompt_fields) == 1, "Spawn input needs exactly one message or prompt."
        )
        prompt = arguments[prompt_fields[0]]
        require(
            isinstance(prompt, str)
            and bool(prompt.strip())
            and len(prompt.encode()) <= MAX_INPUT,
            "Worker prompt must be nonblank bounded text.",
        )
        validate_assignment(prompt, repository)
        return {}
    except InvalidAssignment as error:
        return denial(str(error))
    except (ValueError, TypeError, OSError, RecursionError, RuntimeError):
        return denial(
            "Cannot validate assignment or workspace/QA metadata; correct the contract before spawning."
        )


def main() -> int:
    try:
        raw = sys.stdin.buffer.read(MAX_INPUT + 1)
        require(len(raw) <= MAX_INPUT, "Hook input exceeds the 128 KiB limit.")
        result = handle_event(
            decode_json(raw.decode("utf-8")), Path(__file__).resolve().parents[2]
        )
    except (InvalidAssignment, ValueError, RecursionError) as error:
        result = denial(
            str(error)
            if isinstance(error, InvalidAssignment)
            else "Hook input must be valid bounded JSON."
        )
    print(json.dumps(result, ensure_ascii=True))
    # Codex's supported explicit denial is in JSON; a generic failing hook can
    # let the tool proceed. Never use continue:false or an accidental traceback.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
