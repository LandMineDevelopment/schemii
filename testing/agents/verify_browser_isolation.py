#!/usr/bin/env python3
"""Verify native Codex thread-owned browser MCP clients without starting AI turns.

Uses the installed app-server JSON-RPC API, the project's pinned stdio launcher,
and the already-running canonical HTTPS app. Synthetic fixture actions are
mechanical readiness evidence, never Schemii application acceptance. No server,
account, provider turn, credential export, or persistent proof directory is made.
"""

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import stat
import struct
import subprocess
import time
import uuid


SERVER = "schemii_browser"
PACKAGE = "@playwright/mcp@0.0.83"
ORIGIN = "https://localhost:8001"
URL = f"{ORIGIN}/account"
DOWNLOAD = "probe-download.txt"
TEST_ONLY_TOOLS = ("browser_evaluate", "browser_run_code_unsafe")
REQUIRED_TOOLS = frozenset(
    {
        "browser_close",
        "browser_navigate",
        "browser_resize",
        "browser_tabs",
        "browser_snapshot",
        "browser_type",
        "browser_click",
        "browser_handle_dialog",
        "browser_file_upload",
        "browser_take_screenshot",
        *TEST_ONLY_TOOLS,
    }
)
# This client cannot request an inference turn, login, or configuration write.
RPC_METHODS = frozenset(
    {
        "initialize",
        "config/read",
        "thread/start",
        "mcpServerStatus/list",
        "mcpServer/tool/call",
        "thread/unsubscribe",
    }
)


class ProbeError(RuntimeError):
    """A safe diagnostic that contains no server response or ambient secret."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ProbeError(message)


def parse_proc_stat(raw: str) -> dict:
    """Read identity/RSS fields without command lines or environment variables."""
    pid_text, separator, rest = raw.partition(" (")
    _, closing, fields = rest.rpartition(") ")
    require(bool(separator and closing), "Malformed process identity")
    values = fields.split()
    return {
        "pid": int(pid_text),
        "state": values[0],
        "ppid": int(values[1]),
        "birth_tick": int(values[19]),
        "rss": int(values[21]) * os.sysconf("SC_PAGE_SIZE"),
    }


def process_snapshot() -> dict[int, dict]:
    records = {}
    for directory in Path("/proc").iterdir():
        if not directory.name.isdecimal():
            continue
        try:
            record = parse_proc_stat((directory / "stat").read_text())
        except (OSError, ValueError, IndexError, ProbeError):
            continue  # Processes can exit during this snapshot.
        records[record["pid"]] = record
    return records


def identity(record: dict) -> tuple[int, int]:
    return record["pid"], record["birth_tick"]


def descendants(records: dict[int, dict], owner: tuple[int, int]) -> dict[int, dict]:
    root = records.get(owner[0])
    if root is None or identity(root) != owner:
        return {}
    owned = {owner[0]: root}
    while True:
        added = {
            pid: record
            for pid, record in records.items()
            if pid not in owned and record["ppid"] in owned
        }
        if not added:
            return owned
        owned.update(added)


class AppServer:
    """One short-lived stdlib client for the installed Codex stdio protocol."""

    def __init__(self, cwd: Path, timeout: float):
        env = dict(os.environ)
        env.pop("T3_MCP_BEARER_TOKEN", None)
        self.process = subprocess.Popen(
            ["codex", "app-server", "--strict-config"],
            cwd=cwd,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.process.stdout, selectors.EVENT_READ)
        self.buffer = bytearray()
        self.next_id = 0
        self.timeout = timeout
        self.owner = None
        self.known_processes = set()

    def capture_owned(self) -> dict[int, dict]:
        records = process_snapshot()
        if self.owner is None:
            record = records.get(self.process.pid)
            require(record is not None, "Temporary Codex app-server exited")
            self.owner = identity(record)
        owned = descendants(records, self.owner)
        self.known_processes.update(identity(record) for record in owned.values())
        return owned

    def send(self, payload: dict) -> None:
        self.process.stdin.write((json.dumps(payload) + "\n").encode())

    def request(
        self, method: str, params: dict, *, timeout: float | None = None
    ) -> dict:
        require(method in RPC_METHODS, "Probe attempted a prohibited RPC method")
        self.next_id += 1
        request_id = self.next_id
        self.send(
            {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        )
        deadline = time.monotonic() + (self.timeout if timeout is None else timeout)
        while True:
            while b"\n" in self.buffer:
                line, _, remaining = self.buffer.partition(b"\n")
                self.buffer[:] = remaining
                response = json.loads(line)
                if response.get("id") == request_id and "method" not in response:
                    if "error" in response:
                        # The upstream message can contain config values or page text.
                        raise ProbeError(
                            f"Codex rejected {method}; inspect the installed protocol"
                        )
                    require(
                        isinstance(response.get("result"), dict),
                        "Unexpected Codex response",
                    )
                    return response["result"]
                if "method" in response and "id" in response:
                    self.send(
                        {
                            "jsonrpc": "2.0",
                            "id": response["id"],
                            "error": {
                                "code": -32601,
                                "message": "Mechanical probe handles no server requests",
                            },
                        }
                    )
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not self.selector.select(remaining):
                raise ProbeError(f"Codex timed out responding to {method}")
            chunk = os.read(self.process.stdout.fileno(), 65536)
            require(bool(chunk), "Temporary Codex app-server closed its transport")
            self.buffer.extend(chunk)

    def close(self) -> None:
        self.selector.close()
        self.process.stdin.close()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.terminate()  # Only the app-server created by this probe.
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        self.process.stdout.close()


def ephemeral_overrides(config: dict) -> dict:
    """Allow setup JS only in ephemeral test threads; leave project files intact."""
    servers = config.get("mcp_servers") or {}
    browser = servers.get(SERVER)
    require(
        isinstance(browser, dict) and browser.get("command"),
        "Project stdio browser MCP is not configured",
    )
    require(not browser.get("url"), "Browser probe requires the project stdio launcher")
    allowed = browser.get("enabled_tools")
    require(
        isinstance(allowed, list) and all(isinstance(tool, str) for tool in allowed),
        "Project browser tool allowlist is missing",
    )
    require(
        REQUIRED_TOOLS - set(TEST_ONLY_TOOLS) <= set(allowed),
        "Project browser allowlist lacks a required primitive",
    )
    overrides = {}
    for name in servers:
        # Quoted segments keep dots in an MCP name from becoming key traversal.
        overrides[f"mcp_servers.{json.dumps(name)}.enabled"] = name == SERVER
    prefix = f"mcp_servers.{json.dumps(SERVER)}"
    overrides[f"{prefix}.required"] = True
    overrides[f"{prefix}.enabled_tools"] = sorted(set(allowed) | set(TEST_ONLY_TOOLS))
    overrides[f"{prefix}.disabled_tools"] = [
        tool
        for tool in browser.get("disabled_tools", [])
        if tool not in TEST_ONLY_TOOLS
    ]
    # Installed plugins may contribute additional MCP servers. Disable them only
    # in these throwaway thread configs, then verify the actual runtime inventory.
    for name in config.get("plugins") or {}:
        overrides[f"plugins.{json.dumps(name)}.enabled"] = False
    return overrides


def call(
    server: AppServer,
    thread: str,
    tool: str,
    arguments: dict | None = None,
    *,
    timeout: float | None = None,
) -> dict:
    require(tool in REQUIRED_TOOLS, "Probe attempted an unassigned browser tool")
    response = server.request(
        "mcpServer/tool/call",
        {
            "threadId": thread,
            "server": SERVER,
            "tool": tool,
            "arguments": arguments or {},
        },
        timeout=timeout,
    )
    require(not response.get("isError"), f"Browser primitive failed: {tool}")
    return response


def text_content(response: dict) -> str:
    return "\n".join(
        block["text"]
        for block in response.get("content", [])
        if block.get("type") == "text" and isinstance(block.get("text"), str)
    )


def result_json(response: dict) -> dict:
    text = text_content(response)
    section = re.search(r"(?:\A|\n)### Result\n(.*?)(?=\n### |\Z)", text, re.S)
    try:
        result = json.loads(section.group(1).strip() if section else text)
    except (ValueError, AttributeError):
        raise ProbeError("Browser did not return the synthetic probe result") from None
    require(isinstance(result, dict), "Synthetic probe result is not an object")
    return result


def snapshot_ref(response: dict, role: str, label: str) -> str:
    matches = re.findall(
        rf'\b{re.escape(role)} "{re.escape(label)}"[^\n]*?\[ref=([^\]\s]+)\]',
        text_content(response),
    )
    require(len(matches) == 1, f"Synthetic fixture lacks one {role} reference")
    return matches[0]


def png_response(response: dict) -> bytes:
    images = [
        block
        for block in response.get("content", [])
        if block.get("type") == "image" and block.get("mimeType") == "image/png"
    ]
    require(len(images) == 1, "Screenshot did not use one MCP PNG image response")
    try:
        data = base64.b64decode(images[0]["data"], validate=True)
    except (ValueError, KeyError):
        raise ProbeError("Screenshot image transport is invalid") from None
    require(
        data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) > 24,
        "Screenshot transport is not PNG",
    )
    require(
        data[12:16] == b"IHDR" and all(struct.unpack(">II", data[16:24])),
        "Screenshot PNG has no dimensions",
    )
    return data


def session_names(root: Path) -> set[str]:
    return {path.name for path in root.iterdir()} if root.exists() else set()


def owned_sessions(
    root: Path, baseline: set[str], server: AppServer
) -> dict[Path, dict]:
    owned = server.capture_owned()
    sessions = {}
    for name in session_names(root) - baseline:
        if not re.fullmatch(r"session-[0-9a-f]{32}", name):
            continue
        path = root / name
        metadata_path = path / "session.json"
        if path.is_symlink() or metadata_path.is_symlink():
            continue
        try:
            metadata = json.loads(metadata_path.read_text())
            owner = (int(metadata["pid"]), int(metadata["birth_tick"]))
        except (OSError, ValueError, KeyError, TypeError):
            continue
        record = owned.get(owner[0])
        if record is not None and identity(record) == owner:
            sessions[path] = metadata
    return sessions


def wait_session(
    root: Path, baseline: set[str], seen: set[Path], server: AppServer
) -> tuple[Path, dict]:
    deadline = time.monotonic() + server.timeout
    while time.monotonic() < deadline:
        fresh = {
            path: metadata
            for path, metadata in owned_sessions(root, baseline, server).items()
            if path not in seen
        }
        if fresh and all(
            metadata.get("state") == "running" for metadata in fresh.values()
        ):
            require(
                len(fresh) == 1,
                "One thread created an unexpected number of browser transports",
            )
            path, metadata = next(iter(fresh.items()))
            require(
                metadata.get("session_id") == path.name
                and metadata.get("package") == PACKAGE,
                "Browser launcher metadata differs from the pinned contract",
            )
            require(
                stat.S_IMODE(path.stat().st_mode) == 0o700,
                "Browser session directory is not private",
            )
            output = path / "output"
            require(
                not output.is_symlink() and output.is_dir(),
                "Private MCP output directory is missing",
            )
            require(
                stat.S_IMODE(output.stat().st_mode) == 0o700,
                "MCP output directory is not private",
            )
            return path, metadata
        time.sleep(0.05)
    raise ProbeError("Thread-owned browser metadata did not become ready")


def inventory(server: AppServer, thread: str) -> None:
    response = server.request(
        "mcpServerStatus/list", {"threadId": thread, "detail": "full", "limit": 100}
    )
    entries = response.get("data", [])
    require(
        not response.get("nextCursor"),
        "MCP inventory exceeded the probe's bounded page",
    )
    active = [
        entry
        for entry in entries
        if entry.get("runtimeStatus") not in {"disabled", None}
    ]
    require(
        len(active) == 1 and active[0].get("name") == SERVER,
        "Ephemeral thread enabled another MCP server",
    )
    browser = active[0]
    require(
        browser.get("runtimeStatus") == "connected" and not browser.get("toolsError"),
        "Thread browser MCP did not connect",
    )
    tools = {tool.get("name") for tool in browser.get("tools", {}).values()}
    require(
        REQUIRED_TOOLS <= tools,
        "Pinned browser MCP did not expose the required test tools",
    )


def fixture_code(marker: str, key: str) -> str:
    """Only context-local markers and synthetic DOM; no app/API writes."""
    return (
        """async (page) => {
      const marker = MARKER, key = KEY;
      if (page.url() !== URL) throw new Error('Unexpected canonical page');
      await page.context().addCookies([{name:key, value:marker, url:ORIGIN,
        httpOnly:true, secure:true, sameSite:'Lax'}]);
      await page.evaluate(({marker,key}) => {
        localStorage.setItem(key, marker);
        document.title = marker;
        document.body.innerHTML = '<main><h1>Mechanical browser probe</h1>' +
          '<p id="marker"></p><label>Probe text <input id="text"></label>' +
          '<button id="dialog">Probe dialog</button><output id="answer"></output>' +
          '<button id="upload">Probe upload</button><input id="file" type="file" hidden>' +
          '<a id="download" download="probe-download.txt">Probe download</a></main>';
        document.body.dataset.probe = marker;
        document.body.style.cssText = 'font:18px sans-serif;background:white;color:black;padding:24px';
        document.getElementById('marker').textContent = marker;
        document.getElementById('dialog').onclick = () => {
          document.getElementById('answer').textContent = prompt('Mechanical probe prompt') || '';
        };
        document.getElementById('upload').onclick = () => document.getElementById('file').click();
        document.getElementById('download').href = 'data:text/plain;charset=utf-8,' + encodeURIComponent(marker);
      }, {marker,key});
      return {ready:true};
    }""".replace("MARKER", json.dumps(marker))
        .replace("KEY", json.dumps(key))
        .replace("URL", json.dumps(URL))
        .replace("ORIGIN", json.dumps(ORIGIN))
    )


def state_code(key: str) -> str:
    return """async (page) => {
      const key = KEY;
      const cookies = (await page.context().cookies(ORIGIN)).filter(c => c.name === key);
      const state = await page.evaluate(key => ({
        local:localStorage.getItem(key), marker:document.body.dataset.probe || null,
        scriptCookieVisible:document.cookie.split(';').some(c => c.trim().startsWith(key+'='))
      }), key);
      return {...state, cookie:cookies[0]?.value || null,
        httpOnly:cookies.length === 1 && cookies[0].httpOnly,
        title:await page.title(), url:page.url(),
        titles:await Promise.all(page.context().pages().map(p => p.title()))};
    }""".replace("KEY", json.dumps(key)).replace("ORIGIN", json.dumps(ORIGIN))


def read_state(server: AppServer, client: dict, key: str) -> dict:
    return result_json(
        call(
            server,
            client["thread"],
            "browser_run_code_unsafe",
            {"code": state_code(key)},
        )
    )


def assert_state(state: dict, marker: str, *, tab: bool = False) -> None:
    title = marker + ("-tab" if tab else "")
    require(
        state.get("cookie") == marker and state.get("local") == marker,
        "Same-origin cookie or storage isolation failed",
    )
    require(
        state.get("httpOnly") is True and state.get("scriptCookieVisible") is False,
        "Probe cookie is not isolated HttpOnly state",
    )
    require(
        state.get("marker") == title and state.get("title") == title,
        "Browser page routing crossed thread ownership",
    )
    require(
        state.get("titles") == ([marker, title] if tab else [marker]),
        "Browser tab routing crossed thread ownership",
    )
    require(
        state.get("url") == URL + ("#probe-tab" if tab else ""),
        "Browser left the canonical probe page",
    )


def click_fixture(server: AppServer, client: dict, role: str, label: str) -> None:
    snapshot = call(server, client["thread"], "browser_snapshot")
    call(
        server,
        client["thread"],
        "browser_click",
        {"target": snapshot_ref(snapshot, role, label)},
    )


def exercise_primitives(server: AppServer, client: dict) -> None:
    thread = client["thread"]
    snapshot = call(server, thread, "browser_snapshot")
    call(
        server,
        thread,
        "browser_type",
        {
            "target": snapshot_ref(snapshot, "textbox", "Probe text"),
            "text": client["marker"],
        },
    )
    click_fixture(server, client, "button", "Probe dialog")
    call(
        server,
        thread,
        "browser_handle_dialog",
        {"accept": True, "promptText": client["marker"]},
    )
    upload = client["session"] / "output" / "probe-upload.txt"
    with upload.open("x", encoding="utf-8") as handle:
        handle.write(client["marker"])
    click_fixture(server, client, "button", "Probe upload")
    call(server, thread, "browser_file_upload", {"paths": [str(upload)]})
    result = result_json(
        call(
            server,
            thread,
            "browser_evaluate",
            {
                "function": """async () => ({
      typed:document.getElementById('text').value,
      dialog:document.getElementById('answer').textContent,
      uploaded:await document.getElementById('file').files[0].text()
    })"""
            },
        )
    )
    require(
        all(
            result.get(name) == client["marker"]
            for name in ("typed", "dialog", "uploaded")
        ),
        "Synthetic type/dialog/upload primitives did not preserve their input",
    )


def process_report(server: AppServer, clients: list[dict]) -> dict:
    owned = server.capture_owned()
    browser_sets = []
    for client in clients:
        metadata = client["metadata"]
        child = (int(metadata["child_pid"]), int(metadata["child_birth_tick"]))
        transport = descendants(
            owned, (int(metadata["pid"]), int(metadata["birth_tick"]))
        )
        require(
            child[0] in transport and identity(transport[child[0]]) == child,
            "Launcher child process identity differs from metadata",
        )
        tree = descendants(owned, child)
        browsers = {}
        for pid, record in tree.items():
            try:
                executable = Path(os.readlink(f"/proc/{pid}/exe")).name
            except OSError:
                continue
            if executable == "chromium" and record["state"] != "Z":
                browsers[pid] = record
        roots = [
            record for record in browsers.values() if record["ppid"] not in browsers
        ]
        require(len(roots) == 1, "Thread does not own one separate live Chromium tree")
        browser_sets.append(set(browsers))
    require(
        sum(map(len, browser_sets)) == len(set().union(*browser_sets)),
        "Chromium trees overlap between thread transports",
    )
    browser_pids = set().union(*browser_sets)
    return {
        "browser_trees": len(browser_sets),
        "browser_processes": len(browser_pids),
        "browser_rss_mib": round(
            sum(owned[pid]["rss"] for pid in browser_pids) / 1048576, 1
        ),
        "owned_processes": len(owned),
    }


def cleanup(
    server: AppServer,
    clients: list[dict],
    sessions: set[Path],
    root: Path,
    baseline: set[str],
) -> dict:
    report = {
        "browser_close_ok": 0,
        "unsubscribed": 0,
        "app_server_stopped": False,
        "session_directories_removed": False,
        "live_owned_processes": 0,
    }
    try:
        sessions.update(owned_sessions(root, baseline, server))
    except (OSError, ProbeError):
        pass
    for client in clients:
        try:
            call(
                server,
                client["thread"],
                "browser_close",
                timeout=min(server.timeout, 5),
            )
            report["browser_close_ok"] += 1
        except (OSError, ProbeError, ValueError):
            pass
        try:
            response = server.request(
                "thread/unsubscribe",
                {"threadId": client["thread"]},
                timeout=min(server.timeout, 5),
            )
            report["unsubscribed"] += response.get("status") == "unsubscribed"
        except (OSError, ProbeError, ValueError):
            pass
    try:
        server.capture_owned()
        server.close()
        report["app_server_stopped"] = server.process.poll() is not None
    except (OSError, ProbeError, subprocess.TimeoutExpired):
        # Still close the process even if an ownership snapshot failed.
        server.close()
        report["app_server_stopped"] = server.process.poll() is not None
    deadline = time.monotonic() + 15
    while True:
        records = process_snapshot()
        live = [
            record
            for record in records.values()
            if identity(record) in server.known_processes and record["state"] != "Z"
        ]
        remaining = [path for path in sessions if path.exists()]
        if not live and not remaining or time.monotonic() >= deadline:
            report["live_owned_processes"] = len(live)
            report["session_directories_removed"] = not remaining
            return report
        time.sleep(0.1)


def verify(cwd: Path, count: int, timeout: float) -> dict:
    root = cwd / "artifacts" / "native-browsers"
    baseline = session_names(root)
    clients = []
    sessions = set()
    summary = {
        "result": "failed",
        "requested_clients": count,
        "proven_clients": 0,
        "ai_turns_started": 0,
        "screenshot_image_png_responses": 0,
        "artifact_isolation": False,
        "idle_followup_retained": False,
        "close_reset_isolated": False,
        "limits": [
            "Ephemeral native Codex threads; no AI agents, inference concurrency, or application acceptance tested.",
            "Retention covers idle followups during this run, not an unlimited lifetime.",
            "thread/unsubscribe has an inactivity grace period; owned app-server shutdown releases transports.",
            "Temporary synthetic artifacts must disappear automatically; no screenshots are retained.",
        ],
    }
    server = AppServer(cwd, timeout)
    try:
        server.capture_owned()
        server.request(
            "initialize",
            {
                "clientInfo": {"name": "schemii_browser_probe", "version": "1.0.0"},
                "capabilities": {"experimentalApi": True},
            },
        )
        server.send({"jsonrpc": "2.0", "method": "initialized", "params": {}})
        config = server.request(
            "config/read", {"cwd": str(cwd), "includeLayers": False}
        )["config"]
        overrides = ephemeral_overrides(config)
        key = "schemii_probe_" + uuid.uuid4().hex
        for number in range(count):
            response = server.request(
                "thread/start",
                {"cwd": str(cwd), "ephemeral": True, "config": overrides},
            )
            thread = response["thread"]
            client = {"thread": thread["id"], "marker": f"{key}-{number + 1}"}
            clients.append(
                client
            )  # Track immediately so failures still unsubscribe it.
            require(
                thread.get("ephemeral") is True,
                "Codex created a persistent probe thread",
            )
            inventory(server, client["thread"])
            session, metadata = wait_session(root, baseline, sessions, server)
            sessions.add(session)
            client.update(session=session, metadata=metadata)
            call(server, client["thread"], "browser_navigate", {"url": URL})
            call(
                server,
                client["thread"],
                "browser_resize",
                {"width": 640, "height": 480},
            )
            result = result_json(
                call(
                    server,
                    client["thread"],
                    "browser_run_code_unsafe",
                    {"code": fixture_code(client["marker"], key)},
                )
            )
            require(
                result.get("ready") is True,
                "Synthetic probe fixture did not initialize",
            )
        require(
            len({client["thread"] for client in clients}) == count
            and len(sessions) == count,
            "Thread or stdio artifact ownership was reused",
        )
        for client in reversed(clients):
            assert_state(read_state(server, client, key), client["marker"])
        summary["idle_followup_retained"] = True
        summary["processes"] = process_report(server, clients)
        first = clients[0]
        exercise_primitives(server, first)
        call(
            server,
            first["thread"],
            "browser_tabs",
            {"action": "new", "url": URL + "#probe-tab"},
        )
        call(
            server,
            first["thread"],
            "browser_evaluate",
            {
                "function": "() => { const marker = "
                + json.dumps(first["marker"] + "-tab")
                + "; document.title=marker; document.body.textContent=marker; document.body.dataset.probe=marker; return {ready:true}; }"
            },
        )
        assert_state(read_state(server, first, key), first["marker"], tab=True)
        for client in clients[1:]:
            assert_state(read_state(server, client, key), client["marker"])
        call(server, first["thread"], "browser_tabs", {"action": "select", "index": 0})
        call(server, first["thread"], "browser_tabs", {"action": "close", "index": 1})
        assert_state(read_state(server, first, key), first["marker"])
        image_hashes = set()
        downloads = []
        for client in clients:
            data = png_response(
                call(
                    server, client["thread"], "browser_take_screenshot", {"type": "png"}
                )
            )
            image_hashes.add(hashlib.sha256(data).hexdigest())
            summary["screenshot_image_png_responses"] += 1
            images = list((client["session"] / "output").rglob("*.png"))
            require(
                any(
                    not image.is_symlink() and image.read_bytes() == data
                    for image in images
                ),
                "MCP PNG response differs from its private screenshot artifact",
            )
            click_fixture(server, client, "link", "Probe download")
            deadline = time.monotonic() + timeout
            while True:
                files = list((client["session"] / "output").rglob(DOWNLOAD))
                if files or time.monotonic() >= deadline:
                    break
                time.sleep(0.05)
            require(
                len(files) == 1
                and not files[0].is_symlink()
                and files[0].read_text() == client["marker"],
                "Thread's standard download did not reach its private output",
            )
            downloads.append((files[0], client["marker"]))
        require(
            len(image_hashes) == count,
            "Synthetic screenshots crossed browser ownership",
        )
        require(
            len({path.resolve() for path, _ in downloads}) == count
            and all(path.read_text() == marker for path, marker in downloads),
            "Same-filename downloads overwrote another thread's artifact",
        )
        summary["artifact_isolation"] = True
        call(server, first["thread"], "browser_close")
        call(server, first["thread"], "browser_navigate", {"url": URL})
        reset = read_state(server, first, key)
        require(
            reset.get("cookie") is None
            and reset.get("local") is None
            and reset.get("marker") is None,
            "browser_close did not reset the isolated context",
        )
        require(
            reset.get("url") == URL and len(reset.get("titles", [])) == 1,
            "Reopened browser did not own a fresh canonical page",
        )
        for client in clients[1:]:
            assert_state(read_state(server, client, key), client["marker"])
        summary["close_reset_isolated"] = True
        summary["proven_clients"] = count
        summary["result"] = "passed"
    except ProbeError as error:
        summary["reason"] = str(error)
    except KeyboardInterrupt:
        summary["reason"] = "Mechanical probe interrupted"
    except (OSError, ValueError, KeyError, TypeError) as error:
        # Never include arbitrary upstream/config/page/environment values.
        summary["reason"] = f"Unexpected probe failure ({type(error).__name__})"
    finally:
        summary["cleanup"] = cleanup(server, clients, sessions, root, baseline)
        released = summary["cleanup"]
        if not (
            released["app_server_stopped"]
            and released["session_directories_removed"]
            and released["live_owned_processes"] == 0
        ):
            summary["result"] = "failed"
            summary.setdefault(
                "reason", "Temporary browser resources did not clean up automatically"
            )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--clients",
        type=int,
        default=12,
        help="Live native Codex threads to prove (1..12; no AI turns)",
    )
    parser.add_argument(
        "--cwd",
        type=Path,
        default=Path.cwd(),
        help="Trusted checkout containing the pinned browser MCP configuration",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=45,
        help="Per-request timeout in seconds (1..300)",
    )
    args = parser.parse_args()
    if not 1 <= args.clients <= 12:
        parser.error("--clients must be between 1 and 12")
    if not 1 <= args.timeout <= 300:
        parser.error("--timeout must be between 1 and 300 seconds")
    os.umask(0o077)
    try:
        summary = verify(args.cwd.resolve(strict=True), args.clients, args.timeout)
    except (OSError, ProbeError) as error:
        summary = {
            "result": "failed",
            "proven_clients": 0,
            "ai_turns_started": 0,
            "reason": str(error)
            if isinstance(error, ProbeError)
            else "Could not start the temporary native Codex probe",
        }
    print(json.dumps(summary, separators=(",", ":")))
    return 0 if summary["result"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
