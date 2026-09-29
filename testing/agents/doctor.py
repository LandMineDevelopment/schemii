#!/usr/bin/env python3
"""Read supported Codex configuration; never infer capacity from configuration alone."""

import argparse
import json
import os
from pathlib import Path
import re
import selectors
import subprocess
import time

BROWSER_COMMAND = (
    'exec python3 "$(git rev-parse --show-toplevel)/testing/agents/browser.py"'
)
BROWSER_TOOLS = {
    "browser_close",
    "browser_resize",
    "browser_console_messages",
    "browser_handle_dialog",
    "browser_emulate_media",
    "browser_file_upload",
    "browser_drop",
    "browser_find",
    "browser_fill_form",
    "browser_press_key",
    "browser_type",
    "browser_mouse_move_xy",
    "browser_mouse_click_xy",
    "browser_mouse_drag_xy",
    "browser_mouse_down",
    "browser_mouse_up",
    "browser_mouse_wheel",
    "browser_navigate",
    "browser_navigate_back",
    "browser_take_screenshot",
    "browser_snapshot",
    "browser_click",
    "browser_drag",
    "browser_hover",
    "browser_select_option",
    "browser_tabs",
    "browser_wait_for",
}


def browser_policy(config: dict) -> dict:
    server = (config.get("mcp_servers") or {}).get("schemii_browser") or {}
    tools = server.get("enabled_tools")
    valid = bool(
        server.get("command") == "bash"
        and server.get("args") == ["-c", BROWSER_COMMAND]
        and not server.get("url")
        and server.get("enabled") is not False
        and server.get("required") is True
        and isinstance(tools, list)
        and all(isinstance(tool, str) for tool in tools)
        and set(tools) == BROWSER_TOOLS
        and len(tools) == len(BROWSER_TOOLS)
    )
    return {
        "configured": bool(server),
        "isolated_stdio_policy_valid": valid,
        "runtime_browser_isolation": "unverified",
        "artifact_cleanup": "owned_connection_exit; orphan_sweep_on_start"
        if valid
        else "unverified",
    }


def inspect_configuration(cwd: Path, timeout: float = 15) -> dict:
    env = dict(os.environ)
    # This check doesn't need T3 credentials or start a provider turn.
    env.pop("T3_MCP_BEARER_TOKEN", None)
    process = subprocess.Popen(
        ["codex", "app-server", "--strict-config"],
        cwd=cwd,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        bufsize=0,
    )
    buffer = bytearray()
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)

    def request(request_id: int, method: str, params: dict) -> dict:
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }
        process.stdin.write((json.dumps(payload) + "\n").encode())
        deadline = time.monotonic() + timeout
        while True:
            while b"\n" in buffer:
                line, _, rest = buffer.partition(b"\n")
                buffer[:] = rest
                response = json.loads(line)
                if response.get("id") == request_id:
                    if "error" in response:
                        raise RuntimeError(
                            f"Codex rejected {method}; check the installed CLI version"
                        )
                    return response["result"]
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not selector.select(remaining):
                raise RuntimeError(f"Codex timed out responding to {method}")
            data = os.read(process.stdout.fileno(), 65536)
            if not data:
                raise RuntimeError("Codex exited before returning its configuration")
            buffer.extend(data)

    try:
        request(
            1,
            "initialize",
            {
                "clientInfo": {"name": "schemii_agent_doctor", "version": "1.0.0"},
                "capabilities": {"experimentalApi": True},
            },
        )
        process.stdin.write(b'{"jsonrpc":"2.0","method":"initialized"}\n')
        config = request(2, "config/read", {"includeLayers": False, "cwd": str(cwd)})
        hooks = request(3, "hooks/list", {"cwds": [str(cwd)]})
        return {"config": config["config"], "hooks": hooks.get("data", [])}
    finally:
        selector.close()
        process.stdin.close()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        process.stdout.close()


def summarize(data: dict, runtime_slots: int | None) -> dict:
    config = data["config"]
    agents = config.get("agents") or {}
    features = config.get("features") or {}
    v2 = features.get("multi_agent_v2")
    v2_enabled = v2 is True or isinstance(v2, dict) and v2.get("enabled") is True
    configured_limit = (
        v2.get("max_concurrent_threads_per_session")
        if isinstance(v2, dict) and v2_enabled
        else agents.get("max_concurrent_threads_per_session", agents.get("max_threads"))
        if not v2_enabled
        else None
    )
    native_enabled = (
        True
        if v2_enabled
        else False
        if features.get("multi_agent") is False or agents.get("enabled") is False
        else True
        if features.get("multi_agent") is True or agents.get("enabled") is True
        else None
    )
    loaded_hooks = [hook for entry in data["hooks"] for hook in entry.get("hooks", [])]
    guards = [
        hook
        for hook in loaded_hooks
        if "testing/agents/guard.py" in hook.get("command", "")
    ]

    def covers_tools(hook: dict, names: tuple[str, ...]) -> bool:
        matcher = hook.get("matcher")
        if matcher in {None, "", "*"}:
            return True
        try:
            return all(re.search(matcher, name) for name in names)
        except (TypeError, re.error):
            return False

    active_guard = features.get("hooks") is not False and any(
        hook.get("eventName") in {"preToolUse", "PreToolUse"}
        and hook.get("enabled") is True
        and hook.get("trustStatus") in {"trusted", "managed"}
        and covers_tools(hook, ("spawn_agent", "Agent"))
        for hook in guards
    )
    available_total = runtime_slots
    known_limit = type(configured_limit) is int and configured_limit > 0
    if available_total is not None and known_limit:
        available_total = min(available_total, configured_limit + 1)
    # Keep the coordinator and an independent verifier available.
    max_workers = (
        max(0, min(10, available_total - 2)) if available_total is not None else None
    )
    dispatch_ready = bool(
        native_enabled is True
        and known_limit
        and active_guard
        and (max_workers or 0) >= 10
        and not any(entry.get("errors") for entry in data["hooks"])
    )
    native_browser = browser_policy(config)
    native_browser["output_guard_trusted"] = features.get("hooks") is not False and any(
        hook.get("eventName") in {"preToolUse", "PreToolUse"}
        and hook.get("enabled") is True
        and hook.get("trustStatus") in {"trusted", "managed"}
        and covers_tools(
            hook,
            (
                *(
                    f"{prefix}browser_{name}"
                    for prefix in ("mcp__schemii_browser__", "mcp.schemii_browser.")
                    for name in (
                        "take_screenshot",
                        "snapshot",
                        "find",
                        "console_messages",
                    )
                ),
            ),
        )
        for hook in guards
    )
    return {
        "native_agents_enabled": native_enabled,
        "engine": "v2" if v2_enabled else "v1",
        "configured_child_limit": configured_limit,
        "runtime_total_slots": runtime_slots,
        "maximum_workers_with_reviewer_reserved": max_workers,
        "spawn_guard_trusted": active_guard,
        "dispatch_ready": dispatch_ready,
        "native_browser": native_browser,
        "native_browser_workers_with_reviewer_reserved": max(0, available_total - 2)
        if available_total is not None
        else None,
        "guard_hooks": [
            {
                "event": hook.get("eventName"),
                "enabled": hook.get("enabled"),
                "trust": hook.get("trustStatus"),
            }
            for hook in guards
        ],
        "hook_loading_errors": sum(
            len(entry.get("errors", [])) for entry in data["hooks"]
        ),
        "status": "runtime_capacity_unverified"
        if runtime_slots is None
        else "runtime_capacity_reported",
        "limitations": [
            "Reported runtime slots must come from the attached agent interface, not a requested count.",
            "This configuration check does not launch agents or prove simultaneous inference.",
            "Trusted local hooks can fail open and do not cover every hosted tool path.",
            "T3 preview tools share the chat browser context; schemii_browser uses a separate stdio connection per native thread when loaded.",
            "Browser configuration does not prove running tools, account isolation, cleanup or completed UI acceptance; run the dedicated verification first.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cwd", type=Path, default=Path.cwd())
    parser.add_argument(
        "--runtime-slots",
        type=int,
        help="Actual total slots advertised by this interface",
    )
    parser.add_argument(
        "--require-ready",
        action="store_true",
        help="Fail unless 10 workers and a trusted guard are available",
    )
    parser.add_argument(
        "--require-native-browser-config",
        action="store_true",
        help="Fail unless the isolated stdio launcher and UI-only tool policy are configured; does not launch browsers",
    )
    args = parser.parse_args()
    if args.runtime_slots is not None and args.runtime_slots < 1:
        parser.error("--runtime-slots must be positive")
    try:
        report = summarize(
            inspect_configuration(args.cwd.resolve()), args.runtime_slots
        )
    except (OSError, RuntimeError, ValueError) as error:
        print(json.dumps({"status": "blocked", "reason": str(error)}))
        return 2
    print(json.dumps(report, indent=2))
    return (
        2
        if (
            args.require_ready
            and not report["dispatch_ready"]
            or args.require_native_browser_config
            and not report["native_browser"]["isolated_stdio_policy_valid"]
        )
        else 0
    )


if __name__ == "__main__":
    raise SystemExit(main())
