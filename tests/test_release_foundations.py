import re
import subprocess
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ReleaseFoundationTests(unittest.TestCase):
    def test_release_hygiene_rejects_private_database_and_runtime_artifacts(self):
        result = subprocess.run(
            ["python3", "scripts/check-release-hygiene.py"],
            cwd=ROOT, check=True, capture_output=True, text=True,
        )
        self.assertIn("no private database or runtime data is tracked", result.stdout)
        gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")
        for pattern in ("*.dump", "*.backup", "*.sqlite", "*.db", "pgdata/", "dashboards/", "credentials/"):
            self.assertIn(pattern, gitignore)
            self.assertIn(pattern.rstrip("/"), dockerignore)

    def test_version_changelog_launchers_and_recovery_are_aligned(self):
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        self.assertRegex(version, r"^[0-9]+\.[0-9]+\.[0-9]+$")
        self.assertNotIn("version", project["project"])
        self.assertIn("version", project["project"]["dynamic"])
        self.assertEqual(project["tool"]["setuptools"]["dynamic"]["version"]["file"], "VERSION")
        self.assertIn(f"## {version} -", (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"))
        for name in ("start.sh", "start.ps1", "compose.recovery.yaml", "README.md"):
            self.assertNotIn(version, (ROOT / name).read_text(encoding="utf-8"), name)

    def test_release_workflow_explicitly_promotes_one_tested_candidate_without_rebuilding(self):
        workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("candidate_run_id:", workflow)
        self.assertIn("confirmation:", workflow)
        self.assertIn('[[ "$CONFIRMATION" == PROMOTE ]]', workflow)
        self.assertIn("environment: production-release", workflow)
        self.assertIn("actions: read", workflow)
        self.assertIn("contents: write", workflow)
        self.assertIn("packages: write", workflow)
        self.assertIn("attestations: write", workflow)
        self.assertIn("id-token: write", workflow)
        self.assertIn('[[ "$(jq -r .conclusion', workflow)
        self.assertIn('[[ "$(jq -r .head_branch', workflow)
        self.assertIn('[[ "$(jq -r .head_repository.full_name', workflow)
        self.assertIn('[[ "$(jq -r .path', workflow)
        self.assertIn('release-candidate-$REVISION', workflow)
        self.assertIn("scripts/release-manifest.py verify", workflow)
        self.assertIn("scripts/inspect-release-artifacts.py", workflow)
        self.assertIn("gh attestation verify", workflow)
        self.assertIn("docker load", workflow)
        self.assertIn("docker image inspect", workflow)
        self.assertIn("ghcr.io/$repository/$package", workflow)
        self.assertEqual(workflow.count("push-to-registry: true"), 3)
        self.assertIn("published-images.json", workflow)
        self.assertIn("gh release create", workflow)
        self.assertNotRegex(workflow, r"docker build(?:\s|$)")
        self.assertNotIn("python -m build", workflow)
        self.assertNotIn("tags:\n", workflow)
        self.assertNotRegex(workflow, r"(?i)(?:docker\s+(?:build|tag|push)|image:)\s+[^\n]*:latest")

    def test_ci_builds_tests_inspects_and_attests_one_candidate(self):
        workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        self.assertIn("permissions:\n  contents: read", workflow)
        self.assertEqual(workflow.count("persist-credentials: false"), 3)
        self.assertNotIn("contents: write", workflow)
        self.assertNotIn("packages: write", workflow)
        self.assertIn("attestations: write", workflow)
        self.assertIn("id-token: write", workflow)
        self.assertIn("SCHEMII_REQUIRE_CHROMIUM: \"1\"", workflow)
        self.assertIn("chrome@151.0.7922.138", workflow)
        self.assertIn("shell: bash\n        run: bash -n", workflow)
        self.assertIn("tests/test_start_recovery.ps1", workflow)
        self.assertIn("tests/test_legacy_volume_adoption.ps1", workflow)
        self.assertIn("SCHEMII_SMOKE_PROJECT: ci-smoke", workflow)
        self.assertIn('ci-smoke > "$RUNNER_TEMP/schemii-credentials/instance"', workflow)
        self.assertEqual(workflow.count("docker build --pull"), 3)
        self.assertEqual(workflow.count("python -m build --outdir dist"), 1)
        self.assertNotIn("docker compose -f compose.yaml -f compose.ai.yaml build", workflow)
        self.assertIn("scripts/test-python-artifacts.sh", workflow)
        self.assertIn("scripts/inspect-release-artifacts.py", workflow)
        self.assertIn("scripts/release-manifest.py create", workflow)
        self.assertIn("scripts/release-manifest.py verify", workflow)
        self.assertIn("SCHEMII_EXPECTED_REVISION", workflow)
        self.assertIn("--image \"application=release/", workflow)
        self.assertIn("docker save", workflow)
        self.assertIn("actions/upload-artifact@ea165f8d", workflow)
        self.assertIn("actions/attest-build-provenance@e8998f9", workflow)
        self.assertIn("github.event.repository.default_branch", workflow)
        self.assertNotRegex(workflow, r"smoke-compose\.sh[^\n]+--build(?:\s|$)")

    def test_docker_contexts_allow_only_each_images_required_inputs(self):
        application = (ROOT / "Dockerfile.dockerignore").read_text(encoding="utf-8")
        metadata = (ROOT / "docker/metadata/Dockerfile.dockerignore").read_text(encoding="utf-8")
        opencode = (ROOT / "ai/Dockerfile.dockerignore").read_text(encoding="utf-8")
        self.assertTrue(application.startswith("**\n"))
        self.assertIn("!dist/*.whl", application)
        self.assertIn("!docker/runtime-secret-entrypoint.sh", application)
        self.assertEqual(metadata, "**\n!secret-entrypoint.sh\n")
        self.assertIn("!workspace/.opencode/package-lock.json", opencode)
        self.assertIn("!secret-entrypoint.sh", opencode)

    def test_recovery_version_is_bound_to_the_metadata_image(self):
        metadata = (ROOT / "docker/metadata/Dockerfile").read_text(encoding="utf-8")
        recovery = (ROOT / "docker/recovery.sh").read_text(encoding="utf-8")
        compose = (ROOT / "compose.recovery.yaml").read_text(encoding="utf-8")
        self.assertIn("/opt/schemii-release-version", metadata)
        self.assertIn("/opt/schemii-release-revision", metadata)
        self.assertIn("SCHEMII_RECOVERY_VERSION_FILE:-/opt/schemii-release-version", recovery)
        self.assertNotIn("./VERSION:", compose)

    def test_container_foundations_are_digest_pinned(self):
        dockerfiles = [
            (ROOT / "Dockerfile").read_text(encoding="utf-8"),
            (ROOT / "docker/metadata/Dockerfile").read_text(encoding="utf-8"),
            (ROOT / "ai/Dockerfile").read_text(encoding="utf-8"),
        ]
        for source in dockerfiles:
            for line in (item for item in source.splitlines() if item.startswith("FROM ")):
                if line.startswith("FROM runtime "):
                    continue
                self.assertRegex(line, r"@sha256:[0-9a-f]{64}(?:\s+AS\s+\S+)?$")
        compose = (ROOT / "compose.postgres.yaml").read_text(encoding="utf-8")
        self.assertEqual(len(re.findall(r"postgres:17-alpine@sha256:[0-9a-f]{64}", compose)), 2)

    def test_runtime_dependencies_are_exact(self):
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertTrue(all("==" in item for item in project["build-system"]["requires"]))
        self.assertTrue(all("==" in item for item in project["project"]["dependencies"]))
        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
        self.assertTrue(all("==" in item for item in requirements if item))


if __name__ == "__main__":
    unittest.main()
