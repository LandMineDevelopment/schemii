import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ComposeConfigTests(unittest.TestCase):
    COMBINATIONS = (
        ("ui",),
        ("local-db", "compose.local-db.yaml"),
        ("docker-db", "compose.postgres.yaml"),
        ("ai", "compose.ai.yaml"),
        ("ai-local-db", "compose.local-db.yaml", "compose.ai.yaml", "compose.ai.local-db.yaml"),
        ("ai-docker-db", "compose.postgres.yaml", "compose.ai.yaml"),
        ("schemer", "compose.postgres.yaml", "compose.schemer.yaml"),
        ("schemer-ai", "compose.postgres.yaml", "compose.ai.yaml", "compose.schemer.yaml", "compose.schemer.ai.yaml"),
        ("recovery", "compose.recovery.yaml"),
    )

    @classmethod
    def setUpClass(cls):
        if shutil.which("docker") is None:
            raise unittest.SkipTest("Docker Compose is unavailable")
        result = subprocess.run(
            ["docker", "compose", "version"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise unittest.SkipTest("Docker Compose is unavailable")

    def compose_config(self, *overrides, environment=None):
        command = ["docker", "compose", "-f", "compose.yaml"]
        for override in overrides:
            command.extend(("-f", override))
        command.extend(("config", "--format", "json"))
        with tempfile.TemporaryDirectory() as directory:
            for name in (
                "metadata_bootstrap_password", "metadata_migration_password",
                "metadata_schemii_password", "metadata_schemer_password", "opencode_password",
            ):
                (Path(directory) / name).write_text(f"compose-test-{name}\n", encoding="utf-8")
            result = subprocess.run(
                command,
                cwd=ROOT,
                env={
                    **os.environ,
                    "SCHEMII_CREDENTIAL_DIR": directory,
                    "SCHEMII_INSTANCE": "compose-test",
                    **(environment or {}),
                },
                capture_output=True,
                text=True,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_all_supported_compose_combinations_are_valid(self):
        for combination in self.COMBINATIONS:
            with self.subTest(mode=combination[0]):
                config = self.compose_config(*combination[1:])
                self.assertIn('"metadata-postgres"', config)
                self.assertIn('"metadata-migrate"', config)

    def test_metadata_and_application_backends_are_never_host_published(self):
        bridge_config = self.compose_config()
        local_config = json.loads(self.compose_config("compose.local-db.yaml"))

        self.assertNotIn('"published": "5433"', bridge_config)
        self.assertNotIn("ports", local_config["services"]["metadata-postgres"])
        self.assertNotIn("ports", local_config["services"]["schemii"])
        self.assertNotIn("network_mode", local_config["services"]["schemii"])
        self.assertEqual(local_config["services"]["host-postgres-exporter"]["network_mode"], "host")
        self.assertNotIn("ports", local_config["services"]["host-postgres-exporter"])
        self.assertEqual(local_config["services"]["local-postgres-relay"]["network_mode"], "service:schemii")

    def test_http_ingress_is_pinned_hardened_and_exclusively_publishes_loopback(self):
        config = json.loads(self.compose_config("compose.postgres.yaml", "compose.schemer.yaml"))
        services = config["services"]
        image = "nginx:1.29.1-alpine@sha256:42a516af16b852e33b7682d5ef8acbd5d13fe08fecadc7ed98605ba5e3b26ab8"

        for app, ingress, network, published in (
            ("schemii", "schemii-ingress", "schemii-ingress", "8080"),
            ("schemer", "schemer-ingress", "schemer-ingress", "8081"),
        ):
            with self.subTest(app=app):
                self.assertNotIn("ports", services[app])
                self.assertEqual(set(services[app]["networks"]), {"default", network})
                proxy = services[ingress]
                self.assertEqual(proxy["image"], image)
                loopback_network = network.replace("-ingress", "-loopback")
                self.assertEqual(set(proxy["networks"]), {network, loopback_network})
                self.assertEqual(proxy["ports"], [{
                    "mode": "ingress", "host_ip": "127.0.0.1", "target": 8080,
                    "published": published, "protocol": "tcp",
                }])
                self.assertTrue(proxy["read_only"])
                self.assertEqual(proxy["user"], "101:101")
                self.assertEqual(proxy["cap_drop"], ["ALL"])
                self.assertNotIn("cap_add", proxy)
                self.assertIn("no-new-privileges:true", proxy["security_opt"])
                self.assertTrue(proxy["tmpfs"])
                self.assertIn("healthcheck", proxy)
                self.assertEqual(proxy["restart"], "unless-stopped")
                self.assertTrue(config["networks"][network]["internal"])
                self.assertFalse(config["networks"][loopback_network].get("internal", False))
                for other_service, details in services.items():
                    if other_service != ingress:
                        self.assertNotIn(loopback_network, details.get("networks", {}))

        for dependency in ("metadata-postgres", "metadata-migrate", "postgres", "example-seed", "example-profile-init"):
            self.assertNotIn("schemii-ingress", services[dependency].get("networks", {}))
            self.assertNotIn("schemer-ingress", services[dependency].get("networks", {}))

    def test_rendered_config_contains_secret_files_not_secret_values(self):
        config = self.compose_config("compose.postgres.yaml", "compose.ai.yaml", "compose.schemer.yaml", "compose.schemer.ai.yaml")
        self.assertNotIn("compose-test-", config)
        self.assertNotIn("PGPASSWORD", config)
        self.assertNotIn("OPENCODE_SERVER_PASSWORD:", config)
        self.assertIn("SCHEMII_METADATA_PASSWORD_FILE", config)
        self.assertIn("/run/secrets/opencode_password", config)

    def test_schemer_reuses_application_image_and_shared_post_seed_profile_initializer(self):
        config = json.loads(self.compose_config("compose.postgres.yaml", "compose.schemer.yaml"))
        services = config["services"]
        self.assertEqual(services["schemii"]["image"], services["schemer"]["image"])
        self.assertEqual(services["schemii"]["command"], ["schemii"])
        self.assertEqual(services["schemer"]["command"], ["schemer"])
        self.assertEqual(services["example-profile-init"]["command"], ["python", "-m", "schemii.example_profile_init"])
        self.assertEqual(services["example-profile-init"]["depends_on"]["example-seed"]["condition"], "service_completed_successfully")
        self.assertEqual(services["schemii"]["depends_on"]["example-profile-init"]["condition"], "service_completed_successfully")
        self.assertEqual(services["schemer"]["depends_on"]["example-profile-init"]["condition"], "service_completed_successfully")
        self.assertNotIn("build", services["schemii"])
        self.assertNotIn("build", services["metadata-postgres"])
        self.assertNotIn("build", services["metadata-migrate"])
        self.assertNotIn("build", services["example-profile-init"])
        self.assertNotIn("build", services["schemer"])
        self.assertNotIn("SCHEMER_IMAGE", (ROOT / "compose.schemer.yaml").read_text(encoding="utf-8"))

    def test_generic_public_origins_are_opt_in_and_keep_loopback_ingress(self):
        base = json.loads(self.compose_config("compose.postgres.yaml", "compose.schemer.yaml"))
        self.assertEqual(base["services"]["schemii"]["environment"]["SCHEMII_PUBLIC_ORIGINS"], "")
        self.assertEqual(base["services"]["schemer"]["environment"]["SCHEMER_PUBLIC_ORIGINS"], "")

        config = json.loads(self.compose_config(
            "compose.postgres.yaml", "compose.schemer.yaml",
            environment={
                "SCHEMII_PUBLIC_ORIGINS": "https://design.example.invalid",
                "SCHEMER_PUBLIC_ORIGINS": "https://dashboards.example.invalid",
            },
        ))
        services = config["services"]
        self.assertEqual(set(services["schemii"]["networks"]), {"default", "schemii-ingress"})
        self.assertEqual(set(services["schemer"]["networks"]), {"default", "schemer-ingress"})
        self.assertEqual(set(services["schemii-ingress"]["networks"]), {"schemii-ingress", "schemii-loopback"})
        self.assertEqual(set(services["schemer-ingress"]["networks"]), {"schemer-ingress", "schemer-loopback"})
        for excluded in ("metadata-postgres", "postgres", "opencode"):
            if excluded in services:
                self.assertNotIn("schemii-ingress", services[excluded].get("networks", {}))
                self.assertNotIn("schemer-ingress", services[excluded].get("networks", {}))
        self.assertTrue(config["networks"]["schemii-ingress"]["internal"])
        self.assertTrue(config["networks"]["schemer-ingress"]["internal"])
        self.assertEqual(services["schemii"]["environment"]["SCHEMII_PUBLIC_ORIGINS"], "https://design.example.invalid")
        self.assertEqual(services["schemer"]["environment"]["SCHEMER_PUBLIC_ORIGINS"], "https://dashboards.example.invalid")
        self.assertEqual(services["schemii"]["environment"]["SCHEMII_TRUSTED_LOCAL_PROXY"], "schemii-ingress")
        self.assertEqual(services["schemer"]["environment"]["SCHEMER_TRUSTED_LOCAL_PROXY"], "schemer-ingress")
        for ingress, published in (("schemii-ingress", "8080"), ("schemer-ingress", "8081")):
            self.assertEqual(services[ingress]["ports"][0]["host_ip"], "127.0.0.1")
            self.assertEqual(services[ingress]["ports"][0]["published"], published)


if __name__ == "__main__":
    unittest.main()
