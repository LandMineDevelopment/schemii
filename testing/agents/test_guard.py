"""Behavioral tests with temporary Git worktrees and token-free QA manifests."""

import copy
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

from testing.agents import guard


class AssignmentGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.directory = Path(cls.temporary.name)
        cls.repository = cls.directory / "project"
        cls.worktree = cls.directory / "worker"
        cls.repository.mkdir()
        cls.git("init", "--initial-branch=main")
        cls.git(
            "-c",
            "user.name=Guard test",
            "-c",
            "user.email=guard@example.invalid",
            "commit",
            "--allow-empty",
            "-m",
            "Fixture",
        )
        cls.git("worktree", "add", "-b", "task/worker", str(cls.worktree))

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    @classmethod
    def git(cls, *args):
        return subprocess.run(
            ["git", "-c", "core.hooksPath=/dev/null", "-C", str(cls.repository), *args],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def assignment(self, kind="development", workspace=None):
        return {
            "kind": kind,
            "task": "Fix query cancellation and preserve neighboring edits.",
            "workspace": str(workspace or self.worktree),
            "owned_paths": ["src/query.py"],
            "owned_resources": [],
            "verification": ["python -m pytest tests/test_query.py"],
        }

    def event(self, assignment=None, tool="spawn_agent", field="message"):
        assignment = self.assignment() if assignment is None else assignment
        return {
            "hook_event_name": "PreToolUse",
            "tool_name": tool,
            "tool_input": {
                field: "Work only on this task.\nSCHEMII_ASSIGNMENT "
                + json.dumps(assignment)
            },
        }

    def result(self, assignment):
        return guard.handle_event(self.event(assignment), self.repository)

    def assertDenied(self, result, reason=None):
        output = result["hookSpecificOutput"]
        self.assertEqual(output["hookEventName"], "PreToolUse")
        self.assertEqual(output["permissionDecision"], "deny")
        self.assertTrue(output["permissionDecisionReason"])
        self.assertNotIn("continue", output)
        if reason:
            self.assertIn(reason, output["permissionDecisionReason"])

    def ready_qa(self):
        assignment = self.assignment("ui-testing", self.repository)
        run = "qa-guard-test"
        lane = "lane-1"
        assignment.update(
            owned_paths=[f"artifacts/qa/{run}/{lane}"],
            owned_resources=[f"qa:{run}:{lane}"],
            verification=[
                "View fresh desktop and mobile screenshots and check saved state.",
                "./test.sh checkpoint with each scenario's evidence and actual result.",
                "./test.sh finish after all assigned checkpoints.",
            ],
            qa={
                "run": run,
                "lane": lane,
                "claim": "after-spawn-before-actions",
                "browser": "isolated",
            },
        )
        self.run_dir = self.repository / "artifacts/qa" / run
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.manifest = {
            "id": run,
            "root": str(self.repository),
            "controller": "t3",
            "status": "ready",
            "deployment": {
                "identity": {"fingerprint": "a" * 64},
                "verifiedAt": "2026-09-29T00:00:00.000Z",
            },
            "lanes": [{"id": lane, "status": "ready", "agent": None}],
        }
        self.write_manifest()
        fields = Path(f"/proc/{os.getpid()}/stat").read_text().rsplit(")", 1)[1].split()
        self.owner = {"pid": os.getpid(), "birthTick": fields[19]}
        self.write_owner()
        return assignment

    def write_manifest(self):
        (self.run_dir / "manifest.json").write_text(json.dumps(self.manifest))

    def write_owner(self):
        (self.run_dir / "controller-owner.json").write_text(json.dumps(self.owner))

    def test_real_linked_worktree_development_is_allowed(self):
        self.assertEqual(self.result(self.assignment()), {})

    def test_supported_native_aliases_and_prompt_shape_are_allowed(self):
        for tool, field in [
            ("Agent", "prompt"),
            ("collaboration.spawn_agent", "message"),
            ("collaboration__spawn_agent", "message"),
            ("functions.spawn_agent", "message"),
        ]:
            with self.subTest(tool=tool):
                self.assertEqual(
                    guard.handle_event(
                        self.event(tool=tool, field=field), self.repository
                    ),
                    {},
                )

    def test_shared_main_checkout_needs_explicit_exclusive_paths(self):
        assignment = self.assignment(workspace=self.repository)
        self.assertDenied(self.result(assignment), "linked Git worktree")
        assignment["shared_checkout_exclusive"] = True
        self.assertEqual(self.result(assignment), {})
        assignment["owned_paths"] = []
        assignment["owned_resources"] = ["query"]
        self.assertDenied(self.result(assignment), "owned relative paths")

    def test_another_branch_in_main_checkout_is_still_shared(self):
        self.git("switch", "-c", "task/shared")
        try:
            self.assertDenied(
                self.result(self.assignment(workspace=self.repository)),
                "linked Git worktree",
            )
        finally:
            self.git("switch", "main")

    def test_detached_or_main_worktree_cannot_be_claimed_for_development(self):
        marker = (self.worktree / ".git").read_text().strip().removeprefix("gitdir: ")
        head = Path(marker) / "HEAD"
        original = head.read_text()
        try:
            for content in ["a" * 40 + "\n", "ref: refs/heads/main\n"]:
                head.write_text(content)
                self.assertDenied(self.result(self.assignment()), "linked Git worktree")
        finally:
            head.write_text(original)

    def test_read_only_research_and_review_allow_shared_checkout(self):
        for kind in ("research", "review"):
            with self.subTest(kind=kind):
                self.assertEqual(
                    self.result(self.assignment(kind, self.repository)), {}
                )

    def test_required_fields_blank_unknown_and_wrong_types_are_rejected(self):
        for field in [
            "kind",
            "task",
            "workspace",
            "owned_paths",
            "owned_resources",
            "verification",
        ]:
            assignment = self.assignment()
            del assignment[field]
            with self.subTest(missing=field):
                self.assertDenied(self.result(assignment))
        for field, value in [
            ("task", "  "),
            ("verification", [" "]),
            ("verification", ["tests"]),
            ("kind", "whatever"),
            ("kind", []),
            ("owned_paths", "src/query.py"),
            ("owned_resources", ["*"]),
            ("owned_resources", [" all "]),
            ("shared_checkout_exclusive", "true"),
        ]:
            assignment = self.assignment()
            assignment[field] = value
            with self.subTest(field=field, value=value):
                self.assertDenied(self.result(assignment))
        assignment = self.assignment()
        assignment["password"] = "not-echoed-test-secret"
        result = self.result(assignment)
        self.assertDenied(result, "unknown fields")
        self.assertNotIn("not-echoed-test-secret", json.dumps(result))

    def test_relative_missing_and_foreign_workspaces_are_rejected(self):
        for workspace in [".", str(self.directory / "missing"), str(self.directory)]:
            assignment = self.assignment()
            assignment["workspace"] = workspace
            with self.subTest(workspace=workspace):
                self.assertDenied(self.result(assignment))
        other = self.directory / "foreign"
        other.mkdir(exist_ok=True)
        (other / ".git").mkdir(exist_ok=True)
        (other / ".git/HEAD").write_text("ref: refs/heads/main\n")
        self.assertDenied(
            self.result(self.assignment("research", other)), "this project"
        )

    def test_path_traversal_globs_and_global_ownership_are_rejected(self):
        for path in [
            ".",
            "../foreign",
            "/tmp/foreign",
            "src/../query.py",
            "src/*.py",
            "src\\query.py",
            " src/query.py",
            "src//query.py",
            ".git/HEAD",
        ]:
            assignment = self.assignment()
            assignment["owned_paths"] = [path]
            with self.subTest(path=path):
                self.assertDenied(self.result(assignment))
        assignment = self.assignment()
        assignment.update(owned_paths=[], owned_resources=[])
        self.assertDenied(self.result(assignment), "explicit owned")

    def test_symlink_escape_is_rejected_even_when_final_file_does_not_exist(self):
        link = self.worktree / "escaped"
        link.symlink_to(self.directory, target_is_directory=True)
        try:
            assignment = self.assignment()
            assignment["owned_paths"] = ["escaped/nonexistent/file.txt"]
            self.assertDenied(self.result(assignment), "symlinks")
        finally:
            link.unlink()

    def test_missing_duplicate_multiline_and_malformed_contract_are_rejected(self):
        valid = self.event()
        line = valid["tool_input"]["message"].splitlines()[-1]
        for prompt in [
            "No contract",
            line + "\n" + line,
            "SCHEMII_ASSIGNMENT {",
            "SCHEMII_ASSIGNMENT\n{}",
            'SCHEMII_ASSIGNMENT {"task": "one", "task": "two"}',
        ]:
            event = copy.deepcopy(valid)
            event["tool_input"]["message"] = prompt
            with self.subTest(prompt=prompt[:50]):
                self.assertDenied(guard.handle_event(event, self.repository))

    def test_unrecognized_or_malformed_spawn_inputs_do_not_run_commands(self):
        for arguments in [
            None,
            [],
            {"unknown": "__import__('os').system('false')"},
            {"message": "", "prompt": "x"},
            {"message": {"command": "rm -rf /"}},
        ]:
            event = self.event()
            event["tool_input"] = arguments
            with self.subTest(arguments=arguments):
                self.assertDenied(guard.handle_event(event, self.repository))

    def test_nonspawn_tool_and_advisory_events_use_native_hook_shapes(self):
        self.assertEqual(
            guard.handle_event(
                {
                    "hook_event_name": "PreToolUse",
                    "tool_name": "Bash",
                    "tool_input": {"command": "true"},
                },
                self.repository,
            ),
            {},
        )
        for event in ["SessionStart", "UserPromptSubmit", "SubagentStart"]:
            output = guard.handle_event({"hook_event_name": event}, self.repository)[
                "hookSpecificOutput"
            ]
            self.assertEqual(output["hookEventName"], event)
            self.assertIn("SCHEMII_ASSIGNMENT", output["additionalContext"])
            self.assertNotIn("continue", output)
        self.assertDenied(guard.handle_event({}, self.repository))

    def test_ready_ui_lane_and_separate_qa_reviewer_are_allowed_without_reading_secrets(
        self,
    ):
        assignment = self.ready_qa()
        # Token-bearing files are deliberately invalid: the guard must not open them.
        for name in (
            "private.json",
            "control.json",
            "session-1.json",
            "credentials.json",
        ):
            (self.run_dir / name).write_text("not JSON; do not read")
        self.assertEqual(self.result(assignment), {})
        assignment["kind"] = "review"
        self.assertEqual(self.result(assignment), {})

    def test_qa_cannot_use_asserted_readiness_or_another_claim(self):
        assignment = self.ready_qa()
        del assignment["qa"]
        self.assertDenied(self.result(assignment), "qa requires")
        assignment = self.ready_qa()
        for status in [
            "queued",
            "preparing",
            "claimed",
            "blocked",
            "complete",
            "paused",
        ]:
            self.manifest["lanes"][0].update(
                status=status, agent="another-worker" if status == "claimed" else None
            )
            self.write_manifest()
            with self.subTest(status=status):
                self.assertDenied(self.result(assignment), "ready unclaimed")

    def test_qa_rejects_nonisolated_browser_extra_lane_or_source_ownership(self):
        for change in [
            "browser",
            "resource",
            "source",
            "claim",
            "evidence",
            "verification",
        ]:
            assignment = self.ready_qa()
            if change == "browser":
                assignment["qa"]["browser"] = "native-cooperative"
            elif change == "resource":
                assignment["owned_resources"].append("qa:qa-guard-test:lane-2")
            elif change == "source":
                assignment["owned_paths"].append("src/query.py")
            elif change == "claim":
                assignment["qa"]["claim"] = "already-claimed"
            elif change == "evidence":
                assignment["owned_paths"] = ["artifacts/qa/qa-guard-test/lane-2"]
            else:
                assignment["verification"] = ["Look at the screen."]
            with self.subTest(change=change):
                self.assertDenied(self.result(assignment))

    def test_qa_requires_verified_same_workspace_t3_run(self):
        for change in ["deployment", "root", "controller", "status", "id"]:
            assignment = self.ready_qa()
            self.manifest[change] = {
                "deployment": {},
                "root": str(self.worktree),
                "controller": "codex",
                "status": "stopped",
                "id": "qa-another-run",
            }[change]
            self.write_manifest()
            with self.subTest(change=change):
                self.assertDenied(self.result(assignment))

    def test_qa_dead_or_replaced_controller_cannot_use_stale_ready_manifest(self):
        assignment = self.ready_qa()
        self.owner["birthTick"] = "0"
        self.write_owner()
        self.assertDenied(self.result(assignment), "stopped or replaced")
        self.owner["pid"] = 999999999
        self.write_owner()
        self.assertDenied(self.result(assignment))

    def test_qa_read_does_not_follow_manifest_symlink_outside_workspace(self):
        assignment = self.ready_qa()
        manifest = self.run_dir / "manifest.json"
        manifest.unlink()
        manifest.symlink_to(self.directory / "private-credentials.json")
        try:
            self.assertDenied(self.result(assignment), "symlinks")
        finally:
            manifest.unlink()

    def test_git_metadata_aliases_cannot_be_owned(self):
        protected = [
            (self.repository, self.repository / ".git", "HEAD"),
            (self.worktree, self.repository / ".git", "worktrees/worker/HEAD"),
            (self.worktree, self.worktree / ".git", ""),
        ]
        for workspace, target, suffix in protected:
            alias = workspace / "git-alias"
            alias.symlink_to(target, target_is_directory=target.is_dir())
            try:
                assignment = self.assignment("research", workspace)
                assignment["owned_paths"] = [
                    "git-alias" + ("/" + suffix if suffix else "")
                ]
                with self.subTest(workspace=workspace, target=target):
                    self.assertDenied(self.result(assignment), "Git internals")
                    with self.assertRaisesRegex(
                        guard.InvalidAssignment, "Git internals"
                    ):
                        guard.owned_path(assignment["owned_paths"][0], workspace)
            finally:
                alias.unlink()

    def test_qa_lane_directory_cannot_alias_source_or_another_lane(self):
        for target_name in ("src", "artifacts/qa/qa-guard-test/lane-2"):
            assignment = self.ready_qa()
            target = self.repository / target_name
            target.mkdir(parents=True, exist_ok=True)
            lane = self.repository / assignment["owned_paths"][0]
            lane.symlink_to(target, target_is_directory=True)
            try:
                with self.subTest(target=target_name):
                    self.assertDenied(self.result(assignment), "symlinks")
            finally:
                lane.unlink()

    def test_qa_owned_subdirectory_and_file_cannot_alias_other_regions(self):
        assignment = self.ready_qa()
        lane = self.repository / assignment["owned_paths"][0]
        lane.mkdir(exist_ok=True)
        target = self.repository / "src"
        target.mkdir(exist_ok=True)
        screenshot = target / "source.png"
        screenshot.write_bytes(b"not lane evidence")
        try:
            for name, destination in [("images", target), ("screen.png", screenshot)]:
                alias = lane / name
                alias.symlink_to(destination, target_is_directory=destination.is_dir())
                try:
                    owned = dict(
                        assignment,
                        owned_paths=[str(alias.relative_to(self.repository))],
                    )
                    with self.subTest(name=name):
                        self.assertDenied(self.result(owned), "symlinks")
                finally:
                    alias.unlink()
        finally:
            lane.rmdir()

    def test_qa_metadata_files_cannot_alias_same_workspace_documents(self):
        assignment = self.ready_qa()
        for filename in ("manifest.json", "controller-owner.json"):
            path = self.run_dir / filename
            saved = path.read_bytes()
            target = self.repository / ("copied-" + filename)
            target.write_bytes(saved)
            path.unlink()
            path.symlink_to(target)
            try:
                with self.subTest(filename=filename):
                    self.assertDenied(self.result(assignment), "symlinks")
            finally:
                path.unlink()
                path.write_bytes(saved)
                target.unlink()

    def test_qa_run_directory_cannot_alias_same_workspace_directory(self):
        assignment = self.ready_qa()
        alternate = self.repository / "run-alias-target"
        self.run_dir.rename(alternate)
        self.run_dir.symlink_to(alternate, target_is_directory=True)
        try:
            self.assertDenied(self.result(assignment), "symlinks")
        finally:
            self.run_dir.unlink()
            alternate.rename(self.run_dir)

    def test_qa_artifact_root_cannot_alias_a_renamed_same_workspace_directory(self):
        assignment = self.ready_qa()
        artifacts = self.repository / "artifacts"
        alternate = self.repository / "renamed-artifacts"
        artifacts.rename(alternate)
        artifacts.symlink_to(alternate, target_is_directory=True)
        try:
            self.assertDenied(self.result(assignment), "symlinks")
        finally:
            artifacts.unlink()
            alternate.rename(artifacts)

    def test_real_existing_lane_evidence_file_is_allowed(self):
        assignment = self.ready_qa()
        lane = self.repository / assignment["owned_paths"][0]
        lane.mkdir(exist_ok=True)
        image = lane / "observed.png"
        image.write_bytes(b"test evidence bytes")
        assignment["owned_paths"] = [str(image.relative_to(self.repository))]
        try:
            self.assertEqual(self.result(assignment), {})
        finally:
            image.unlink()
            lane.rmdir()

    def test_cli_denial_is_json_exit_zero_without_echo_or_unsupported_fields(self):
        event = self.event()
        event["tool_input"]["message"] = "not-echoed-test-secret"
        result = self.cli(json.dumps(event).encode())
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, b"")
        self.assertDenied(json.loads(result.stdout))
        self.assertNotIn(b"not-echoed-test-secret", result.stdout)

    def test_cli_bounds_and_rejects_invalid_json_unicode_duplicate_keys(self):
        for raw in [
            b"{",
            b"\xff",
            b"[1]",
            b'{"hook_event_name":"PreToolUse","hook_event_name":"SessionStart"}',
            b'{"value":NaN}',
            b"x" * (guard.MAX_INPUT + 1),
        ]:
            with self.subTest(size=len(raw)):
                result = self.cli(raw)
                self.assertEqual(result.returncode, 0)
                self.assertEqual(result.stderr, b"")
                self.assertDenied(json.loads(result.stdout))

    def test_cli_allows_valid_assignment_and_injects_advisory_context(self):
        repository = Path(guard.__file__).resolve().parents[2]
        assignment = self.assignment("research", repository)
        result = self.cli(json.dumps(self.event(assignment)).encode())
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout), {})
        result = self.cli(b'{"hook_event_name":"SessionStart"}')
        self.assertIn(
            "additionalContext", json.loads(result.stdout)["hookSpecificOutput"]
        )

    def cli(self, raw):
        return subprocess.run(
            [sys.executable, guard.__file__],
            input=raw,
            capture_output=True,
            cwd=self.repository,
            timeout=5,
        )


class BrowserOutputGuardTests(unittest.TestCase):
    def screenshot(
        self, arguments, name="mcp__schemii_browser__browser_take_screenshot"
    ):
        return guard.handle_event(
            {
                "hook_event_name": "PreToolUse",
                "tool_name": name,
                "tool_input": arguments,
            },
            Path.cwd(),
        )

    def test_automatic_image_output_is_allowed(self):
        self.assertEqual(self.screenshot({"type": "png"}), {})
        self.assertEqual(self.screenshot({"filename": None}), {})

    def test_shared_workspace_and_traversing_filenames_are_denied(self):
        for filename in ("screenshot.png", "../report.png", "/tmp/shared.png", ""):
            with self.subTest(filename=filename):
                output = self.screenshot({"filename": filename})
                self.assertEqual(
                    output["hookSpecificOutput"]["permissionDecision"], "deny"
                )

    def test_dotted_tool_name_also_covers_output_ownership(self):
        output = self.screenshot(
            {"filename": "shared.png"}, "mcp.schemii_browser.browser_take_screenshot"
        )
        self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_all_filename_tools_are_guarded_and_matched_by_project_hook(self):
        config = json.loads(
            (Path(guard.__file__).parents[2] / ".codex/hooks.json").read_text()
        )
        matcher = config["hooks"]["PreToolUse"][0]["matcher"]
        for prefix in ("mcp__schemii_browser__", "mcp.schemii_browser."):
            for tool in ("take_screenshot", "snapshot", "find", "console_messages"):
                name = f"{prefix}browser_{tool}"
                with self.subTest(name=name):
                    self.assertIsNotNone(re.search(matcher, name))
                    self.assertEqual(self.screenshot({}, name), {})
                    output = self.screenshot({"filename": "shared.txt"}, name)
                    self.assertEqual(
                        output["hookSpecificOutput"]["permissionDecision"], "deny"
                    )

    def test_other_servers_keep_their_own_output_contract(self):
        self.assertEqual(
            self.screenshot(
                {"filename": "report.png"}, "mcp__other__browser_take_screenshot"
            ),
            {},
        )


if __name__ == "__main__":
    unittest.main()
