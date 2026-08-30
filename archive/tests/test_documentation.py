import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DocumentationTests(unittest.TestCase):
    def markdown_files(self):
        return [
            path for path in ROOT.rglob("*.md")
            if ".git" not in path.parts and "node_modules" not in path.parts
        ]

    def test_all_local_markdown_links_resolve(self):
        failures = []
        for path in self.markdown_files():
            text = path.read_text(encoding="utf-8")
            for target in re.findall(r"\[[^]]*\]\(([^)]+)\)", text):
                target = target.strip().split("#", 1)[0]
                if not target or re.match(r"^[a-z][a-z0-9+.-]*:", target, re.I):
                    continue
                destination = (path.parent / target).resolve()
                if not destination.exists():
                    failures.append(f"{path.relative_to(ROOT)} -> {target}")
        self.assertEqual(failures, [])

    def test_setup_docs_use_launcher_first_current_defaults(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        setup = (ROOT / "docs/AI_AGENT_SETUP.md").read_text(encoding="utf-8")
        assistant = (ROOT / "docs/AI_ASSISTANT.md").read_text(encoding="utf-8")

        self.assertIn("Docker is the only software required", readme)
        self.assertIn("### Without Git", readme)
        self.assertIn("bash ./start.sh", readme)
        self.assertIn("first start downloads", readme)
        self.assertIn("default `ai-docker-db` mode", setup)
        self.assertIn("Do not assume port 8080", setup)
        self.assertIn("schemii-metadata-postgres", readme)
        self.assertIn("metadata-migrate", setup)
        self.assertIn("metadata PostgreSQL remains private", setup)
        self.assertIn("bash ./start.sh schemer", setup)
        self.assertIn("instance-restore", readme)
        self.assertIn("legacy-volume-adopt ADOPT:schemii", readme)
        self.assertIn("legacy-volume-adoptions.v1", readme)
        self.assertIn("must never be copied, edited, or automatically replaced", setup)
        self.assertIn("docs/RELEASE_CHECKLIST.md", readme)
        self.assertIn("application-linux-amd64.tar.gz", readme)
        self.assertIn("gh attestation verify", readme)
        self.assertIn("Full Disaster Recovery Order", readme)
        self.assertIn("target-postgres.dump", readme)
        self.assertIn('docker exec -i "$postgres_id" pg_restore --list < disaster-recovery/target-postgres.dump', readme)
        self.assertIn("schemii-opencode-state.tar.gz", readme)
        self.assertIn('SCHEMII_CREDENTIAL_DIR="$SCHEMII_CREDENTIAL_DIR"', readme)
        self.assertIn('SCHEMII_METADATA_IMAGE="$SCHEMII_METADATA_IMAGE"', readme)
        self.assertIn('SCHEMII_OPENCODE_IMAGE="$SCHEMII_OPENCODE_IMAGE"', readme)
        self.assertIn("up --no-build -d postgres", readme)
        self.assertIn("rm -f disaster-recovery/SHA256SUMS", readme)
        self.assertIn("export SCHEMII_INSTANCE='<exact-instance>'", readme)
        self.assertIn("git fetch --no-tags origin", readme)
        self.assertIn("starts the complete private Compose stack with `--no-build`", readme)
        self.assertIn("SCHEMII_PUBLIC_ORIGINS", readme)
        self.assertIn("SCHEMER_PUBLIC_ORIGINS", readme)
        self.assertNotIn("git pull --ff-only", readme)
        self.assertNotIn("For a ZIP installation", readme)
        self.assertIn("protected GitHub `production-release` environment", (ROOT / "docs/RELEASE_CHECKLIST.md").read_text(encoding="utf-8"))
        self.assertIn("no model request is made until the user sends", assistant)
        for stale in (
            "The default trial starts only Schemii",
            "not started by default",
            "base Compose setup also maps",
            "Run docker compose logs schemii",
        ):
            self.assertNotIn(stale, "\n".join((readme, setup, assistant)))

    def test_agent_guides_match_networking_and_verification_contracts(self):
        guide = (ROOT / "agent_guide.md").read_text(encoding="utf-8")
        connection = (ROOT / "ai/workspace/.opencode/skills/connection-setup/SKILL.md").read_text(encoding="utf-8")
        help_skill = (ROOT / "ai/workspace/.opencode/skills/schemii-help/SKILL.md").read_text(encoding="utf-8")
        layout = (ROOT / ".opencode/skills/preserve-schemii-layout/SKILL.md").read_text(encoding="utf-8")

        self.assertIn('for test_file in tests/test_*.js; do node "$test_file" || exit 1; done', guide)
        self.assertIn("Base Compose does not add that mapping on Linux", connection)
        self.assertIn("no-argument launcher uses `ai-docker-db`", help_skill)
        self.assertIn("For a local-only design", layout)
        self.assertIn("Skip migration preview for a confirmed local-only design", layout)

        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        setup = (ROOT / "docs/AI_AGENT_SETUP.md").read_text(encoding="utf-8")
        guide = (ROOT / "agent_guide.md").read_text(encoding="utf-8")
        for required in (
            "machine-local reverse proxy", "SCHEMII_PUBLIC_ORIGINS", "SCHEMER_PUBLIC_ORIGINS",
            "X-Forwarded-Host", "X-Forwarded-Proto", "not application user authorization",
        ):
            self.assertIn(required, readme)
        self.assertIn("machine-local reverse proxy", setup)
        self.assertIn("publish applications only on loopback", guide)

    def test_schemer_docs_use_order_only_v3_and_precise_source_consistency_terms(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        assistant = (ROOT / "docs/AI_ASSISTANT.md").read_text(encoding="utf-8")
        audit = (ROOT / "docs/SHARED_RESOURCES_AUDIT.md").read_text(encoding="utf-8")

        self.assertIn("the cross-system linearization boundary", readme)
        self.assertIn("authoritative at the guarded repeatable-read snapshot", readme)
        self.assertIn("no response claims the catalog remains frozen", readme)
        self.assertIn("Preview's `deferredWidgetIds` is the sole continuation list", readme)
        self.assertIn("order safety", assistant)
        self.assertIn("uniform responsive version-3 cards", assistant.lower())
        self.assertIn("uniform responsive version-3 cards", audit)
        self.assertNotIn("layout safety", assistant)
        self.assertNotIn("desktop/mobile layout", assistant)
        self.assertNotIn("freeform widget", audit)
        self.assertNotIn("dashboard layout metadata", audit)


if __name__ == "__main__":
    unittest.main()
