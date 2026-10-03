import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import selectors
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock


spec = importlib.util.spec_from_file_location(
    "agent_browser", Path(__file__).with_name("browser.py")
)
browser = importlib.util.module_from_spec(spec)
spec.loader.exec_module(browser)


class ArtifactFixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.repository = Path(self.temporary.name) / "checkout"
        self.repository.mkdir()

    def base(self):
        artifacts = self.repository / "artifacts"
        artifacts.mkdir(mode=0o755)
        base = artifacts / "native-browsers"
        base.mkdir(mode=0o700)
        return base


class ArtifactTests(ArtifactFixture):
    def test_sessions_are_unique_private_and_have_nonsecret_metadata(self):
        first = browser.create_session(self.repository).path
        second = browser.create_session(self.repository).path
        self.assertNotEqual(first, second)
        self.assertEqual(first.parent, second.parent)
        self.assertEqual(
            first.parent, self.repository / "artifacts" / "native-browsers"
        )
        for directory in (first.parent, first, second):
            self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
            self.assertEqual(directory.stat().st_uid, os.getuid())
        for session in (first, second):
            self.assertRegex(session.name, r"^session-[0-9a-f]{32}$")
            metadata_path = session / "session.json"
            self.assertEqual(stat.S_IMODE(metadata_path.stat().st_mode), 0o600)
            metadata = json.loads(metadata_path.read_text())
            self.assertEqual(
                set(metadata),
                {
                    "schema",
                    "session_id",
                    "package",
                    "pid",
                    "birth_tick",
                    "started_at",
                    "state",
                    "child_pid",
                    "child_birth_tick",
                },
            )
            self.assertEqual(metadata["schema"], 1)
            self.assertEqual(
                metadata["birth_tick"], browser.process_birth_tick(os.getpid())
            )
            self.assertEqual(metadata["state"], "launching")
            self.assertEqual(stat.S_IMODE((session / "output").stat().st_mode), 0o700)
            self.assertFalse((session / "output" / "session.json").exists())
            self.assertEqual(metadata["session_id"], session.name)
            self.assertEqual(metadata["package"], "@playwright/mcp@0.0.83")
            self.assertEqual(metadata["pid"], os.getpid())
            self.assertTrue(metadata["started_at"].endswith("+00:00"))

    def test_existing_downloads_and_other_artifacts_are_preserved(self):
        base = self.base()
        retained = base / "session-old"
        retained.mkdir(mode=0o700)
        download = retained / "report.csv"
        download.write_bytes(b"original download")
        unrelated = base.parent / "qa-output.txt"
        unrelated.write_bytes(b"other task output")
        existing_modes = {
            path: path.stat().st_mode for path in (base.parent, base, retained)
        }
        with mock.patch.object(os, "chmod") as chmod:
            new = browser.create_session(self.repository).path
        chmod.assert_not_called()
        self.assertNotEqual(new, retained)
        self.assertEqual(download.read_bytes(), b"original download")
        self.assertEqual(unrelated.read_bytes(), b"other task output")
        for path, mode in existing_modes.items():
            self.assertEqual(path.stat().st_mode, mode)

    def test_symlink_in_any_repository_ancestor_is_rejected(self):
        alias = Path(self.temporary.name) / "linked-checkout"
        alias.symlink_to(self.repository, target_is_directory=True)
        with self.assertRaises(OSError):
            browser.create_session(alias)
        self.assertFalse((self.repository / "artifacts").exists())

    def test_artifacts_symlink_is_rejected_without_touching_target(self):
        target = Path(self.temporary.name) / "outside"
        target.mkdir()
        (self.repository / "artifacts").symlink_to(target, target_is_directory=True)
        with self.assertRaises(OSError):
            browser.create_session(self.repository)
        self.assertEqual(list(target.iterdir()), [])

    def test_session_base_symlink_and_dangling_symlink_are_rejected(self):
        artifacts = self.repository / "artifacts"
        artifacts.mkdir()
        target = Path(self.temporary.name) / "outside"
        target.mkdir()
        link = artifacts / "native-browsers"
        for destination in (target, target / "missing"):
            with self.subTest(destination=destination):
                link.symlink_to(destination, target_is_directory=True)
                try:
                    with self.assertRaises(OSError):
                        browser.create_session(self.repository)
                    self.assertTrue(link.is_symlink())
                    self.assertEqual(list(target.iterdir()), [])
                finally:
                    link.unlink()

    def test_nonprivate_session_base_is_rejected_without_permission_repair(self):
        base = self.base()
        for mode in (0o755, 0o750, 0o711, 0o777, 0o500):
            with self.subTest(mode=oct(mode)):
                base.chmod(mode)
                with mock.patch.object(os, "chmod") as chmod:
                    with self.assertRaises(browser.ArtifactError):
                        browser.create_session(self.repository)
                chmod.assert_not_called()
                self.assertEqual(stat.S_IMODE(base.stat().st_mode), mode)
                self.assertEqual(list(base.iterdir()), [])

    def test_group_writable_artifacts_are_rejected(self):
        artifacts = self.repository / "artifacts"
        artifacts.mkdir()
        artifacts.chmod(0o775)
        with self.assertRaises(browser.ArtifactError):
            browser.create_session(self.repository)
        self.assertEqual(stat.S_IMODE(artifacts.stat().st_mode), 0o775)
        self.assertEqual(list(artifacts.iterdir()), [])

    def test_nonowned_artifact_directory_is_rejected(self):
        base = self.base()
        real_fstat = os.fstat

        def foreign_owner(descriptor):
            actual = real_fstat(descriptor)
            if actual.st_ino == base.stat().st_ino:
                return mock.Mock(st_uid=os.getuid() + 1, st_mode=actual.st_mode)
            return actual

        with mock.patch.object(os, "fstat", side_effect=foreign_owner):
            with self.assertRaises(browser.ArtifactError):
                browser.create_session(self.repository)
        self.assertEqual(list(base.iterdir()), [])

    def test_directory_collision_never_reuses_existing_output(self):
        base = self.base()
        session = base / ("session-" + "a" * 32)
        session.mkdir(mode=0o700)
        download = session / "report.csv"
        download.write_bytes(b"keep")
        with mock.patch.object(browser.secrets, "token_hex", return_value="a" * 32):
            with self.assertRaises(FileExistsError):
                browser.create_session(self.repository)
        self.assertEqual(download.read_bytes(), b"keep")
        self.assertFalse((session / "session.json").exists())

    def test_regular_file_session_base_is_rejected_and_preserved(self):
        artifacts = self.repository / "artifacts"
        artifacts.mkdir()
        base = artifacts / "native-browsers"
        base.write_bytes(b"existing data")
        with self.assertRaises(OSError):
            browser.create_session(self.repository)
        self.assertEqual(base.read_bytes(), b"existing data")


class LifecycleTests(ArtifactFixture):
    def running_session(self):
        session = browser.create_session(self.repository)
        session.metadata.update(
            state="running",
            pid=500001,
            birth_tick=100,
            child_pid=500002,
            child_birth_tick=200,
        )
        (session.path / "session.json").write_text(json.dumps(session.metadata))
        (session.path / "output" / "report.csv").write_bytes(b"temporary output")
        return session

    def sweep(self, ticks):
        with mock.patch.object(
            browser, "process_birth_tick", side_effect=lambda pid: ticks.get(pid)
        ):
            with browser._artifact_base(self.repository) as base:
                browser._sweep_orphans(base)

    def test_owned_cleanup_removes_only_session_and_does_not_follow_internal_symlinks(
        self,
    ):
        session = self.running_session()
        outside = self.repository / "user-report.csv"
        outside.write_bytes(b"preserve copied evidence")
        (session.path / "output" / "external").symlink_to(outside)
        browser.cleanup_session(session)
        self.assertFalse(session.path.exists())
        self.assertEqual(outside.read_bytes(), b"preserve copied evidence")
        self.assertTrue(session.path.parent.exists())

    def test_cleanup_refuses_replaced_session_directory(self):
        session = self.running_session()
        preserved = session.path.with_name("retained-original")
        session.path.rename(preserved)
        session.path.mkdir(mode=0o700)
        unrelated = session.path / "unrelated.txt"
        unrelated.write_bytes(b"keep replacement")
        with self.assertRaises(browser.ArtifactError):
            browser.cleanup_session(session)
        self.assertEqual(unrelated.read_bytes(), b"keep replacement")
        self.assertTrue((preserved / "output" / "report.csv").exists())

    def test_cleanup_directory_swap_before_recursion_preserves_replacement_data(self):
        session = self.running_session()
        retained = session.path.with_name("retained-original")
        original_remove = browser._remove_contents
        swapped = False

        def replace_before_recursion(descriptor):
            nonlocal swapped
            if not swapped:
                swapped = True
                session.path.rename(retained)
                session.path.mkdir(mode=0o700)
                (session.path / "user-data.txt").write_bytes(
                    b"unrelated replacement data"
                )
            return original_remove(descriptor)

        with mock.patch.object(
            browser, "_remove_contents", side_effect=replace_before_recursion
        ):
            with self.assertRaises(browser.ArtifactError):
                browser.cleanup_session(session)
        self.assertEqual(
            (session.path / "user-data.txt").read_bytes(),
            b"unrelated replacement data",
        )
        self.assertTrue(retained.exists())
        self.assertEqual(list(retained.iterdir()), [])

    def test_cleanup_directory_swap_preserves_empty_replacement(self):
        session = self.running_session()
        retained = session.path.with_name("retained-original")
        original_remove = browser._remove_contents
        swapped = False

        def replace_before_recursion(descriptor):
            nonlocal swapped
            if not swapped:
                swapped = True
                session.path.rename(retained)
                session.path.mkdir(mode=0o700)
            return original_remove(descriptor)

        with mock.patch.object(
            browser, "_remove_contents", side_effect=replace_before_recursion
        ):
            with self.assertRaises(browser.ArtifactError):
                browser.cleanup_session(session)
        self.assertTrue(session.path.is_dir())
        self.assertEqual(list(session.path.iterdir()), [])
        self.assertEqual(list(retained.iterdir()), [])

    def test_nested_cleanup_swap_preserves_replacement_data(self):
        session = self.running_session()
        output = session.path / "output"
        output_inode = output.stat().st_ino
        retained = session.path / "retained-output"
        original_remove = browser._remove_contents
        swapped = False

        def replace_before_recursion(descriptor):
            nonlocal swapped
            if os.fstat(descriptor).st_ino == output_inode and not swapped:
                swapped = True
                output.rename(retained)
                output.mkdir(mode=0o700)
                (output / "user-data.txt").write_bytes(b"unrelated nested data")
            return original_remove(descriptor)

        with mock.patch.object(
            browser, "_remove_contents", side_effect=replace_before_recursion
        ):
            with self.assertRaises(browser.ArtifactError):
                browser.cleanup_session(session)
        self.assertEqual(
            (output / "user-data.txt").read_bytes(), b"unrelated nested data"
        )
        self.assertEqual(list(retained.iterdir()), [])

    def test_dead_supervisor_and_dead_child_are_swept(self):
        session = self.running_session()
        self.sweep({})
        self.assertFalse(session.path.exists())

    def test_reused_pid_with_different_birth_tick_is_swept(self):
        session = self.running_session()
        self.sweep({500001: 101, 500002: 201})
        self.assertFalse(session.path.exists())

    def test_live_supervisor_preserves_session(self):
        session = self.running_session()
        self.sweep({500001: 100})
        self.assertTrue((session.path / "output" / "report.csv").exists())

    def test_dead_supervisor_with_live_child_preserves_session(self):
        session = self.running_session()
        self.sweep({500002: 200})
        self.assertTrue((session.path / "output" / "report.csv").exists())

    def test_launching_unknown_and_nonprivate_sessions_are_never_swept(self):
        for invalid in (
            "launching",
            "missing-field",
            "wrong-id",
            "nonprivate",
            "invalid-json",
        ):
            with self.subTest(invalid=invalid):
                session = self.running_session()
                metadata = session.path / "session.json"
                if invalid == "launching":
                    session.metadata["state"] = "launching"
                elif invalid == "missing-field":
                    del session.metadata["child_birth_tick"]
                elif invalid == "wrong-id":
                    session.metadata["session_id"] = "session-other"
                elif invalid == "nonprivate":
                    session.path.chmod(0o755)
                metadata.write_text(
                    "not json"
                    if invalid == "invalid-json"
                    else json.dumps(session.metadata)
                )
                self.sweep({})
                self.assertTrue((session.path / "output" / "report.csv").exists())

    def test_symlink_session_and_symlink_metadata_are_preserved(self):
        session = self.running_session()
        metadata = session.path / "session.json"
        original = session.path / "retained.json"
        metadata.rename(original)
        metadata.symlink_to(original)
        linked = session.path.with_name("session-" + "b" * 32)
        linked.symlink_to(session.path, target_is_directory=True)
        self.sweep({})
        self.assertTrue(metadata.is_symlink())
        self.assertTrue(linked.is_symlink())
        self.assertTrue(original.exists())

    def test_unverifiable_process_owner_is_preserved(self):
        session = self.running_session()
        with mock.patch.object(
            browser, "process_birth_tick", side_effect=PermissionError()
        ):
            with browser._artifact_base(self.repository) as base:
                browser._sweep_orphans(base)
        self.assertTrue(session.path.exists())

    def test_startup_sweeps_dead_session_and_preserves_other_output(self):
        dead = self.running_session()
        unrelated = dead.path.parent / "user-report.csv"
        unrelated.write_bytes(b"keep")
        real_birth_tick = browser.process_birth_tick
        with mock.patch.object(
            browser,
            "process_birth_tick",
            side_effect=lambda pid: (
                real_birth_tick(pid) if pid == os.getpid() else None
            ),
        ):
            fresh = browser.create_session(self.repository)
        self.assertFalse(dead.path.exists())
        self.assertTrue(fresh.path.exists())
        self.assertEqual(unrelated.read_bytes(), b"keep")

    def test_process_birth_tick_handles_parentheses_in_process_name(self):
        value = "123 (name ) with parentheses) " + " ".join(
            ["S"] + ["0"] * 18 + ["6789"]
        )
        with mock.patch.object(Path, "read_text", return_value=value):
            self.assertEqual(browser.process_birth_tick(123), 6789)
        with mock.patch.object(Path, "read_text", side_effect=FileNotFoundError()):
            self.assertIsNone(browser.process_birth_tick(123))
        with mock.patch.object(
            Path, "read_text", return_value=value.replace(") S ", ") Z ")
        ):
            self.assertIsNone(browser.process_birth_tick(123))

    def test_orphan_sweep_preserves_live_guardian_then_recovers_dead_guardian(self):
        session = self.running_session()
        browser._update_metadata(session, guardian_pid=500003, guardian_birth_tick=300)
        self.sweep({500003: 300})
        self.assertTrue(session.path.exists())
        self.sweep({})
        self.assertFalse(session.path.exists())

    def test_orphan_sweep_rejects_partial_and_malformed_guardian_identity(self):
        for updates in (
            {"guardian_pid": 500003},
            {"guardian_pid": 500003, "guardian_birth_tick": True},
            {"guardian_pid": 0, "guardian_birth_tick": 300},
            {"guardian_pid": 500003, "guardian_birth_tick": 300, "unknown": "value"},
        ):
            with self.subTest(updates=updates):
                session = self.running_session()
                browser._update_metadata(session, **updates)
                self.sweep({})
                self.assertTrue(session.path.exists())

    def test_guardian_expires_only_own_output_after_supervisor_death_then_exits(self):
        session = self.running_session()
        with (
            mock.patch.object(
                browser, "process_birth_tick", side_effect=[None, 200, None, None]
            ),
            mock.patch.object(browser.time, "monotonic", side_effect=[0, 31, 31]),
            mock.patch.object(browser.time, "sleep") as sleep,
            mock.patch.object(browser, "expire_output") as expire,
            mock.patch.object(browser, "cleanup_session") as cleanup,
        ):
            browser._guard_session(session)
        expire.assert_called_once()
        self.assertEqual(expire.call_args.args[0], session)
        sleep.assert_called_once_with(1)
        cleanup.assert_called_once_with(session)

    def test_idle_expiry_removes_only_old_owned_files_and_preserves_metadata(self):
        session = self.running_session()
        now = time.time()
        old = session.path / "output" / "report.csv"
        recent = session.path / "output" / "recent.png"
        recent.write_bytes(b"fresh evidence")
        nested = session.path / "output" / "nested"
        nested.mkdir(mode=0o700)
        nested_old = nested / "old.png"
        nested_old.write_bytes(b"expired")
        metadata = session.path / "session.json"
        for path in (old, nested_old, metadata):
            os.utime(path, (now - 601, now - 601))
        browser.expire_output(session, now)
        self.assertFalse(old.exists())
        self.assertFalse(nested_old.exists())
        self.assertEqual(recent.read_bytes(), b"fresh evidence")
        self.assertTrue(metadata.exists())
        self.assertTrue(nested.exists())

    def test_idle_expiry_never_follows_symlink_files_or_directories(self):
        session = self.running_session()
        outside = self.repository / "reports"
        outside.mkdir()
        report = outside / "old.csv"
        report.write_bytes(b"preserve report")
        now = time.time()
        os.utime(report, (now - 601, now - 601))
        file_link = session.path / "output" / "file-link"
        directory_link = session.path / "output" / "directory-link"
        file_link.symlink_to(report)
        directory_link.symlink_to(outside, target_is_directory=True)
        browser.expire_output(session, now)
        self.assertTrue(file_link.is_symlink())
        self.assertTrue(directory_link.is_symlink())
        self.assertEqual(report.read_bytes(), b"preserve report")

    def test_idle_expiry_preserves_unknown_directory_and_other_session(self):
        first = browser.create_session(self.repository)
        second = browser.create_session(self.repository)
        now = time.time()
        other_output = second.path / "output" / "other.csv"
        other_output.write_bytes(b"other session")
        unknown = first.path / "output" / "unknown"
        unknown.mkdir(mode=0o755)
        unknown_output = unknown / "old.csv"
        unknown_output.write_bytes(b"unknown ownership boundary")
        for path in (other_output, unknown_output):
            os.utime(path, (now - 601, now - 601))
        browser.expire_output(first, now)
        self.assertTrue(other_output.exists())
        self.assertTrue(unknown_output.exists())


class GuardianProcessTests(ArtifactFixture):
    def wait_for(self, condition, message):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if condition():
                return
            time.sleep(0.02)
        self.fail(message)

    def launch_stub(self, guardian=True, ignore_shutdown=False):
        child = (
            "from pathlib import Path; import sys, signal; "
            + (
                "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                "signal.signal(signal.SIGINT, signal.SIG_IGN); "
                if ignore_shutdown
                else ""
            )
            + "(Path(sys.argv[1]) / 'temporary.dat').write_bytes(b'owned temporary output'); "
            "sys.stdin.buffer.read()"
        )
        script = f"""
import importlib.util
from pathlib import Path
import json, os, sys
spec = importlib.util.spec_from_file_location('stub_browser', {str(Path(browser.__file__))!r})
browser = importlib.util.module_from_spec(spec)
spec.loader.exec_module(browser)
browser.REPOSITORY_ROOT = Path(sys.argv[1])
browser.shutil.which = lambda name: sys.executable
browser.command = lambda npx, chromium, output: [sys.executable, '-c', {child!r}, str(output)]
# Exercise real process ownership and escalation without spending the production
# grace period in every default test run. The requested grace is still asserted.
original_wait = browser.subprocess.Popen.wait
def observed_wait(process, timeout=None):
    if timeout != 5:
        return original_wait(process, timeout=timeout)
    evidence = {{'pid': process.pid, 'requested_timeout': timeout, 'timed_out': False}}
    try:
        return original_wait(process, timeout=0.05)
    except browser.subprocess.TimeoutExpired:
        evidence['timed_out'] = True
        raise
    finally:
        receipt = browser.REPOSITORY_ROOT / 'shutdown-grace.json'
        attempts = json.loads(receipt.read_text()) if receipt.exists() else []
        attempts.append(evidence)
        receipt.write_text(json.dumps(attempts))
browser.subprocess.Popen.wait = observed_wait

original_guard = browser._guard_session
def observed_guard(session):
    original_sleep = browser.time.sleep
    original_birth_tick = browser.process_birth_tick
    observed_owners = {{}}
    def observed_birth_tick(pid):
        tick = original_birth_tick(pid)
        if pid in (session.metadata['pid'], session.metadata['child_pid']):
            observed_owners[pid] = tick
        return tick
    browser.process_birth_tick = observed_birth_tick
    def observed_poll(seconds):
        if seconds != 1:
            raise AssertionError('Production guardian polling policy changed')
        if (observed_owners.get(session.metadata['pid'], -1) is None
            and observed_owners.get(session.metadata['child_pid'])
                == session.metadata['child_birth_tick']):
            # This acknowledgment occurs after the actual guardian's ownership
            # decision, while the captured stdio child still holds its context.
            observed = browser.REPOSITORY_ROOT / 'guardian-owner-observed.json'
            if not observed.exists():
                temporary = observed.with_suffix('.tmp')
                temporary.write_text(json.dumps({{
                    'guardian_pid': os.getpid(),
                    'child_pid': session.metadata['child_pid'],
                    'child_birth_tick': session.metadata['child_birth_tick']}}))
                temporary.replace(observed)
        observed_owners.clear()
        original_sleep(0.02)
    browser.time.sleep = observed_poll
    original_guard(session)
browser._guard_session = observed_guard
if not {guardian!r}:
    browser._start_guardian = lambda session: None
raise SystemExit(browser.main([]))
"""
        process = subprocess.Popen(
            [sys.executable, "-c", script, str(self.repository)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        captured = {}

        def stop_owned_processes():
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
            metadata = captured.get("metadata", {})
            for pid_key, tick_key in (
                ("child_pid", "child_birth_tick"),
                ("guardian_pid", "guardian_birth_tick"),
            ):
                pid = metadata.get(pid_key)
                if pid and browser.process_birth_tick(pid) == metadata.get(tick_key):
                    os.kill(pid, browser.signal.SIGKILL)
            for handle in (process.stdin, process.stdout, process.stderr):
                handle.close()

        self.addCleanup(stop_owned_processes)

        def ready():
            for path in (self.repository / "artifacts" / "native-browsers").glob(
                "session-*"
            ):
                try:
                    metadata = json.loads((path / "session.json").read_text())
                except (OSError, ValueError):
                    continue
                if (
                    metadata.get("pid") == process.pid
                    and metadata.get("state") == "running"
                ):
                    if guardian and "guardian_pid" not in metadata:
                        continue
                    if (path / "output" / "temporary.dat").exists():
                        captured.update(path=path, metadata=metadata)
                        return True
            return False

        self.wait_for(ready, "Disposable stdio launcher did not become ready")
        return process, captured["path"], captured["metadata"]

    def test_forced_launcher_death_reproduces_finalizer_gap_without_guardian(self):
        process, path, metadata = self.launch_stub(guardian=False)
        os.killpg(process.pid, browser.signal.SIGKILL)
        process.wait(timeout=5)
        process.stdin.close()
        self.wait_for(
            lambda: browser.process_birth_tick(metadata["child_pid"]) is None,
            "Disposable server did not exit after stdin closed",
        )
        self.assertTrue((path / "output" / "temporary.dat").exists())

    def test_forced_launcher_death_cleans_only_owned_output_and_guardian_exits(self):
        peer = browser.create_session(self.repository)
        peer_output = peer.path / "output" / "peer.dat"
        peer_output.write_bytes(b"live peer output")
        process, path, metadata = self.launch_stub()
        guardian = metadata["guardian_pid"]
        self.assertNotEqual(os.getpgid(guardian), os.getpgid(process.pid))
        self.assertEqual(os.getsid(guardian), guardian)
        descriptors = Path(f"/proc/{guardian}/fd")
        for name in ("0", "1", "2"):
            self.assertEqual(os.readlink(descriptors / name), os.devnull)
        for descriptor in descriptors.iterdir():
            try:
                target = os.readlink(descriptor)
            except FileNotFoundError:
                continue
            self.assertFalse(target.startswith(("pipe:", "socket:")))
        os.killpg(process.pid, browser.signal.SIGKILL)
        process.wait(timeout=5)
        # The backend's private pipe closes with its relay owner even while the
        # caller's upstream transport remains open; no inherited descriptor leaks.
        self.assertFalse(process.stdin.closed)
        self.wait_for(
            lambda: not path.exists(), "Guardian did not remove ended connection output"
        )
        self.wait_for(
            lambda: browser.process_birth_tick(guardian) is None,
            "Connection guardian did not exit after cleanup",
        )
        self.assertEqual(peer_output.read_bytes(), b"live peer output")
        self.assertEqual(process.stdout.read(), b"")
        self.assertEqual(process.stderr.read(), b"")

    def test_normal_disconnect_cleans_output_and_reaps_guardian(self):
        process, path, metadata = self.launch_stub()
        process.stdin.close()
        self.assertEqual(process.wait(timeout=5), 0)
        self.assertFalse(path.exists())
        self.assertIsNone(browser.process_birth_tick(metadata["guardian_pid"]))
        self.assertFalse(Path(f"/proc/{metadata['guardian_pid']}").exists())
        self.assertEqual(process.stdout.read(), b"")
        self.assertEqual(process.stderr.read(), b"")

    def test_termination_escalates_ignoring_child_and_automatically_cleans_output(self):
        peer = browser.create_session(self.repository)
        peer_output = peer.path / "output" / "peer.dat"
        peer_output.write_bytes(b"live peer output")
        process, path, metadata = self.launch_stub(ignore_shutdown=True)
        started = time.monotonic()
        process.terminate()
        self.assertEqual(process.wait(timeout=7), 143)
        self.assertLess(time.monotonic() - started, 7)
        self.assertEqual(
            json.loads((self.repository / "shutdown-grace.json").read_text())[0],
            {
                "pid": metadata["child_pid"],
                "requested_timeout": 5,
                "timed_out": True,
            },
        )
        self.assertFalse(path.exists())
        self.assertIsNone(browser.process_birth_tick(metadata["child_pid"]))
        self.assertFalse(Path(f"/proc/{metadata['guardian_pid']}").exists())
        self.assertEqual(peer_output.read_bytes(), b"live peer output")
        self.assertEqual(process.stdout.read(), b"")
        self.assertEqual(process.stderr.read(), b"")

    def test_guardian_startup_metadata_failure_reaps_its_fork_child(self):
        session = browser.create_session(self.repository)
        browser._record_child(session, os.getpid())
        children = []
        original_fork = os.fork

        def tracked_fork():
            pid = original_fork()
            if pid > 0:
                children.append(pid)
            return pid

        with (
            mock.patch.object(os, "fork", side_effect=tracked_fork),
            mock.patch.object(
                browser, "_update_metadata", side_effect=OSError("setup failure")
            ),
        ):
            with self.assertRaises(OSError):
                browser._start_guardian(session)
        self.assertEqual(len(children), 1)
        self.assertIsNone(browser.process_birth_tick(children[0]))
        with self.assertRaises(ChildProcessError):
            os.waitpid(children[0], os.WNOHANG)


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.repository = Path(self.temporary.name)
        self.stdout = io.StringIO()
        self.stderr = io.StringIO()
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(browser, "REPOSITORY_ROOT", self.repository).start()
        self.which = mock.patch.object(
            browser.shutil, "which", side_effect=lambda name: f"/usr/bin/{name}"
        ).start()
        self.popen = mock.patch.object(browser.subprocess, "Popen").start()
        self.process = self.popen.return_value
        # A Popen fake must never borrow pytest's real session/group identity.
        # Launcher controls use synthetic ownership; real group lifecycle belongs
        # to the separate faithful subprocess regressions.
        self.process.pid = 2**30
        real_birth_tick = browser.process_birth_tick
        mock.patch.object(
            browser,
            "process_birth_tick",
            side_effect=lambda pid: (
                123456 if pid == self.process.pid else real_birth_tick(pid)
            ),
        ).start()
        mock.patch.object(browser.os, "getsid", return_value=2**30 + 1).start()
        mock.patch.object(browser.os, "getpgid", return_value=2**30 + 2).start()
        self.process.wait.return_value = 0
        self.process.poll.return_value = 0
        self.umask = mock.patch.object(browser.os, "umask").start()
        self.signals = mock.patch.object(browser.signal, "signal").start()
        self.killpg = mock.patch.object(browser.os, "killpg").start()
        self.guardian = mock.patch.object(
            browser, "_start_guardian", return_value=12346
        ).start()
        self.stop_guardian = mock.patch.object(browser, "_stop_guardian").start()
        self.relay = mock.patch.object(
            browser, "relay", side_effect=self.wait_transport
        ).start()

    def wait_transport(self, backend):
        # Ownership/launcher controls mock only the transport seam. Real relay
        # framing, callbacks and lifecycle are covered by subprocess tests below.
        while True:
            try:
                return backend.process.wait(timeout=browser.OUTPUT_SWEEP_SECONDS)
            except subprocess.TimeoutExpired:
                browser.expire_output(backend.session, time.time())

    def launch(self, args=None):
        with (
            contextlib.redirect_stdout(self.stdout),
            contextlib.redirect_stderr(self.stderr),
        ):
            return browser.main([] if args is None else args)

    def output(self):
        return Path(self.popen.call_args.args[0][-1])

    def test_pinned_isolated_stdio_command_private_umask_and_cleanup_after_exit(self):
        def exit_process(timeout):
            self.assertEqual(timeout, 30)
            output = self.output()
            self.assertTrue(output.is_dir())
            metadata = json.loads((output.parent / "session.json").read_text())
            self.assertEqual(metadata["state"], "running")
            self.assertEqual(metadata["child_pid"], self.process.pid)
            self.assertEqual(
                metadata["child_birth_tick"],
                browser.process_birth_tick(self.process.pid),
            )
            self.assertFalse((output / "session.json").exists())
            (output / "download.csv").write_bytes(b"temporary")
            return 0

        self.process.wait.side_effect = exit_process
        self.assertEqual(self.launch(), 0)
        self.umask.assert_called_once_with(0o077)
        arguments = self.popen.call_args.args[0]
        self.assertEqual(
            arguments[:-1],
            [
                "/usr/bin/npx",
                "--yes",
                "--offline",
                "@playwright/mcp@0.0.83",
                "--isolated",
                "--headless",
                "--browser",
                "chromium",
                "--executable-path",
                "/usr/bin/chromium",
                "--ignore-https-errors",
                "--caps=vision",
                "--no-webmcp",
                "--output-max-size",
                "20971520",
                "--output-dir",
            ],
        )
        self.assertEqual(
            self.popen.call_args.kwargs,
            {
                "env": mock.ANY,
                "stdin": subprocess.PIPE,
                "stdout": subprocess.PIPE,
                "stderr": None,
                "start_new_session": True,
            },
        )
        self.assertEqual(
            self.output().parent.parent,
            self.repository / "artifacts" / "native-browsers",
        )
        self.assertFalse(self.output().parent.exists())
        self.assertEqual(self.stdout.getvalue(), "")
        self.assertEqual(self.stderr.getvalue(), "")
        self.assertEqual(
            {call.args[0] for call in self.signals.call_args_list},
            {browser.signal.SIGINT, browser.signal.SIGTERM},
        )
        self.guardian.assert_called_once()
        self.stop_guardian.assert_called_once_with(12346)

    def test_ambient_playwright_overrides_are_removed_only_from_server_environment(
        self,
    ):
        unrelated = {
            "PATH": "/synthetic/tools",
            "UNRELATED_SETTING": "preserve",
            "PLAYWRIGHT_LANGUAGE": "preserve",
            "PLAYWRIGHT_MCP": "preserve-exact-prefix-without-underscore",
        }
        ambient = {
            **unrelated,
            "PLAYWRIGHT_MCP_CDP_ENDPOINT": "http://example.invalid:9222",
            "PLAYWRIGHT_MCP_CONFIG": "/shared/config.json",
            "PLAYWRIGHT_MCP_SHARED_BROWSER_CONTEXT": "true",
            "PLAYWRIGHT_MCP_USER_DATA_DIR": "/shared/profile",
            "PLAYWRIGHT_MCP_BROWSER": "firefox",
            "PLAYWRIGHT_MCP_FUTURE_OPTION": "uncontrolled",
        }
        with mock.patch.dict(os.environ, ambient, clear=True):
            self.assertEqual(self.launch(), 0)
            self.assertEqual(self.popen.call_args.kwargs.get("env"), unrelated)
            self.assertEqual(dict(os.environ), ambient)
        self.assertEqual(self.stdout.getvalue(), "")
        self.assertEqual(self.stderr.getvalue(), "")

    def test_missing_tools_fail_without_creating_artifacts(self):
        for missing in ("npx", "node", "chromium"):
            with self.subTest(missing=missing):
                self.which.side_effect = lambda name: (
                    None if name == missing else f"/usr/bin/{name}"
                )
                self.assertEqual(self.launch(), 2)
                self.popen.assert_not_called()
                self.assertFalse((self.repository / "artifacts").exists())
        self.assertEqual(self.stdout.getvalue(), "")
        self.assertIn("Node.js, npx and Chromium", self.stderr.getvalue())

    def test_setup_failure_has_safe_stderr_and_no_process_launch(self):
        with mock.patch.object(
            browser, "create_session", side_effect=PermissionError("secret-token-value")
        ):
            self.assertEqual(self.launch(), 2)
        self.popen.assert_not_called()
        self.assertEqual(self.stdout.getvalue(), "")
        self.assertIn("setup failed", self.stderr.getvalue())
        self.assertNotIn("secret-token-value", self.stderr.getvalue())

    def test_process_start_failure_cleans_own_artifacts_with_safe_stderr(self):
        self.popen.side_effect = OSError("secret-token-value")
        self.assertEqual(self.launch(), 2)
        self.assertFalse(self.output().parent.exists())
        self.assertEqual(self.stdout.getvalue(), "")
        self.assertIn("setup failed", self.stderr.getvalue())
        self.assertNotIn("secret-token-value", self.stderr.getvalue())

    def test_child_nonzero_exit_is_reported_and_output_is_cleaned(self):
        for status in (1, -15):
            with self.subTest(status=status):
                self.process.wait.return_value = status
                self.assertEqual(self.launch(), 143 if status < 0 else status)
                self.assertFalse(self.output().parent.exists())
        self.assertEqual(self.stdout.getvalue(), "")

    def test_signal_forwarding_targets_only_captured_live_child_group(self):
        self.process.pid = 12345
        self.process.poll.return_value = None
        browser._signal_child(self.process, browser.signal.SIGTERM)
        self.killpg.assert_called_once_with(12345, browser.signal.SIGTERM)
        self.killpg.reset_mock()
        self.process.poll.return_value = 0
        browser._signal_child(self.process, browser.signal.SIGINT)
        self.killpg.assert_not_called()

    def test_failed_startup_metadata_stops_owned_process_before_cleanup(self):
        self.process.poll.side_effect = [None, None]
        with mock.patch.object(
            browser, "_record_child", side_effect=OSError("secret-token-value")
        ):
            self.assertEqual(self.launch(), 2)
        self.killpg.assert_called_once_with(self.process.pid, browser.signal.SIGTERM)
        self.process.wait.assert_called_once_with(timeout=5)
        self.assertFalse(self.output().parent.exists())
        self.assertNotIn("secret-token-value", self.stderr.getvalue())

    def test_cleanup_failure_preserves_unsafe_directory_and_reports_failure(self):
        def exit_process(timeout):
            self.output().parent.chmod(0o755)
            return 0

        self.process.wait.side_effect = exit_process
        self.assertEqual(self.launch(), 2)
        self.assertTrue(self.output().parent.exists())
        self.assertIn("could not remove", self.stderr.getvalue())
        self.assertEqual(self.stdout.getvalue(), "")

    def test_idle_wait_expires_output_then_cleans_after_transport_exit(self):
        self.process.wait.side_effect = [
            browser.subprocess.TimeoutExpired("npx", 30),
            0,
        ]
        with mock.patch.object(browser, "expire_output") as expire:
            self.assertEqual(self.launch(), 0)
        self.assertEqual(expire.call_count, 1)
        self.assertEqual(expire.call_args.args[0].path / "output", self.output())
        self.assertFalse(self.output().parent.exists())

    def test_shutdown_signal_enters_bounded_finalizer_with_preserved_exit_status(self):
        for signum in (browser.signal.SIGINT, browser.signal.SIGTERM):
            with self.subTest(signum=signum):
                self.process.wait.side_effect = browser.ShutdownRequested(signum)
                with mock.patch.object(browser, "_stop_child") as stop:
                    self.assertEqual(self.launch(), 128 + signum)
                stop.assert_called_once_with(self.process)
                self.assertFalse(self.output().parent.exists())

    def test_agent_supplied_options_and_commands_are_rejected(self):
        for arguments in (
            ["--user-data-dir", "/shared/profile"],
            ["--cdp-endpoint", "http://localhost:9222"],
            ["--port", "9000"],
            ["--headed"],
            ["$(touch should-never-run)"],
        ):
            with self.subTest(arguments=arguments):
                self.assertEqual(self.launch(arguments), 2)
                self.popen.assert_not_called()
                self.which.assert_not_called()
                self.assertFalse((self.repository / "artifacts").exists())
        self.assertEqual(self.stdout.getvalue(), "")
        self.assertIn("accepts no arguments", self.stderr.getvalue())


class RelayProcessTests(ArtifactFixture):
    """Use real stdio/process ownership; the backend speaks the pinned MCP framing."""

    def launch(self, roots=False):
        backend_code = r"""
import json, os, signal, subprocess, sys
from pathlib import Path
output = Path(sys.argv[1])
pending = {}
client_initialized = False
def send(message):
    print(json.dumps(message, separators=(',', ':')), flush=True)
def result(identity, payload):
    send({'jsonrpc':'2.0','id':identity,'result':payload})
def initialized(identity):
    result(identity, {'protocolVersion':'2024-11-05',
        'capabilities':{'tools':{}},'serverInfo':{'name':'owned-fake','version':'1'}})
for raw in sys.stdin.buffer:
    message = json.loads(raw)
    method = message.get('method')
    if method == 'initialize':
        if 'roots' in message['params'].get('capabilities', {}):
            pending[7] = ('initialize', message['id'])
            send({'jsonrpc':'2.0','id':7,'method':'roots/list'})
        elif (output.parents[3] / 'incompatible').exists():
            result(message['id'], {'protocolVersion':'wrong','capabilities':{}})
        else:
            initialized(message['id'])
    elif method == 'notifications/initialized':
        client_initialized = True
    elif method == 'ping':
        if message['id'] == 'launch-ready':
            (output / 'launch-ready.json').write_text(json.dumps({'initialized':client_initialized}))
        result(message['id'], {})
    elif method == 'tools/list':
        result(message['id'], {'tools':[{'name':'browser_close','inputSchema':{'type':'object'}}]})
    elif method == 'tools/call':
        name = message['params']['name']
        (output / 'calls.jsonl').open('a').write(json.dumps({'id':message['id'],'name':name})+'\n')
        args = message['params'].get('arguments', {})
        if name == 'hold':
            pending[8] = ('hold', message['id'])
            send({'jsonrpc':'2.0','id':8,'method':'roots/list'})
        elif name == 'crash':
            raise SystemExit(9)
        elif name == 'browser_close' and args.get('mode') == 'late':
            pending[9] = ('late', message['id'])
            send({'jsonrpc':'2.0','method':'notifications/message','params':{'level':'info','data':'close pending'}})
        elif name == 'browser_close' and args.get('mode') == 'rpc-error':
            send({'jsonrpc':'2.0','id':message['id'],'error':{'code':-32603,'message':'original failure'}})
        elif name == 'browser_close' and args.get('mode') in ('tool-error', 'malformed'):
            result(message['id'], {'content':[], 'isError':True if args['mode']=='tool-error' else 1})
        else:
            if name == 'descendant':
                ready = output / 'descendant.ready'
                child = subprocess.Popen([sys.executable,'-c',
                    'import signal,time,sys;from pathlib import Path;signal.signal(signal.SIGTERM,signal.SIG_IGN);Path(sys.argv[1]).touch();time.sleep(60)', str(ready)])
                import time
                while not ready.exists():
                    time.sleep(.01)
                (output / 'descendant.pid').write_text(str(child.pid))
            if name == 'browser_close' and args.get('mode') == 'unsafe':
                output.parent.chmod(0o755)
            result(message['id'], {'content':[{'type':'image','data':'AAEC','mimeType':'image/png'}]})
    elif method == 'notifications/cancelled' and 9 in pending:
        _, identity = pending.pop(9)
        result(identity, {'content':[]})
    elif method is None:
        kind, identity = pending.pop(message['id'])
        if kind == 'initialize':
            initialized(identity)
        else:
            send({'jsonrpc':'2.0','method':'notifications/message','params':{'level':'info','data':'owned notification'}})
            result(identity, {'content':[]})
"""
        script = f"""
import importlib.util, sys
from pathlib import Path
spec=importlib.util.spec_from_file_location('owned_browser',{str(Path(browser.__file__))!r})
browser=importlib.util.module_from_spec(spec);spec.loader.exec_module(browser)
browser.REPOSITORY_ROOT=Path(sys.argv[1])
browser.shutil.which=lambda name:sys.executable
browser.command=lambda npx,chromium,output:[sys.executable,'-u','-c',{backend_code!r},str(output)]
raise SystemExit(browser.main([]))
"""
        process = subprocess.Popen(
            [sys.executable, "-u", "-c", script, str(self.repository)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        self.process = process
        self.frames = browser.Frames()
        self.messages = []
        self.addCleanup(self.stop, process)
        self.send(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"roots": {}} if roots else {},
                    "clientInfo": {"name": "focused-test", "version": "1"},
                },
            }
        )
        if roots:
            callback = self.receive(
                lambda message: message.get("method") == "roots/list"
            )
            self.send({"jsonrpc": "2.0", "id": callback["id"], "result": {"roots": []}})
        self.receive(lambda message: message.get("id") == 1)
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        # A sent notification may still be queued when release arrives. Its
        # following backend ping response proves the initialization traffic drained.
        self.send({"jsonrpc": "2.0", "id": "launch-ready", "method": "ping"})
        ready = self.receive(lambda message: message.get("id") == "launch-ready")
        self.assertEqual(ready["result"], {})
        return self.session()

    def stop(self, process):
        if process.poll() is None:
            process.stdin.close()
            try:
                process.wait(timeout=7)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        for handle in (process.stdin, process.stdout, process.stderr):
            if not handle.closed:
                handle.close()

    def send(self, message):
        self.process.stdin.write(browser.Relay.encoded(message))
        self.process.stdin.flush()

    def call(self, identity, name, arguments=None):
        self.send(
            {
                "jsonrpc": "2.0",
                "id": identity,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments or {}},
            }
        )

    def receive(self, predicate, timeout=5):
        deadline = time.monotonic() + timeout
        with selectors.DefaultSelector() as reader:
            reader.register(self.process.stdout, selectors.EVENT_READ)
            while time.monotonic() < deadline:
                for index, message in enumerate(self.messages):
                    if predicate(message):
                        return self.messages.pop(index)
                if not reader.select(timeout=0.05):
                    continue
                data = os.read(self.process.stdout.fileno(), 65536)
                if not data:
                    self.fail(self.process.stderr.read().decode())
                self.messages.extend(
                    message for message, _ in self.frames.receive(data)
                )
        self.fail("Owned relay response did not arrive")

    def session(self):
        paths = list((self.repository / "artifacts/native-browsers").glob("session-*"))
        self.assertEqual(len(paths), 1)
        return paths[0], json.loads((paths[0] / "session.json").read_text())

    def released(self, owned):
        path, metadata = owned
        self.assertFalse(path.exists())
        for key in ("child", "guardian"):
            self.assertIsNone(browser.process_birth_tick(metadata[f"{key}_pid"]))
        self.assertIsNone(self.process.poll(), "The native endpoint must remain alive")

    def test_launch_observes_backend_initialized_notification_before_returning(self):
        owned = self.launch(roots=True)
        self.assertEqual(
            json.loads((owned[0] / "output/launch-ready.json").read_text()),
            {"initialized": True},
        )
        self.assertFalse((owned[0] / "output/calls.jsonl").exists())

    def test_close_releases_generation_inline_images_then_same_endpoint_reinitializes(
        self,
    ):
        owned = self.launch()
        peer = browser.create_session(self.repository)
        marker = peer.path / "output/peer.dat"
        marker.write_text("live peer")
        self.call("7", "navigate")
        image = self.receive(lambda message: message.get("id") == "7")
        self.assertEqual(image["result"]["content"][0]["data"], "AAEC")
        exported = self.repository / "selected-evidence.json"
        exported.write_text(json.dumps(image))
        self.call(7, "browser_close")
        self.receive(lambda message: message.get("id") == 7)
        self.assertFalse(owned[0].exists())
        self.assertEqual(exported.read_text(), json.dumps(image))
        self.assertEqual(marker.read_text(), "live peer")
        self.send({"jsonrpc": "2.0", "id": "ping", "method": "ping"})
        self.assertEqual(
            self.receive(lambda message: message.get("id") == "ping")["result"], {}
        )
        self.assertFalse(owned[0].exists())
        self.call(8, "navigate")
        self.receive(lambda message: message.get("id") == 8)
        paths = list((self.repository / "artifacts/native-browsers").glob("session-*"))
        paths.remove(peer.path)
        self.assertEqual(len(paths), 1)
        self.assertNotEqual(paths[0], owned[0])
        self.call(9, "browser_close")
        self.receive(lambda message: message.get("id") == 9)
        self.assertFalse(paths[0].exists())

    def test_release_inventory_and_active_owned_transport_exit(self):
        owned = self.launch()
        self.send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        tools = self.receive(lambda message: message.get("id") == 2)["result"]["tools"]
        self.assertEqual(
            [tool["name"] for tool in tools], ["browser_close", "browser_release"]
        )
        self.assertEqual(
            tools[1]["inputSchema"],
            {"type": "object", "properties": {}, "additionalProperties": False},
        )
        peer = browser.create_session(self.repository)
        marker = peer.path / "output/peer.txt"
        marker.write_text("live peer")
        self.addCleanup(browser.cleanup_session, peer)
        self.call("release", "browser_release")
        response = self.receive(lambda message: message.get("id") == "release")
        self.assertIs(response["result"]["isError"], False)
        self.assertEqual(self.process.wait(timeout=5), 0)
        self.assertFalse(owned[0].exists())
        for key in ("pid", "child_pid", "guardian_pid"):
            self.assertIsNone(browser.process_birth_tick(owned[1][key]))
        self.assertEqual(self.process.stdout.read(), b"")
        self.assertEqual(marker.read_text(), "live peer")

    def test_release_dormant_endpoint_without_backend_reallocation(self):
        owned = self.launch()
        self.call(2, "browser_close")
        self.receive(lambda message: message.get("id") == 2)
        self.released(owned)
        self.call(3, "browser_release")
        self.assertIs(
            self.receive(lambda message: message.get("id") == 3)["result"]["isError"],
            False,
        )
        self.assertEqual(self.process.wait(timeout=5), 0)
        self.assertEqual(list(owned[0].parent.glob("session-*")), [])

    def test_release_busy_callback_rejects_without_cancelling_or_replaying_work(self):
        owned = self.launch(roots=True)
        self.call(2, "hold")
        callback = self.receive(lambda message: message.get("method") == "roots/list")
        self.call(3, "browser_release")
        self.assertIn(
            "busy",
            self.receive(lambda message: message.get("id") == 3)["error"]["message"],
        )
        self.assertTrue(owned[0].exists())
        self.assertIsNone(self.process.poll())
        self.send({"jsonrpc": "2.0", "id": callback["id"], "result": {"roots": []}})
        self.receive(lambda message: message.get("id") == 2)
        calls = [
            json.loads(line)
            for line in (owned[0] / "output/calls.jsonl").read_text().splitlines()
        ]
        self.assertEqual(calls, [{"id": 2, "name": "hold"}])
        self.call(4, "browser_release")
        self.receive(lambda message: message.get("id") == 4)
        self.assertEqual(self.process.wait(timeout=5), 0)

    def test_release_cleanup_failure_is_one_error_without_false_success(self):
        owned = self.launch()
        owned[0].chmod(0o755)
        self.call(2, "browser_release")
        response = self.receive(lambda message: message.get("id") == 2)
        self.assertIn("could not be released", response["error"]["message"])
        self.assertEqual(self.process.wait(timeout=5), 2)
        self.assertEqual(self.process.stdout.read(), b"")
        self.assertTrue(owned[0].exists())

    def test_close_barrier_drains_callbacks_and_never_kills_queued_navigation(self):
        owned = self.launch(roots=True)
        self.call("8", "hold")
        callback = self.receive(lambda message: message.get("method") == "roots/list")
        self.call(2, "browser_close")
        self.call(3, "navigate")
        self.send({"jsonrpc": "2.0", "id": callback["id"], "result": {"roots": []}})
        self.receive(lambda message: message.get("id") == "8")
        self.receive(lambda message: message.get("id") == 2)
        self.assertFalse(owned[0].exists())
        callback = self.receive(lambda message: message.get("method") == "roots/list")
        self.send({"jsonrpc": "2.0", "id": callback["id"], "result": {"roots": []}})
        self.receive(lambda message: message.get("id") == 3)
        fresh, _ = self.session()
        calls = [
            json.loads(line)
            for line in (fresh / "output/calls.jsonl").read_text().splitlines()
        ]
        self.assertEqual(calls, [{"id": 3, "name": "navigate"}])
        self.assertTrue(
            any(
                message.get("method") == "notifications/message"
                for message in self.messages
            )
        )

    def test_failed_or_malformed_close_and_cancelled_late_success_never_release(self):
        owned = self.launch()
        for identity, mode in enumerate(("rpc-error", "tool-error", "malformed"), 2):
            self.call(identity, "browser_close", {"mode": mode})
            response = self.receive(lambda message: message.get("id") == identity)
            self.assertTrue(owned[0].exists())
            if mode == "rpc-error":
                self.assertEqual(response["error"]["message"], "original failure")
            else:
                self.assertEqual(
                    response["result"]["isError"], True if mode == "tool-error" else 1
                )
        self.call(5, "browser_close", {"mode": "late"})
        self.receive(
            lambda message: message.get("params", {}).get("data") == "close pending"
        )
        self.send(
            {
                "jsonrpc": "2.0",
                "method": "notifications/cancelled",
                "params": {"requestId": 5},
            }
        )
        self.assertEqual(
            self.receive(lambda message: message.get("id") == 5)["result"],
            {"content": []},
        )
        self.assertTrue(owned[0].exists())

    def test_cancellation_of_queued_navigation_does_not_execute_it(self):
        owned = self.launch()
        self.call(2, "hold")
        callback = self.receive(lambda message: message.get("method") == "roots/list")
        self.call(3, "browser_close")
        self.call(4, "navigate")
        self.send(
            {
                "jsonrpc": "2.0",
                "method": "notifications/cancelled",
                "params": {"requestId": 4},
            }
        )
        self.assertEqual(
            self.receive(lambda message: message.get("id") == 4)["error"]["code"],
            -32800,
        )
        self.send({"jsonrpc": "2.0", "id": callback["id"], "result": {"roots": []}})
        self.receive(lambda message: message.get("id") == 2)
        self.receive(lambda message: message.get("id") == 3)
        self.released(owned)
        self.assertEqual(
            list((self.repository / "artifacts/native-browsers").glob("session-*")), []
        )

    def test_cleanup_failure_returns_one_explicit_error_preserving_unsafe_output(self):
        owned = self.launch()
        self.call(2, "browser_close", {"mode": "unsafe"})
        response = self.receive(lambda message: message.get("id") == 2)
        self.assertIn("could not be released", response["error"]["message"])
        self.assertEqual(self.process.wait(timeout=5), 2)
        self.assertEqual(self.process.stdout.read(), b"")
        self.assertTrue(owned[0].exists())

    def test_backend_crash_returns_failure_without_replaying_call(self):
        self.launch()
        self.call(2, "crash")
        self.assertIn(
            "not replayed",
            self.receive(lambda message: message.get("id") == 2)["error"]["message"],
        )
        self.assertEqual(self.process.wait(timeout=5), 2)
        self.assertEqual(
            list((self.repository / "artifacts/native-browsers").glob("session-*")), []
        )

    def test_incompatible_private_initialize_fails_without_forwarding_followup(self):
        owned = self.launch()
        self.call(2, "browser_close")
        self.receive(lambda message: message.get("id") == 2)
        self.released(owned)
        (self.repository / "incompatible").touch()
        self.call(3, "navigate")
        self.assertIn(
            "not replayed",
            self.receive(lambda message: message.get("id") == 3)["error"]["message"],
        )
        self.assertEqual(self.process.wait(timeout=5), 2)

    def test_descendant_ignoring_term_is_gone_before_close_success(self):
        owned = self.launch()
        self.call(2, "descendant")
        self.receive(lambda message: message.get("id") == 2)
        descendant = int((owned[0] / "output/descendant.pid").read_text())
        self.assertIsNotNone(browser.process_birth_tick(descendant))
        self.call(3, "browser_close")
        self.receive(lambda message: message.get("id") == 3, timeout=12)
        self.released(owned)
        self.assertIsNone(browser.process_birth_tick(descendant))

    def test_signal_during_release_finishes_owned_teardown_before_endpoint_exit(self):
        owned = self.launch()
        self.call(2, "descendant")
        self.receive(lambda message: message.get("id") == 2)
        descendant = int((owned[0] / "output/descendant.pid").read_text())
        self.call(3, "browser_close")
        deadline = time.monotonic() + 3
        while browser.process_birth_tick(owned[1]["child_pid"]) is not None:
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.01)
        # Leader exited, ignoring descendant remains: interruption hits release.
        self.assertIsNotNone(browser.process_birth_tick(descendant))
        self.process.terminate()
        self.assertEqual(self.process.wait(timeout=12), 143)
        self.assertFalse(owned[0].exists())
        self.assertIsNone(browser.process_birth_tick(descendant))
        self.assertIsNone(browser.process_birth_tick(owned[1]["guardian_pid"]))

    def test_eof_while_dormant_and_signal_while_privately_initializing_clean_ownership(
        self,
    ):
        owned = self.launch(roots=True)
        self.call(2, "browser_close")
        self.receive(lambda message: message.get("id") == 2)
        self.released(owned)
        self.call(3, "navigate")
        self.receive(lambda message: message.get("method") == "roots/list")
        initializing = self.session()
        self.process.terminate()
        self.assertEqual(self.process.wait(timeout=5), 143)
        self.assertFalse(initializing[0].exists())
        self.assertIsNone(browser.process_birth_tick(initializing[1]["child_pid"]))
        self.assertIsNone(browser.process_birth_tick(initializing[1]["guardian_pid"]))

        # Another isolated endpoint reaches dormant state then shuts down by EOF.
        self.launch()
        self.call(2, "browser_close")
        self.receive(lambda message: message.get("id") == 2)
        self.process.stdin.close()
        self.assertEqual(self.process.wait(timeout=5), 0)
        self.assertEqual(
            list((self.repository / "artifacts/native-browsers").glob("session-*")), []
        )


class RelayFrameTests(unittest.TestCase):
    def test_split_and_multiple_frames_preserve_original_bytes_and_typed_ids(self):
        frames = browser.Frames()
        first = b'{"jsonrpc":"2.0","id":1,"method":"ping"}\n'
        second = b'{"jsonrpc":"2.0","id":"1","method":"ping"}\n'
        self.assertEqual(list(frames.receive(first[:12])), [])
        parsed = list(frames.receive(first[12:] + second))
        self.assertEqual([raw for _, raw in parsed], [first, second])
        self.assertNotEqual(
            browser._request_id(parsed[0][0]), browser._request_id(parsed[1][0])
        )

    def test_malformed_and_oversized_frames_fail_with_fixed_errors(self):
        for value in (
            b"not-json\n",
            b"[]\n",
            b'{"jsonrpc":"2.0","id":true,"method":"ping"}\n',
        ):
            with self.subTest(value=value), self.assertRaises(browser.ProtocolError):
                list(browser.Frames().receive(value))
        with mock.patch.object(browser, "MAX_MESSAGE_BYTES", 8):
            for value in (b"x" * 9, b"x" * 9 + b"\n"):
                with self.assertRaises(browser.ProtocolError):
                    list(browser.Frames().receive(value))

    def make_relay(self):
        incoming, writer = os.pipe()
        reader, outgoing = os.pipe()
        for descriptor in (incoming, writer, reader, outgoing):
            self.addCleanup(os.close, descriptor)
        backend = mock.Mock()
        backend.process = None
        relay = browser.Relay(backend, incoming, outgoing)
        self.addCleanup(relay.selector.close)
        return relay, reader

    def test_private_initialize_ids_avoid_client_collision_and_never_leak(self):
        relay, _ = self.make_relay()
        collision = "schemii-private-" + "a" * 32
        request = {"jsonrpc": "2.0", "id": collision, "method": "tools/list"}
        relay.requests[(str, collision)] = "tools/list"
        relay.deferred = [(request, relay.encoded(request))]
        relay.initialize = {"protocolVersion": "2024-11-05", "capabilities": {}}
        relay.identity = {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "serverInfo": {"name": "fake"},
        }
        relay.initialized = relay.encoded(
            {"jsonrpc": "2.0", "method": "notifications/initialized"}
        )
        with mock.patch.object(
            browser.secrets, "token_hex", side_effect=["a" * 32, "b" * 32]
        ):
            relay.progress()
        self.assertEqual(relay.private_id, (str, "schemii-private-" + "b" * 32))
        response = {
            "jsonrpc": "2.0",
            "id": relay.private_id[1],
            "result": relay.identity,
        }
        relay.server(response, relay.encoded(response))
        self.assertEqual(relay.to_client, b"")
        self.assertIsNone(relay.private_id)
        self.assertEqual(relay.deferred[0][0], request)

    def ready_for_release(self):
        relay, reader = self.make_relay()
        relay.backend.session = None
        relay.identity = {"protocolVersion": "2024-11-05", "capabilities": {}}
        relay.initialized = b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n'
        return relay, reader

    def release_request(self, identity=1, **params):
        return {
            "jsonrpc": "2.0",
            "id": identity,
            "method": "tools/call",
            "params": {"name": "browser_release", "arguments": {}, **params},
        }

    def test_release_rejects_target_and_malformed_arguments_without_cleanup(self):
        for params in (
            {"arguments": {"pid": 123}},
            {"arguments": []},
            {"arguments": None},
            {"target": "peer"},
            {"_meta": None},
        ):
            with self.subTest(params=params):
                relay, _ = self.ready_for_release()
                request = self.release_request(**params)
                relay.client(request, relay.encoded(request))
                self.assertEqual(json.loads(relay.to_client)["error"]["code"], -32602)
                relay.backend.close.assert_not_called()
                self.assertFalse(relay.releasing)

    def test_release_rejects_each_unsafe_pending_boundary(self):
        states = {
            "identity": None,
            "initialized": None,
            "requests": {(int, 7): "tools/call"},
            "inflight": {(int, 7): "tools/call"},
            "callbacks": {(str, "root")},
            "closing": [{"id": 7}, b"close", None, False],
            "private_id": (str, "private"),
            "deferred": [("pending", b"pending")],
            "to_client": bytearray(b"pending"),
            "to_backend": bytearray(b"pending"),
        }
        for name, value in states.items():
            with self.subTest(name=name):
                relay, _ = self.ready_for_release()
                setattr(relay, name, value)
                request = self.release_request()
                relay.client(request, relay.encoded(request))
                self.assertIn(b"busy", relay.to_client)
                relay.backend.close.assert_not_called()
                self.assertFalse(relay.releasing)
        relay, _ = self.ready_for_release()
        relay.backend_frames.buffer.extend(b'{"jsonrpc":')
        request = self.release_request()
        relay.client(request, relay.encoded(request))
        self.assertIn(b"busy", relay.to_client)
        relay.backend.close.assert_not_called()

    def test_release_rejects_unread_backend_callback_before_frame_admission(self):
        relay, _ = self.ready_for_release()
        incoming, outgoing = os.pipe()
        for descriptor in (incoming, outgoing):
            self.addCleanup(os.close, descriptor)
        relay.backend.process = mock.Mock(stdout=incoming)
        os.write(outgoing, b'{"jsonrpc":"2.0","id":"root","method":"roots/list"}\n')
        request = self.release_request()
        relay.client(request, relay.encoded(request))
        self.assertIn(b"busy", relay.to_client)
        relay.backend.close.assert_not_called()

    def test_release_drains_partial_writes_then_returns_without_backend_revival(self):
        relay, reader = self.ready_for_release()
        request = self.release_request(identity="release", _meta={"progressToken": 3})
        relay.client(request, relay.encoded(request))
        expected = bytes(relay.to_client)
        self.assertTrue(relay.releasing)
        relay.backend.close.assert_called_once_with()
        original = os.write
        with mock.patch.object(
            browser.os, "write", side_effect=lambda fd, data: original(fd, data[:17])
        ) as write:
            self.assertEqual(relay.run(), 0)
            self.assertGreater(write.call_count, 1)
        self.assertEqual(os.read(reader, len(expected)), expected)
        self.assertIs(json.loads(expected)["result"]["isError"], False)
        relay.backend.start.assert_not_called()

    def test_release_refuses_same_batch_followup_and_duplicate_without_replay(self):
        relay, _ = self.ready_for_release()
        request = self.release_request()
        followup = {
            "jsonrpc": "2.0",
            "id": "1",
            "method": "tools/call",
            "params": {
                "name": "browser_navigate",
                "arguments": {"url": "data:text/html,owned"},
            },
        }
        for message, raw in relay.client_frames.receive(
            relay.encoded(request) + relay.encoded(followup) + relay.encoded(request)
        ):
            relay.client(message, raw)
        relay.progress()
        responses = [json.loads(line) for line in relay.to_client.splitlines()]
        self.assertEqual([response["id"] for response in responses], [1, "1"])
        self.assertIn("permanently released", responses[1]["error"]["message"])
        self.assertEqual(relay.to_backend, b"")
        self.assertEqual(relay.deferred, [])
        relay.backend.start.assert_not_called()
        relay.backend.close.assert_called_once_with()

    def test_release_broken_output_is_failure_after_owned_cleanup(self):
        relay, _ = self.ready_for_release()
        request = self.release_request()
        relay.client(request, relay.encoded(request))
        with mock.patch.object(browser.os, "write", side_effect=BrokenPipeError):
            with self.assertRaises(BrokenPipeError):
                relay.run()
        self.assertTrue(relay.to_client)
        relay.backend.close.assert_called_once_with()
        relay.backend.start.assert_not_called()

    def test_release_stalled_output_is_bounded_and_does_not_restart_backend(self):
        relay, _ = self.ready_for_release()
        while True:
            try:
                os.write(relay.outgoing, b"x" * 65536)
            except BlockingIOError:
                break
        request = self.release_request()
        relay.client(request, relay.encoded(request))
        started = time.monotonic()
        with self.assertRaisesRegex(browser.ProtocolError, "fully flushed"):
            relay.run()
        self.assertLess(time.monotonic() - started, 2.5)
        self.assertTrue(relay.to_client)
        relay.backend.close.assert_called_once_with()
        relay.backend.start.assert_not_called()

    def test_release_inventory_preserves_original_schema_and_rejects_collisions(self):
        for tools in ([{"name": "browser_release"}], [None]):
            relay, _ = self.ready_for_release()
            relay.inflight[(int, 1)] = "tools/list"
            relay.requests[(int, 1)] = "tools/list"
            message = {"jsonrpc": "2.0", "id": 1, "result": {"tools": tools}}
            with self.assertRaises(browser.ProtocolError):
                relay.server(message, relay.encoded(message))
            self.assertEqual(relay.to_client, b"")

    def test_failure_flush_is_bounded_when_upstream_stops_reading(self):
        relay, _ = self.make_relay()
        while True:
            try:
                os.write(relay.outgoing, b"x" * 65536)
            except BlockingIOError:
                break
        relay.error((int, 1), "fixed transport failure")
        started = time.monotonic()
        relay.flush_failure()
        self.assertLess(time.monotonic() - started, 1.5)
        self.assertTrue(relay.to_client)

    def test_pending_and_byte_budgets_fail_before_unbounded_admission(self):
        relay, _ = self.make_relay()
        with mock.patch.object(browser, "MAX_RELAY_BYTES", 8):
            with self.assertRaises(browser.ProtocolError):
                relay.queue(relay.to_client, b"x" * 9)
        relay.to_client.clear()
        with mock.patch.object(browser, "MAX_PENDING_REQUESTS", 1):
            request = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
            relay.client(request, relay.encoded(request))
            request["id"] = 2
            with self.assertRaises(browser.ProtocolError):
                relay.client(request, relay.encoded(request))

    def test_departed_live_descendant_cannot_authorize_a_reused_group(self):
        process = mock.Mock(pid=123)
        process._schemii_group = {12: 50}
        with (
            mock.patch.object(browser.os, "listdir", return_value=["12", "123"]),
            mock.patch.object(
                browser,
                "process_birth_tick",
                side_effect=lambda pid: 50 if pid == 12 else 60,
            ),
            mock.patch.object(
                browser.os, "getpgid", side_effect=lambda pid: 999 if pid == 12 else 123
            ),
            mock.patch.object(
                browser.os, "getsid", side_effect=lambda pid: 999 if pid == 12 else 123
            ),
            mock.patch.object(browser.os, "killpg") as kill,
        ):
            with self.assertRaises(browser.ArtifactError):
                browser._stop_child(process)
        kill.assert_not_called()


if __name__ == "__main__":
    unittest.main()
