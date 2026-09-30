#!/usr/bin/env python3
"""Exercise real native browser transport cleanup without AI turns or app writes.

Uses ephemeral stock Codex threads, the configured stdio launcher, and synthetic
data URLs. Never kill a process unless this probe captured its birth identity.
Temporary output must disappear through the launcher/guardian or startup sweep;
this probe never deletes a session directory. The optional TTL check takes ten
minutes and belongs outside ordinary PR feedback.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import time

import verify_browser_isolation as probe


BUDGET = 20 * 1024 * 1024
PAGE = "data:text/html,<title>Native cleanup probe</title><h1>Native cleanup probe</h1>"


def wait_for(condition, message: str, timeout: float = 15) -> float:
    start = time.monotonic()
    while not condition():
        probe.require(time.monotonic() - start < timeout, message)
        time.sleep(0.1)
    return round(time.monotonic() - start, 3)


def live_identities(identities: set[tuple[int, int]]) -> set[tuple[int, int]]:
    return {
        probe.identity(record)
        for record in probe.process_snapshot().values()
        if record["state"] not in {"Z", "X", "x"}
        and probe.identity(record) in identities
    }


def signal_owned(pid: int, birth_tick: int, signum: int, *, group=False) -> None:
    record = probe.process_snapshot().get(pid)
    probe.require(
        record is not None and probe.identity(record) == (pid, birth_tick),
        "Disposable process identity changed before signal",
    )
    if group:
        probe.require(os.getpgid(pid) == pid, "Disposable process group is not owned")
        os.killpg(pid, signum)
    else:
        os.kill(pid, signum)


class Connection:
    def __init__(self, cwd: Path, timeout: float):
        self.root = cwd / "artifacts" / "native-browsers"
        self.baseline = probe.session_names(self.root)
        self.sessions = set()
        self.clients = []
        self.server = probe.AppServer(cwd, timeout)
        self.closed = False
        self.cwd = cwd
        try:
            self.initialize()
        except BaseException:
            # The caller cannot track this object until construction returns.
            # Own the failed-start transport here, including interruption.
            self.shutdown()
            raise

    def initialize(self):
        self.server.capture_owned()
        self.server.request(
            "initialize",
            {
                "clientInfo": {"name": "schemii_cleanup_probe", "version": "1.0.0"},
                "capabilities": {"experimentalApi": True},
            },
        )
        self.server.send({"jsonrpc": "2.0", "method": "initialized", "params": {}})
        config = self.server.request(
            "config/read", {"cwd": str(self.cwd), "includeLayers": False}
        )["config"]
        self.overrides = probe.ephemeral_overrides(config)

    def start(self) -> dict:
        result = self.server.request(
            "thread/start",
            {"cwd": str(self.cwd), "ephemeral": True, "config": self.overrides},
        )
        probe.require(result["thread"].get("ephemeral"), "Probe thread is persistent")
        client = {"thread": result["thread"]["id"]}
        self.clients.append(client)
        probe.inventory(self.server, client["thread"])
        path, metadata = probe.wait_session(
            self.root, self.baseline, self.sessions, self.server
        )
        client.update(session=path, metadata=metadata)
        self.sessions.add(path)
        self.call(client, "browser_navigate", {"url": PAGE})
        return client

    def call(self, client: dict, tool: str, arguments=None):
        return probe.call(self.server, client["thread"], tool, arguments)

    def capture(self) -> set[tuple[int, int]]:
        self.server.capture_owned()
        return set(self.server.known_processes)

    def shutdown(self):
        if self.closed:
            return
        self.closed = True
        return probe.cleanup(
            self.server, self.clients, self.sessions, self.root, self.baseline
        )


def assert_released(connection: Connection, owned: set[tuple[int, int]]) -> dict:
    seconds = wait_for(
        lambda: (
            not any(
                probe.identity(record) in owned
                for record in probe.process_snapshot().values()
            )
            and not any(path.exists() for path in connection.sessions)
        ),
        "Owned transports left process entries or disposable directories",
    )
    remaining_entries = [
        record
        for record in probe.process_snapshot().values()
        if probe.identity(record) in owned
    ]
    return {
        "cleanup_seconds": seconds,
        "owned_process_identities": len(owned),
        "live_owned_processes": len(live_identities(owned)),
        "remaining_owned_process_entries": len(remaining_entries),
        "remaining_owned_directories": sum(
            path.exists() for path in connection.sessions
        ),
    }


def check_peer(connection: Connection, client: dict, marker: Path):
    probe.require(marker.read_bytes() == b"live peer", "Live peer output changed")
    result = connection.call(client, "browser_snapshot")
    probe.require(
        "Native cleanup probe" in probe.text_content(result), "Live peer failed"
    )


def check_budget(connection: Connection, client: dict) -> dict:
    output = client["session"] / "output"
    old = output / "budget-owned-old.bin"
    with old.open("xb") as handle:
        handle.truncate(BUDGET + 1)
    before = sum(path.stat().st_size for path in output.rglob("*") if path.is_file())
    connection.call(client, "browser_snapshot")
    probe.require(not old.exists(), "MCP did not evict old output above its budget")
    connection.call(
        client,
        "browser_evaluate",
        {
            "function": """() => {
          document.body.style.margin = '0'; document.body.innerHTML = '';
          const canvas = document.createElement('canvas');
          canvas.width = canvas.height = 3000; document.body.append(canvas);
          const context = canvas.getContext('2d');
          const image = context.createImageData(3000, 3000);
          for (let i=0;i<image.data.length;i+=65536)
            crypto.getRandomValues(image.data.subarray(i, Math.min(i+65536,image.data.length)));
          for (let i=3;i<image.data.length;i+=4) image.data[i]=255;
          context.putImageData(image, 0, 0); return {ready:true};
        }"""
        },
    )
    oversized_image = probe.png_response(
        connection.call(
            client, "browser_take_screenshot", {"type": "png", "fullPage": True}
        )
    )
    current = [path for path in output.glob("*.png") if path.stat().st_size > BUDGET]
    probe.require(len(current) == 1, "Oversized current response was not retained")
    exempt_bytes = current[0].stat().st_size
    probe.require(
        current[0].read_bytes() == oversized_image, "Oversized image transport differs"
    )
    connection.call(client, "browser_navigate", {"url": PAGE})
    after = sum(path.stat().st_size for path in output.rglob("*") if path.is_file())
    probe.require(not current[0].exists() and after <= BUDGET, "Budget did not recover")
    return {
        "budget_bytes": BUDGET,
        "before_eviction_bytes": before,
        "older_output_evicted": True,
        "current_response_exempt_bytes": exempt_bytes,
        "after_next_response_bytes": after,
    }


def check_expiry(connection: Connection, client: dict, peer_marker: Path) -> dict:
    output = client["session"] / "output"
    data = probe.png_response(
        connection.call(client, "browser_take_screenshot", {"type": "png"})
    )
    images = [path for path in output.glob("*.png") if path.read_bytes() == data]
    probe.require(len(images) == 1, "TTL screenshot did not have owned output")
    screenshot = images[0]
    created = screenshot.stat().st_mtime
    refreshed = time.monotonic()

    def expired():
        nonlocal refreshed
        if time.monotonic() - refreshed >= 30:
            os.utime(peer_marker)
            refreshed = time.monotonic()
        return not screenshot.exists()

    wait_for(expired, "Real ten-minute output expiry failed", 650)
    age = round(time.time() - created, 3)
    probe.require(
        600 <= age <= 635, "Output did not expire within its documented interval"
    )
    probe.require((client["session"] / "session.json").exists(), "TTL removed metadata")
    return {
        "screenshot_sha256": hashlib.sha256(data).hexdigest(),
        "observed_age_seconds": age,
    }


def verify(cwd: Path, timeout: float, expiry: bool) -> dict:
    summary = {
        "result": "failed",
        "ai_turns_started": 0,
        "session_directories_manually_removed": 0,
        "limits": [
            "Mechanical native transports and synthetic pages; no Schemii application acceptance.",
            "Forced tests target only recorded disposable identities, never the attached T3 session.",
            "Native turn interruption is a coordinator test; this utility does not request inference turns.",
            "Stock thread/unsubscribe has a grace period; owned app-server exit ends these transports.",
        ],
    }
    connections = []
    try:
        peer = Connection(cwd, timeout)
        connections.append(peer)
        peer_client = peer.start()
        peer_marker = peer_client["session"] / "output" / "cleanup-peer.dat"
        peer_marker.write_bytes(b"live peer")

        normal = Connection(cwd, timeout)
        connections.append(normal)
        client = normal.start()
        summary["budget"] = check_budget(normal, client)
        check_peer(peer, peer_client, peer_marker)
        normal.call(client, "browser_close")
        probe.require(client["session"].exists(), "browser_close ended the transport")
        normal.call(client, "browser_navigate", {"url": PAGE})
        summary["browser_close_retains_transport"] = True
        if expiry:
            summary["real_ten_minute_expiry"] = check_expiry(
                normal, client, peer_marker
            )
        owned = normal.capture()
        summary["normal_shutdown"] = normal.shutdown()
        summary["normal_shutdown"].update(assert_released(normal, owned))
        check_peer(peer, peer_client, peer_marker)

        forced = Connection(cwd, timeout)
        connections.append(forced)
        client = forced.start()
        owned = forced.capture()
        supervisor = client["metadata"]
        signal_owned(
            supervisor["pid"], supervisor["birth_tick"], signal.SIGKILL, group=True
        )
        # The live MCP child keeps its connection/output until the client exits.
        time.sleep(1.1)
        probe.require(
            client["session"].exists(), "Guardian removed a live child session"
        )
        forced.server.close()
        forced.closed = True
        summary["forced_launcher_shutdown"] = assert_released(forced, owned)
        check_peer(peer, peer_client, peer_marker)

        killed = Connection(cwd, timeout)
        connections.append(killed)
        client = killed.start()
        owned = killed.capture()
        owner = killed.server.owner
        signal_owned(*owner, signal.SIGKILL)
        killed.server.close()
        killed.closed = True
        summary["forced_app_server_shutdown"] = assert_released(killed, owned)
        check_peer(peer, peer_client, peer_marker)

        orphan = Connection(cwd, timeout)
        connections.append(orphan)
        client = orphan.start()
        owned = orphan.capture()
        metadata = client["metadata"]
        signal_owned(
            metadata["guardian_pid"], metadata["guardian_birth_tick"], signal.SIGKILL
        )
        signal_owned(
            metadata["pid"], metadata["birth_tick"], signal.SIGKILL, group=True
        )
        orphan.server.close()
        orphan.closed = True
        wait_for(lambda: not live_identities(owned), "Orphan processes did not exit")
        probe.require(
            client["session"].exists(),
            "Orphan test did not create recovery precondition",
        )
        recovery = Connection(cwd, timeout)
        connections.append(recovery)
        recovery.start()
        summary["orphan_recovery"] = assert_released(orphan, owned)
        check_peer(peer, peer_client, peer_marker)
        summary["live_peer_preserved"] = True
        summary["result"] = "passed"
    except probe.ProbeError as error:
        summary["reason"] = str(error)
    except (OSError, ValueError, KeyError, TypeError) as error:
        summary["reason"] = f"Unexpected cleanup probe failure ({type(error).__name__})"
    finally:
        summaries = []
        for connection in reversed(connections):
            try:
                owned = (
                    connection.capture()
                    if not connection.closed
                    else set(connection.server.known_processes)
                )
                connection.shutdown()
                summaries.append(assert_released(connection, owned))
            except (OSError, probe.ProbeError, ValueError):
                summary["result"] = "failed"
                summary.setdefault(
                    "reason", "Owned cleanup did not complete automatically"
                )
        summary["final_cleanup"] = summaries
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cwd", type=Path, default=Path.cwd())
    parser.add_argument("--timeout", type=float, default=45)
    parser.add_argument(
        "--include-expiry", action="store_true", help="Also wait ten real minutes"
    )
    args = parser.parse_args()
    if not 1 <= args.timeout <= 300:
        parser.error("--timeout must be between 1 and 300 seconds")
    os.umask(0o077)
    result = verify(args.cwd.resolve(strict=True), args.timeout, args.include_expiry)
    print(json.dumps(result, separators=(",", ":")))
    return 0 if result["result"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
