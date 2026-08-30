import ast
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "schemii"
WEB = SRC / "web"
sys.path.insert(0, str(ROOT / "src"))

from schemii.server import _paths


class StandaloneRuntimeTests(unittest.TestCase):
    def run_shell_launcher(self, mode="ui", docker_script=None, system_tools=False):
        if os.name == "nt":
            self.skipTest("POSIX shell launcher is tested on POSIX runners")
        with tempfile.TemporaryDirectory() as directory:
            if docker_script is not None:
                docker = Path(directory) / "docker"
                docker.write_text("#!/bin/sh\n" + docker_script, encoding="utf-8")
                docker.chmod(0o755)
            return subprocess.run(
                ["/bin/bash", str(ROOT / "start.sh"), mode],
                cwd=ROOT,
                env={**os.environ, "PATH": directory + (":/usr/bin:/bin" if system_tools else "")},
                capture_output=True,
                text=True,
                timeout=10,
            )

    def test_launcher_help_does_not_require_docker(self):
        result = self.run_shell_launcher("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("Complete UI, tutorial PostgreSQL, and AI stack", result.stdout)
        self.assertIn("schemer-ai", result.stdout)
        self.assertIn("instance-restore", result.stdout)
        self.assertIn("#install-docker", result.stdout)

    def test_powershell_launcher_help_when_powershell_is_available(self):
        executable = shutil.which("pwsh") or shutil.which("powershell")
        if executable is None:
            self.skipTest("PowerShell is not installed")
        result = subprocess.run(
            [executable, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ROOT / "start.ps1"), "-Help"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Complete UI, tutorial PostgreSQL, and AI stack", result.stdout)
        self.assertIn("#install-docker", result.stdout)

    def test_launcher_prerequisite_errors_link_to_install_help(self):
        missing = self.run_shell_launcher()
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("Docker was not found", missing.stderr)
        self.assertIn("#install-docker", missing.stderr)

        unavailable = self.run_shell_launcher(docker_script='[ "$1" = "info" ] && exit 1\nexit 0\n')
        self.assertNotEqual(unavailable.returncode, 0)
        self.assertIn("daemon is unavailable or your user lacks permission", unavailable.stderr)
        self.assertIn("docker info", unavailable.stderr)

        no_compose = self.run_shell_launcher(docker_script='[ "$1" = "info" ] && exit 0\n[ "$1 $2 $3" = "compose version " ] && exit 1\nexit 1\n')
        self.assertNotEqual(no_compose.returncode, 0)
        self.assertIn("Docker Compose was not found", no_compose.stderr)
        self.assertIn("docs.docker.com/compose/install", no_compose.stderr)

    def test_launcher_stops_for_ambiguous_legacy_volumes(self):
        docker_script = '''
case "$*" in
  info|"compose version"|"volume inspect schemii_schemii-config"|"volume inspect schemii_schemii-schemas") exit 0 ;;
  ps*) exit 0 ;;
  *) exit 1 ;;
esac
'''
        result = self.run_shell_launcher(docker_script=docker_script, system_tools=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("Legacy Schemii data volumes were found", result.stderr)
        self.assertIn("SCHEMII_INSTANCE=schemii", result.stderr)
        self.assertIn("SCHEMII_INSTANCE=schemii-dev", result.stderr)

    def test_readme_has_beginner_docker_and_no_git_paths(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        for link in (
            "desktop/setup/install/windows-install",
            "desktop/setup/install/mac-install",
            "engine/install/",
            "compose/install/linux/",
        ):
            self.assertIn(link, readme)
        self.assertIn("### Without Git", readme)
        self.assertIn("bash ./start.sh", readme)
        self.assertIn("first start downloads", readme)

    def test_uninstallers_are_scoped_confirmed_and_avoid_prune(self):
        shell = (ROOT / "uninstall.sh").read_text(encoding="utf-8")
        powershell = (ROOT / "uninstall.ps1").read_text(encoding="utf-8")
        for source in (shell, powershell):
            self.assertIn("UNINSTALL", source)
            self.assertIn("com.docker.compose.project", source)
            self.assertIn("com.docker.compose.project.working_dir", source)
            self.assertIn("com.docker.compose.volume", source)
            self.assertIn("com.docker.compose.network", source)
            self.assertIn("schemii-opencode-data", source)
            self.assertIn("schemii-postgres", source)
            self.assertIn("schemer-dashboards", source)
            self.assertIn("schemii-recovery", source)
            self.assertIn("schemii-ingress", source)
            self.assertIn("schemer-ingress", source)
            self.assertNotIn('"schemer:local"', source)
            self.assertNotIn("system prune", source)
            self.assertNotIn("volume prune", source)
        self.assertIn('! -f "$repo_dir/compose.yaml"', shell)
        self.assertIn("not a recognized Schemii repository", powershell)

    def test_shell_launch_scripts_support_bash_3_2(self):
        bash_4_only = re.compile(r"\b(?:mapfile|readarray)\b|\b(?:declare|local)\s+-A\b")
        for name in ("start.sh", "uninstall.sh"):
            source = (ROOT / name).read_text(encoding="utf-8")
            self.assertNotRegex(source, bash_4_only, name)

    @unittest.skipIf(os.name == "nt", "POSIX shell uninstaller is tested on POSIX runners")
    def test_shell_uninstaller_removes_only_label_verified_owned_resources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = root / "schemii copy"
            (repository / "src/schemii").mkdir(parents=True)
            shutil.copy2(ROOT / "uninstall.sh", repository / "uninstall.sh")
            (repository / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
            (repository / "start.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            binary = root / "bin"
            binary.mkdir()
            log = root / "docker.log"
            credentials = root / "credential data"
            (credentials / "owned-app").mkdir(parents=True)
            credentials.chmod(0o700)
            (credentials / "owned-app/instance").write_text("owned-app\n", encoding="utf-8")
            docker = binary / "docker"
            docker.write_text('''#!/bin/sh
printf '%s\n' "$*" >> "$DOCKER_LOG"
for argument do last=$argument; done
case "$*" in
  info) exit 0 ;;
  "ps -aq") printf 'owned-app-container\nowned-schemer-container\nforeign-schemer-container\nspoof-container\n' ;;
  "ps -aq --filter ancestor="*) exit 0 ;;
  "volume ls -q") printf '%s\n' \
    owned-app_schemii-config owned-app_schemii-schemas owned-app_schemii-postgres \
    owned-schemer_schemer-dashboards owned-schemer_schemii-config \
    orphaned_schemii-config orphaned_schemii-schemas \
    collision_schemii-config foreign_schemer-dashboards ;;
  inspect*"{{.Config.Image}}"*owned-app-container)
    printf 'owned-app|schemii|%s|sha256:owned-app|schemii:owned-app\n' "$REPOSITORY" ;;
  inspect*"{{.Config.Image}}"*owned-schemer-container)
    printf 'owned-schemer|schemer|%s|sha256:shared-schemer|schemer:local\n' "$REPOSITORY" ;;
  inspect*"{{.Config.Image}}"*foreign-schemer-container)
    printf 'foreign|schemer|/tmp/unrelated|sha256:foreign|schemer:local\n' ;;
  inspect*"{{.Config.Image}}"*spoof-container)
    printf 'owned-app|schemer|/tmp/unrelated|sha256:spoof|schemii:owned-app\n' ;;
  "inspect --format "*owned-app-container)
    printf 'owned-app|schemii|%s\n' "$REPOSITORY" ;;
  "inspect --format "*owned-schemer-container)
    printf 'owned-schemer|schemer|%s\n' "$REPOSITORY" ;;
  "inspect --format "*foreign-schemer-container)
    printf 'foreign|schemer|/tmp/unrelated\n' ;;
  "inspect --format "*spoof-container)
    printf 'owned-app|schemer|/tmp/unrelated\n' ;;
  "volume inspect --format "*owned-app_schemii-postgres)
    case "$*" in
      *"{{.Name}}"*) printf 'someone-else|schemii-postgres|owned-app_schemii-postgres\n' ;;
      *) printf 'someone-else|schemii-postgres\n' ;;
    esac ;;
  "volume inspect --format "*"{{.Name}}"*)
    project=${last%%_*}; logical=${last#*_}
    printf '%s|%s|%s\n' "$project" "$logical" "$last" ;;
  "volume inspect --format "*)
    project=${last%%_*}; logical=${last#*_}
    printf '%s|%s\n' "$project" "$logical" ;;
  "network ls -q --filter label=com.docker.compose.project=owned-app") printf 'owned-network\nschemii-ingress-network\nschemer-ingress-network\nschemii-loopback-network\nschemer-loopback-network\nspoof-network\n' ;;
  "network ls -q --filter label=com.docker.compose.project="*) exit 0 ;;
  "network inspect --format "*owned-network) printf 'owned-app|default|owned-app_default\n' ;;
  "network inspect --format "*schemii-ingress-network) printf 'owned-app|schemii-ingress|owned-app_schemii-ingress\n' ;;
  "network inspect --format "*schemer-ingress-network) printf 'owned-app|schemer-ingress|owned-app_schemer-ingress\n' ;;
  "network inspect --format "*schemii-loopback-network) printf 'owned-app|schemii-loopback|owned-app_schemii-loopback\n' ;;
  "network inspect --format "*schemer-loopback-network) printf 'owned-app|schemer-loopback|owned-app_schemer-loopback\n' ;;
  "network inspect --format "*spoof-network) printf 'someone-else|default|owned-app_default\n' ;;
  "image inspect --format "*"schemii:owned-app") printf 'sha256:owned-app\n' ;;
  *) exit 0 ;;
esac
''', encoding="utf-8")
            docker.chmod(0o755)

            result = subprocess.run(
                ["/bin/bash", str(repository / "uninstall.sh"), "--yes"],
                cwd=repository,
                env={
                    **os.environ,
                    "PATH": f"{binary}:/usr/bin:/bin",
                    "DOCKER_LOG": str(log),
                    "REPOSITORY": str(repository),
                    "SCHEMII_CREDENTIAL_ROOT": str(credentials),
                },
                capture_output=True,
                text=True,
                timeout=20,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(repository.exists())
            self.assertFalse((credentials / "owned-app").exists())
            calls = log.read_text(encoding="utf-8")
            self.assertIn("rm -f owned-app-container", calls)
            self.assertIn("rm -f owned-schemer-container", calls)
            self.assertNotIn("rm -f foreign-schemer-container", calls)
            self.assertNotIn("rm -f spoof-container", calls)
            self.assertIn("network rm owned-network", calls)
            self.assertIn("network rm schemii-ingress-network", calls)
            self.assertIn("network rm schemer-ingress-network", calls)
            self.assertIn("network rm schemii-loopback-network", calls)
            self.assertIn("network rm schemer-loopback-network", calls)
            self.assertNotIn("network rm spoof-network", calls)
            self.assertIn("volume rm owned-app_schemii-config", calls)
            self.assertIn("volume rm owned-schemer_schemer-dashboards", calls)
            self.assertIn("volume rm orphaned_schemii-config", calls)
            self.assertIn("volume rm orphaned_schemii-schemas", calls)
            self.assertNotIn("volume rm owned-app_schemii-postgres", calls)
            self.assertNotIn("volume rm collision_schemii-config", calls)
            self.assertNotIn("volume rm foreign_schemer-dashboards", calls)
            self.assertIn("image rm schemii:owned-app", calls)
            self.assertNotIn("image rm schemer:local", calls)

    @unittest.skipIf(os.name == "nt", "POSIX shell uninstaller is tested on POSIX runners")
    def test_shell_uninstaller_handles_empty_docker_resource_lists(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = root / "schemii-empty"
            (repository / "src/schemii").mkdir(parents=True)
            shutil.copy2(ROOT / "uninstall.sh", repository / "uninstall.sh")
            (repository / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
            (repository / "start.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            binary = root / "bin"
            binary.mkdir()
            docker = binary / "docker"
            docker.write_text(
                '#!/bin/sh\ncase "$*" in info|"ps -aq"|"volume ls -q") exit 0 ;; *) exit 1 ;; esac\n',
                encoding="utf-8",
            )
            docker.chmod(0o755)

            result = subprocess.run(
                ["/bin/bash", str(repository / "uninstall.sh"), "--yes"],
                cwd=repository,
                env={**os.environ, "PATH": f"{binary}:/usr/bin:/bin"},
                capture_output=True,
                text=True,
                timeout=20,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Detected Schemii instances: none", result.stdout)
            self.assertFalse(repository.exists())

    def test_backend_has_no_outbound_clients_or_process_execution(self):
        forbidden_modules = {
            "aiohttp", "ftplib", "httpx", "paramiko", "requests", "smtplib",
            "socket", "subprocess", "telnetlib", "urllib.request", "xmlrpc.client",
        }
        violations = []
        for path in SRC.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                else:
                    continue
                for name in names:
                    if (path.name == "opencode_service.py" and name == "urllib.request") or (path.name == "http_access.py" and name == "socket"):
                        continue
                    if any(name == forbidden or name.startswith(f"{forbidden}.") for forbidden in forbidden_modules):
                        violations.append(f"{path.name}:{node.lineno}: {name}")
        self.assertEqual(violations, [], "Unexpected outbound/process imports: " + ", ".join(violations))

    def test_browser_assets_use_only_local_resources_and_api_calls(self):
        html = (WEB / "index.html").read_text(encoding="utf-8")
        css = (WEB / "styles.css").read_text(encoding="utf-8")
        javascript = (WEB / "app.js").read_text(encoding="utf-8")
        postgres_client = (ROOT / "src/schemii/shared_web/postgres-client.js").read_text(encoding="utf-8")
        session_client = (ROOT / "src/schemii/shared_web/session-client.js").read_text(encoding="utf-8")

        resource_urls = re.findall(r'''(?:src|href)=["']([^"']+)["']''', html)
        self.assertTrue(resource_urls)
        self.assertTrue(all("://" not in url and not url.startswith("//") for url in resource_urls))

        css_urls = re.findall(r'''url\(["']?([^"')]+)''', css)
        self.assertTrue(all(url.startswith("data:") or "://" not in url for url in css_urls))
        self.assertNotRegex(javascript, r"\b(?:WebSocket|EventSource|sendBeacon|importScripts)\s*\(")

        literal_fetches = re.findall(r'''fetch\(\s*(["'`])([^"'`]+)\1''', javascript)
        self.assertTrue(all(target.startswith("/api/") for _, target in literal_fetches))
        self.assertIn('sessionPath = "/api/session"', session_client)
        self.assertIn("validatePath(path, allowPath)", session_client)
        self.assertIn("await fetch(path,", session_client)
        self.assertIn('path.startsWith("/api/postgres/")', postgres_client)

    def test_storage_paths_are_absolute_and_independent_of_launch_directory(self):
        with patch.dict(
            "os.environ",
            {"SCHEMII_CONFIG_DIR": "relative-config", "SCHEMII_SCHEMA_DIR": "relative-schemas"},
        ):
            _, config_dir, schema_dir = _paths()
        self.assertTrue(config_dir.is_absolute())
        self.assertTrue(schema_dir.is_absolute())

    def test_ai_provider_credentials_use_a_stable_persistent_volume(self):
        compose = (ROOT / "compose.ai.yaml").read_text(encoding="utf-8")
        launcher = (ROOT / "start.sh").read_text(encoding="utf-8")

        self.assertIn("schemii-opencode-data:/opencode/data", compose)
        self.assertIn("schemii-opencode-data:", compose)
        self.assertIn("XDG_DATA_HOME: /opencode/data", compose)
        self.assertNotIn("~/.local/share/opencode", compose)
        self.assertNotIn("${HOME}", compose)
        self.assertNotIn("down --volumes", launcher)
        self.assertNotIn("volume rm", launcher)
        self.assertIn('credential_dir="${SCHEMII_CREDENTIAL_DIR:-$credential_root/$project}"', launcher)
        self.assertNotIn("SCHEMII_OPENCODE_PASSWORD=", launcher)

    def test_metadata_and_opencode_credentials_are_file_mounted(self):
        compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
        ai_compose = (ROOT / "compose.ai.yaml").read_text(encoding="utf-8")
        launcher = (ROOT / "start.sh").read_text(encoding="utf-8")
        for source in (compose, ai_compose):
            self.assertNotIn("PGPASSWORD:", source)
            self.assertNotIn("metadata-runtime-local", source)
        self.assertIn("POSTGRES_PASSWORD_FILE", compose)
        self.assertIn("SCHEMII_METADATA_PASSWORD_FILE", compose)
        self.assertIn("OPENCODE_SERVER_PASSWORD_FILE", ai_compose)
        self.assertIn("chmod 700", launcher)
        self.assertIn("chmod 600", launcher)
        self.assertIn("Existing metadata volume", launcher)
        self.assertNotIn("volume rm", launcher)
        self.assertIn("DAC_OVERRIDE", compose)
        runtime_entrypoint = (ROOT / "docker/runtime-secret-entrypoint.sh").read_text(encoding="utf-8")
        self.assertIn("--bounding-set=-all", runtime_entrypoint)

    def test_launchers_do_not_open_duplicate_browser_tabs(self):
        shell = (ROOT / "start.sh").read_text(encoding="utf-8")
        powershell = (ROOT / "start.ps1").read_text(encoding="utf-8")

        self.assertIn('curl --fail --silent --max-time 1 "$url"', shell)
        self.assertIn('"$was_ready" != "1"', shell)
        self.assertIn("SCHEMII_NO_OPEN", shell)
        self.assertIn("Invoke-WebRequest -Uri $url -TimeoutSec 1", powershell)
        self.assertIn("-not $NoOpen -and -not $wasReady", powershell)

    def test_launchers_default_to_isolated_tutorial_instances(self):
        shell = (ROOT / "start.sh").read_text(encoding="utf-8")
        powershell = (ROOT / "start.ps1").read_text(encoding="utf-8")
        postgres_compose = (ROOT / "compose.postgres.yaml").read_text(encoding="utf-8")

        self.assertIn('requested="${1:-ai-docker-db}"', shell)
        self.assertIn('[string]$Mode = "ai-docker-db"', powershell)
        for source in (shell, powershell):
            self.assertIn("SCHEMII_INSTANCE", source)
            self.assertIn("--project-name", source)
            self.assertIn("SCHEMII_HOST_PORT", source)
            self.assertNotIn("SCHEMII_METADATA_HOST_PORT", source)
            self.assertIn("Legacy Schemii data volumes were found", source)
        self.assertIn("service_completed_successfully", postgres_compose)
        self.assertIn("/seed/001_bookstore.sql:ro", postgres_compose)
        self.assertIn("SCHEMII_EXAMPLES: all", postgres_compose)

    def test_launchers_have_platform_parity_for_schemer_and_coordinated_recovery(self):
        shell = (ROOT / "start.sh").read_text(encoding="utf-8")
        powershell = (ROOT / "start.ps1").read_text(encoding="utf-8")
        recovery = (ROOT / "compose.recovery.yaml").read_text(encoding="utf-8")

        for source in (shell, powershell):
            for value in ("schemer", "schemer-ai", "SCHEMER_HOST_PORT", "instance-backup", "instance-restore"):
                self.assertIn(value, source)
            self.assertIn("compose.schemer.yaml", source)
            self.assertIn("compose.schemer.ai.yaml", source)
            self.assertIn("compose.recovery.yaml", source)
            self.assertIn("schemer-dashboards", source)
            self.assertIn("schemii-metadata-postgres", source)
            self.assertIn("RESTORE:", source)
            self.assertNotIn("SCHEMER_IMAGE", source)
        self.assertIn("health_services=(metadata-postgres schemii schemii-ingress)", shell)
        self.assertIn("health_services+=(schemer schemer-ingress)", shell)
        self.assertIn("Schemii companion:", shell)
        self.assertIn('$healthServices.Add(@("schemii-ingress", "Schemii ingress"))', powershell)
        self.assertIn('$healthServices.Add(@("schemer-ingress", "Schemer ingress"))', powershell)
        self.assertIn("Schemii companion:", powershell)
        self.assertIn("127.0.0.1:${SCHEMER_HOST_PORT:-8081}:8080", (ROOT / "compose.schemer.yaml").read_text(encoding="utf-8"))
        self.assertIn("application-recovery-verify", recovery)
        self.assertIn("schemii-schemas:/data/schemas", recovery)
        self.assertIn("./src/schemii/metadata/migrations:/opt/schemii-recovery/migrations:ro", recovery)
        self.assertIn("FOWNER", recovery)
        self.assertNotIn("/var/run/docker.sock", recovery)

    def test_metadata_postgres_is_dedicated_migrated_and_role_scoped(self):
        compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
        local = (ROOT / "compose.local-db.yaml").read_text(encoding="utf-8")
        schemer = (ROOT / "compose.schemer.yaml").read_text(encoding="utf-8")
        roles = (ROOT / "docker/metadata/001_roles.sh").read_text(encoding="utf-8")
        rotation = (ROOT / "docker/metadata/002_rotation_function.sql").read_text(encoding="utf-8")
        package = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

        self.assertIn("schemii-metadata-postgres:/var/lib/postgresql/data", compose)
        self.assertIn('["python", "-m", "schemii.metadata_migrate"]', compose)
        self.assertIn("service_completed_successfully", compose)
        self.assertNotRegex(compose, r'(?m)^    ports:.*\n(?:.*\n){0,3}.*metadata-postgres')
        self.assertIn("host-postgres-socket:/run/schemii-host-postgres:ro", local)
        self.assertIn("network_mode: service:schemii", local)
        self.assertNotIn("SCHEMII_METADATA_HOST_PORT", local)
        self.assertIn("schemii_metadata_schemii", compose)
        self.assertIn("schemii_metadata_schemer", schemer)
        self.assertIn("schemii_metadata_owner NOLOGIN", roles)
        self.assertNotIn("CREATEROLE", roles + rotation)
        self.assertNotIn("ADMIN OPTION", roles + rotation)
        self.assertIn("SECURITY DEFINER", rotation)
        self.assertIn("SET search_path = pg_catalog", rotation)
        self.assertIn("OWNER TO schemii_metadata_bootstrap", rotation)
        self.assertIn("REVOKE ALL ON FUNCTION", rotation)
        self.assertIn("TO schemii_metadata_migration", rotation)
        self.assertIn("^[A-Za-z0-9_-]+$", rotation)
        self.assertIn("octet_length", rotation)
        self.assertIn("ALTER ROLE schemii_metadata_bootstrap NOLOGIN", rotation)
        self.assertEqual(rotation.count("EXECUTE format('ALTER ROLE schemii_metadata_"), 3)
        self.assertNotIn("ALTER ROLE schemii_metadata_", (ROOT / "start.sh").read_text(encoding="utf-8"))
        self.assertNotIn("ALTER ROLE schemii_metadata_", (ROOT / "start.ps1").read_text(encoding="utf-8"))
        self.assertIn("options='-c role=schemii_metadata_owner'", compose)
        self.assertIn("ALTER DEFAULT PRIVILEGES FOR ROLE schemii_metadata_owner", roles)
        self.assertIn("schemii_admin.rotate_metadata_passwords", rotation)
        self.assertIn("002_rotation_function.sql:/docker-entrypoint-initdb.d/002_rotation_function.sql:ro", compose)
        self.assertEqual(compose.count("002_rotation_function.sql:/docker-entrypoint-initdb.d/002_rotation_function.sql:ro"), 1)
        self.assertNotIn("postgresql://schemii_metadata_", compose + schemer + local)
        self.assertIn("metadata/migrations/*.sql", package)

    def test_credential_lifecycle_is_marker_bound_and_recoverable(self):
        shell = (ROOT / "start.sh").read_text(encoding="utf-8")
        powershell = (ROOT / "start.ps1").read_text(encoding="utf-8")
        for source in (shell, powershell):
            self.assertIn(".credential-transaction", source)
            self.assertIn(".credential-transaction-committed", source)
            self.assertIn("committed-cleanup-required", source)
            self.assertIn("finalize-commit", source)
            self.assertIn("Backup instance marker", source)
            self.assertIn("16-256 characters from [A-Za-z0-9_-]", source)
        self.assertIn("rollback_credential_transaction", shell)
        self.assertIn("Undo-CredentialTransaction", powershell)
        self.assertIn("wait_for_metadata", shell)
        self.assertIn("Wait-MetadataReady", powershell)
        self.assertIn('cp "$temporary" "$path"', shell)
        self.assertIn("WriteAllBytes($Target", powershell)
        self.assertIn('write_secret "$temporary_dir/metadata_bootstrap_password"', shell)
        self.assertIn('$newValues["metadata_bootstrap_password"] = Read-CredentialValue', powershell)
        self.assertIn('temporary="$(mktemp "$credential_dir/.credential.XXXXXX")" \\', shell)
        self.assertIn('cp "$temporary" "$path" \\', shell)
        self.assertIn('rm -f -- "$temporary" \\', shell)
        self.assertIn("recovery refuses to infer that it is stopped", shell)
        self.assertIn("recovery refuses to infer that it is stopped", powershell)
        self.assertIn("recovery evidence was retained and commit was not attempted", shell)
        self.assertIn("recovery evidence was retained and commit was not attempted", powershell)
        self.assertIn('up --no-build -d --remove-orphans', shell)
        self.assertIn('@("up", "--no-build", "-d", "--remove-orphans")', powershell)
        self.assertIn('src/schemii/build_revision.txt', shell)
        self.assertIn('default_application_image="schemii:${release_identity}"', shell)
        self.assertIn('src/schemii/build_revision.txt', powershell)
        self.assertIn('$defaultApplicationImage = "schemii:$releaseIdentity"', powershell)

    def test_credential_and_recovery_mutations_keep_the_instance_lock(self):
        shell = (ROOT / "start.sh").read_text(encoding="utf-8")
        powershell = (ROOT / "start.ps1").read_text(encoding="utf-8")

        self.assertIn('credential_lock="${credential_dir}.lock"', shell)
        self.assertIn('if [[ "$credential_action" != "instance-backup" && "$credential_action" != "instance-restore" ]]', shell)
        self.assertGreater(
            shell.index("release_credential_lock", shell.index('if [[ "$credential_action" == "instance-backup"')),
            shell.index("cleanup_status=0", shell.index('if [[ "$credential_action" == "instance-backup"')),
        )
        self.assertRegex(shell, re.compile(r'run_credential_transaction\(\).*?wait_for_metadata .*?update_metadata_passwords', re.S))

        self.assertIn("[System.IO.FileShare]::None", powershell)
        self.assertIn('if ($Mode -notin @("instance-backup", "instance-restore")) { Exit-CredentialLock }', powershell)
        recovery_finally = powershell.index("finally { Exit-CredentialLock }")
        self.assertGreater(recovery_finally, powershell.index('if ($Mode -in @("instance-backup", "instance-restore"))'))
        self.assertRegex(powershell, re.compile(r'function Complete-CredentialTransaction.*?Wait-MetadataReady .*?Invoke-MetadataPasswordUpdate', re.S))

    @unittest.skipIf(os.name == "nt", "POSIX recovery failure propagation is tested on POSIX runners")
    def test_shell_recovery_propagates_each_stage_failure_and_credential_mismatch(self):
        credential_names = (
            "metadata_bootstrap_password", "metadata_migration_password",
            "metadata_schemii_password", "metadata_schemer_password", "opencode_password",
        )

        def write_credentials(directory, project, value):
            directory.mkdir(parents=True, mode=0o700)
            (directory / "instance").write_text(f"{project}\n", encoding="utf-8")
            (directory / "instance").chmod(0o600)
            for name in credential_names:
                path = directory / name
                path.write_text(value * 32 + "\n", encoding="utf-8")
                path.chmod(0o600)

        docker_source = r'''#!/bin/sh
printf '%s\n' "$*" >> "$DOCKER_LOG"
for argument do last=$argument; done
if [ "${SCHEMII_TEST_STOPPED_PS_FAILURE:-0}" = 1 ] \
    && [ "$*" = "ps -aq --filter label=com.docker.compose.project=$SCHEMII_INSTANCE" ]; then
  exit 71
fi
recovery_state_file=${SCHEMII_TEST_RECOVERY_STATE_FILE:-${DOCKER_LOG}.state}
case "$*" in
  *"metadata-recovery state")
    if [ -f "$recovery_state_file" ]; then cat "$recovery_state_file"; else printf 'none\n'; fi
    exit 0 ;;
  *"metadata-recovery restore")
    printf 'rollback-required\n' > "$recovery_state_file" ;;
  *"metadata-recovery commit")
    if [ "${SCHEMII_TEST_COMMIT_FAIL_BEFORE_PUBLISH:-0}" = 1 ] \
        && [ -n "${SCHEMII_TEST_FAIL_MATCH:-}" ]; then
      exit 71
    fi
    printf 'committed-cleanup-required\n' > "$recovery_state_file" ;;
  *"metadata-recovery finalize-commit")
    if [ "${SCHEMII_TEST_FAIL_MATCH:-}" = "metadata-recovery finalize-commit" ]; then exit 71; fi
    rm -f -- "$recovery_state_file"
    exit 0 ;;
  *"metadata-recovery rollback")
    rm -f -- "$recovery_state_file" ;;
esac
case "$*" in
  *"$SCHEMII_TEST_FAIL_MATCH"*)
    if [ -n "${SCHEMII_TEST_FAIL_MATCH:-}" ]; then exit 71; fi ;;
esac
case "$*" in
  info|"compose version") exit 0 ;;
  "volume inspect --format "*)
    logical=${last#"${SCHEMII_INSTANCE}_"}
    printf '%s|%s\n' "$SCHEMII_INSTANCE" "$logical"
    exit 0 ;;
  "volume inspect "*"_schemii-metadata-postgres") exit 0 ;;
  "ps -aq "*"com.docker.compose.service=metadata-postgres"*)
    [ "${SCHEMII_TEST_RETAINED_TRANSACTION:-0}" = 1 ] && printf 'metadata-container\n'
    exit 0 ;;
  "ps -aq --filter label=com.docker.compose.project=$SCHEMII_INSTANCE")
    [ "${SCHEMII_TEST_STOPPED_CONTAINER:-0}" = 1 ] && printf 'stopped-container\n'
    exit 0 ;;
  "ps -q "*|"ps -aq "*) exit 0 ;;
  "inspect --format {{.State.Running}} stopped-container") printf 'false\n'; exit 0 ;;
  *compose*" ps -q metadata-postgres") printf 'metadata-container\n'; exit 0 ;;
  "exec -u postgres metadata-container pg_isready"*) exit 0 ;;
  "cp "*)
    mkdir -p "$last"
    : > "$last/complete"
    if [ "${SCHEMII_TEST_MOVE_RACE:-0}" = 1 ]; then : > "$SCHEMII_TEST_FINAL_BACKUP"; fi
    exit 0 ;;
  *) exit 0 ;;
esac
'''

        cases = (
            ("image inspect", "Selected immutable recovery images are not loaded"),
            ("application-recovery-verify", "failed backup validation"),
            ("metadata-recovery backup", "Coordinated backup failed"),
            ("cp ", "Backup output could not be copied"),
        )
        for failed_command, expected_error in cases:
            with self.subTest(failed_command=failed_command), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                binary = root / "bin"
                binary.mkdir()
                docker = binary / "docker"
                docker.write_text(docker_source, encoding="utf-8")
                docker.chmod(0o755)
                project = "schemii-recovery-failure"
                credentials = root / "credentials"
                write_credentials(credentials, project, "a")
                backup_parent = root / "backups"
                result = subprocess.run(
                    ["/bin/bash", str(ROOT / "start.sh"), "instance-backup", str(backup_parent)],
                    cwd=ROOT,
                    env={
                        **os.environ,
                        "PATH": f"{binary}:/usr/bin:/bin",
                        "DOCKER_LOG": str(root / "docker.log"),
                        "SCHEMII_INSTANCE": project,
                        "SCHEMII_CREDENTIAL_DIR": str(credentials),
                        "SCHEMII_TEST_FAIL_MATCH": failed_command,
                    },
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
                self.assertEqual(result.returncode, 71, result.stdout + result.stderr)
                self.assertIn(expected_error, result.stderr)
                self.assertFalse((backup_parent / project).exists())

        for stopped_failure in ("ps", "inspect"):
            with self.subTest(stopped_failure=stopped_failure), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                binary = root / "bin"
                binary.mkdir()
                docker = binary / "docker"
                docker.write_text(docker_source, encoding="utf-8")
                docker.chmod(0o755)
                project = f"schemii-recovery-{stopped_failure}-failure"
                credentials = root / "credentials"
                write_credentials(credentials, project, "a")
                result = subprocess.run(
                    ["/bin/bash", str(ROOT / "start.sh"), "instance-backup", str(root / "backups")],
                    cwd=ROOT,
                    env={
                        **os.environ,
                        "PATH": f"{binary}:/usr/bin:/bin",
                        "DOCKER_LOG": str(root / "docker.log"),
                        "SCHEMII_INSTANCE": project,
                        "SCHEMII_CREDENTIAL_DIR": str(credentials),
                        "SCHEMII_TEST_FAIL_MATCH": "inspect --format {{.State.Running}} stopped-container" if stopped_failure == "inspect" else "never-match",
                        "SCHEMII_TEST_STOPPED_CONTAINER": "1" if stopped_failure == "inspect" else "0",
                        "SCHEMII_TEST_STOPPED_PS_FAILURE": "1" if stopped_failure == "ps" else "0",
                    },
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("refuses to infer that it is stopped", result.stderr)
                self.assertNotIn("metadata-recovery prepare", (root / "docker.log").read_text(encoding="utf-8"))

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / "bin"
            binary.mkdir()
            docker = binary / "docker"
            docker.write_text(docker_source, encoding="utf-8")
            docker.chmod(0o755)
            project = "schemii-recovery-move"
            credentials = root / "credentials"
            write_credentials(credentials, project, "a")
            backup_parent = root / "backups"
            result = subprocess.run(
                ["/bin/bash", str(ROOT / "start.sh"), "instance-backup", str(backup_parent)],
                cwd=ROOT,
                env={
                    **os.environ,
                    "PATH": f"{binary}:/usr/bin:/bin",
                    "DOCKER_LOG": str(root / "docker.log"),
                    "SCHEMII_INSTANCE": project,
                    "SCHEMII_CREDENTIAL_DIR": str(credentials),
                    "SCHEMII_TEST_FAIL_MATCH": "never-match",
                    "SCHEMII_TEST_MOVE_RACE": "1",
                    "SCHEMII_TEST_FINAL_BACKUP": str(backup_parent / project),
                },
                capture_output=True,
                text=True,
                timeout=20,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("could not be published", result.stderr)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / "bin"
            binary.mkdir()
            docker = binary / "docker"
            docker.write_text(docker_source, encoding="utf-8")
            docker.chmod(0o755)
            project = "schemii-recovery-immutable"
            credentials = root / "credentials"
            write_credentials(credentials, project, "a")
            result = subprocess.run(
                ["/bin/bash", str(ROOT / "start.sh"), "instance-backup", str(root / "backups")],
                cwd=ROOT,
                env={
                    **os.environ,
                    "PATH": f"{binary}:/usr/bin:/bin",
                    "DOCKER_LOG": str(root / "docker.log"),
                    "SCHEMII_INSTANCE": project,
                    "SCHEMII_CREDENTIAL_DIR": str(credentials),
                    "SCHEMII_IMAGE": "schemii:immutable",
                    "SCHEMII_METADATA_IMAGE": "schemii-metadata-postgres:immutable",
                    "SCHEMII_TEST_FAIL_MATCH": "never-match",
                },
                capture_output=True,
                text=True,
                timeout=20,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            log = (root / "docker.log").read_text(encoding="utf-8")
            self.assertIn("image inspect schemii:immutable schemii-metadata-postgres:immutable", log)
            self.assertNotIn("build metadata-postgres schemii", log)

        for mismatch in (False, True):
            with self.subTest(credential_stage_mismatch=mismatch), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                binary = root / "bin"
                binary.mkdir()
                docker = binary / "docker"
                docker.write_text(docker_source, encoding="utf-8")
                docker.chmod(0o755)
                project = "schemii-recovery-restore"
                credentials = root / "credentials"
                source = root / "backup"
                write_credentials(credentials, project, "a")
                write_credentials(source / "credentials", project, "b")
                (source / "instance").write_text(f"{project}\n", encoding="utf-8")
                if mismatch:
                    transaction = credentials / ".credential-transaction"
                    write_credentials(transaction / "old", project, "a")
                    write_credentials(transaction / "new", project, "c")
                    (transaction / "instance").write_text(f"{project}\n", encoding="utf-8")
                    (transaction / "operation").write_text("instance-restore\n", encoding="utf-8")
                result = subprocess.run(
                    ["/bin/bash", str(ROOT / "start.sh"), "instance-restore", str(source), f"RESTORE:{project}"],
                    cwd=ROOT,
                    env={
                        **os.environ,
                        "PATH": f"{binary}:/usr/bin:/bin",
                        "DOCKER_LOG": str(root / "docker.log"),
                        "SCHEMII_INSTANCE": project,
                        "SCHEMII_CREDENTIAL_DIR": str(credentials),
                        "SCHEMII_TEST_FAIL_MATCH": "metadata-recovery stage-verification" if not mismatch else "never-match",
                        "SCHEMII_TEST_RETAINED_TRANSACTION": "1" if mismatch else "0",
                    },
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                log = (root / "docker.log").read_text(encoding="utf-8")
                self.assertNotIn("metadata-recovery restore", log)
                if mismatch:
                    self.assertIn("do not match the retained restore staging transaction", result.stderr)
                    self.assertTrue((credentials / ".credential-transaction").is_dir())
                else:
                    self.assertIn("Backup manifest or archive verification failed", result.stderr)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / "bin"
            binary.mkdir()
            docker = binary / "docker"
            docker.write_text(docker_source, encoding="utf-8")
            docker.chmod(0o755)
            project = "schemii-recovery-stop-failure"
            credentials = root / "credentials"
            source = root / "backup"
            write_credentials(credentials, project, "a")
            write_credentials(source / "credentials", project, "b")
            (source / "instance").write_text(f"{project}\n", encoding="utf-8")
            result = subprocess.run(
                ["/bin/bash", str(ROOT / "start.sh"), "instance-restore", str(source), f"RESTORE:{project}"],
                cwd=ROOT,
                env={
                    **os.environ,
                    "PATH": f"{binary}:/usr/bin:/bin",
                    "DOCKER_LOG": str(root / "docker.log"),
                    "SCHEMII_INSTANCE": project,
                    "SCHEMII_CREDENTIAL_DIR": str(credentials),
                    "SCHEMII_TEST_FAIL_MATCH": "stop metadata-container",
                },
                capture_output=True,
                text=True,
                timeout=20,
            )
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn("recovery evidence was retained and commit was not attempted", result.stderr)
            self.assertTrue((credentials / ".credential-transaction").is_dir())
            calls = (root / "docker.log").read_text(encoding="utf-8")
            self.assertNotIn("metadata-recovery commit", calls)
            self.assertNotIn("Coordinated restore completed", result.stdout)

        for commit_failure in ("before-publish", "after-publish", "finalize-after-publish"):
            with self.subTest(commit_failure=commit_failure), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                binary = root / "bin"
                binary.mkdir()
                docker = binary / "docker"
                docker.write_text(docker_source, encoding="utf-8")
                docker.chmod(0o755)
                project = f"schemii-recovery-commit-{commit_failure}"
                credentials = root / "credentials"
                source = root / "backup"
                write_credentials(credentials, project, "a")
                write_credentials(source / "credentials", project, "b")
                (source / "instance").write_text(f"{project}\n", encoding="utf-8")
                base_env = {
                    **os.environ,
                    "PATH": f"{binary}:/usr/bin:/bin",
                    "DOCKER_LOG": str(root / "docker.log"),
                    "SCHEMII_TEST_RECOVERY_STATE_FILE": str(root / "recovery.state"),
                    "SCHEMII_INSTANCE": project,
                    "SCHEMII_CREDENTIAL_DIR": str(credentials),
                }
                failed = subprocess.run(
                    ["/bin/bash", str(ROOT / "start.sh"), "instance-restore", str(source), f"RESTORE:{project}"],
                    cwd=ROOT,
                    env={
                        **base_env,
                        "SCHEMII_TEST_FAIL_MATCH": "metadata-recovery finalize-commit" if commit_failure == "finalize-after-publish" else "metadata-recovery commit",
                        "SCHEMII_TEST_COMMIT_FAIL_BEFORE_PUBLISH": "1" if commit_failure == "before-publish" else "0",
                    },
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
                self.assertEqual(failed.returncode, 71, failed.stdout + failed.stderr)
                first_calls = (root / "docker.log").read_text(encoding="utf-8")
                if commit_failure == "before-publish":
                    self.assertIn("failed before publication", failed.stderr)
                    self.assertIn("metadata-recovery rollback", first_calls)
                    self.assertFalse((credentials / ".credential-transaction").exists())
                else:
                    self.assertIn("forward cleanup", failed.stderr)
                    self.assertNotIn("metadata-recovery rollback", first_calls)
                    self.assertEqual(
                        (credentials / ".credential-transaction").is_dir(),
                        commit_failure == "after-publish",
                    )

                    restarted = subprocess.run(
                        ["/bin/bash", str(ROOT / "start.sh"), "instance-restore", str(source), f"RESTORE:{project}"],
                        cwd=ROOT,
                        env={**base_env, "SCHEMII_TEST_FAIL_MATCH": "never-match"},
                        capture_output=True,
                        text=True,
                        timeout=20,
                    )
                    self.assertEqual(restarted.returncode, 0, restarted.stdout + restarted.stderr)
                    all_calls = (root / "docker.log").read_text(encoding="utf-8")
                    self.assertEqual(all_calls.count("metadata-recovery restore\n"), 1)
                    self.assertNotIn("metadata-recovery rollback", all_calls)
                    self.assertIn("metadata-recovery finalize-commit", all_calls)
                    self.assertFalse((credentials / ".credential-transaction").exists())
                    self.assertFalse((credentials / ".credential-transaction-committed").exists())

        real_mv = shutil.which("mv")
        real_rm = shutil.which("rm")
        self.assertIsNotNone(real_mv)
        self.assertIsNotNone(real_rm)
        for credential_cleanup_failure in ("move", "remove"):
            with self.subTest(credential_cleanup_failure=credential_cleanup_failure), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                binary = root / "bin"
                binary.mkdir()
                project = f"schemii-recovery-credential-cleanup-{credential_cleanup_failure}"
                credentials = root / "credentials"
                source = root / "backup"
                write_credentials(credentials, project, "a")
                write_credentials(source / "credentials", project, "b")
                (source / "instance").write_text(f"{project}\n", encoding="utf-8")
                docker = binary / "docker"
                docker.write_text(docker_source, encoding="utf-8")
                docker.chmod(0o755)
                wrapper = f'''#!/bin/sh
tool=${{0##*/}}
case "$tool:${{SCHEMII_TEST_CREDENTIAL_CLEANUP_FAILURE:-}}:$*" in
  mv:move:*".credential-transaction "*".credential-transaction-committed") exit 71 ;;
  rm:remove:*".credential-transaction-committed") exit 71 ;;
esac
case "$tool" in
  mv) exec {real_mv} "$@" ;;
  rm) exec {real_rm} "$@" ;;
esac
exit 127
'''
                for name in ("mv", "rm"):
                    path = binary / name
                    path.write_text(wrapper, encoding="utf-8")
                    path.chmod(0o755)
                base_env = {
                    **os.environ,
                    "PATH": f"{binary}:/usr/bin:/bin",
                    "DOCKER_LOG": str(root / "docker.log"),
                    "SCHEMII_TEST_RECOVERY_STATE_FILE": str(root / "recovery.state"),
                    "SCHEMII_INSTANCE": project,
                    "SCHEMII_CREDENTIAL_DIR": str(credentials),
                    "SCHEMII_TEST_FAIL_MATCH": "never-match",
                }
                failed = subprocess.run(
                    ["/bin/bash", str(ROOT / "start.sh"), "instance-restore", str(source), f"RESTORE:{project}"],
                    cwd=ROOT,
                    env={**base_env, "SCHEMII_TEST_CREDENTIAL_CLEANUP_FAILURE": credential_cleanup_failure},
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
                self.assertNotEqual(failed.returncode, 0, failed.stdout + failed.stderr)
                self.assertNotIn("metadata-recovery rollback", (root / "docker.log").read_text(encoding="utf-8"))
                self.assertTrue((root / "recovery.state").is_file())

                restarted = subprocess.run(
                    ["/bin/bash", str(ROOT / "start.sh"), "instance-restore", str(source), f"RESTORE:{project}"],
                    cwd=ROOT,
                    env=base_env,
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
                self.assertEqual(restarted.returncode, 0, restarted.stdout + restarted.stderr)
                calls = (root / "docker.log").read_text(encoding="utf-8")
                self.assertEqual(calls.count("metadata-recovery restore\n"), 1)
                self.assertNotIn("metadata-recovery rollback", calls)
                self.assertFalse((credentials / ".credential-transaction").exists())
                self.assertFalse((credentials / ".credential-transaction-committed").exists())

    @unittest.skipIf(os.name == "nt", "POSIX credential mutation failures are tested on POSIX runners")
    def test_shell_credential_mutation_commands_fail_explicitly(self):
        credential_names = (
            "metadata_bootstrap_password", "metadata_migration_password",
            "metadata_schemii_password", "metadata_schemer_password", "opencode_password",
        )
        real_tools = {name: shutil.which(name) for name in ("mktemp", "cp", "chmod", "mv", "rm")}
        self.assertTrue(all(real_tools.values()))

        def write_credentials(directory, project, value):
            directory.mkdir(parents=True, mode=0o700)
            (directory / "instance").write_text(f"{project}\n", encoding="utf-8")
            (directory / "instance").chmod(0o600)
            for name in credential_names:
                path = directory / name
                path.write_text(value * 32 + "\n", encoding="utf-8")
                path.chmod(0o600)

        for failure in ("mktemp", "write", "copy", "chmod-temp", "chmod-target", "remove", "move"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                binary = root / "bin"
                binary.mkdir()
                project = f"schemii-credential-{failure}"
                credentials = root / "credentials"
                source = root / "backup"
                write_credentials(credentials, project, "a")
                write_credentials(source / project, project, "b")
                docker = binary / "docker"
                docker.write_text(
                    "#!/bin/sh\n"
                    "case \"$*\" in\n"
                    "  info|\"compose version\") exit 0 ;;\n"
                    "  \"volume inspect ${SCHEMII_INSTANCE}_schemii-metadata-postgres\")\n"
                    "    [ \"${SCHEMII_TEST_MUTATION_FAILURE:-}\" = move ] && exit 0 || exit 1 ;;\n"
                    "  \"ps -q \"*\"com.docker.compose.service=metadata-postgres\"*) printf 'metadata-container\\n'; exit 0 ;;\n"
                    "  \"run --rm python:3.12-slim python -c \"*) printf '%064d\\n' 0; exit 0 ;;\n"
                    "  ps*|inspect*|start*|restart*|exec*) exit 0 ;;\n"
                    "esac\n"
                    "exit 0\n",
                    encoding="utf-8",
                )
                docker.chmod(0o755)
                wrapper = r"""#!/bin/sh
tool=${0##*/}
last=
for argument do last=$argument; done
case "$tool:$SCHEMII_TEST_MUTATION_FAILURE:$*" in
  mktemp:mktemp:*\/.credential.XXXXXX) exit 71 ;;
  mktemp:write:*\/.credential.XXXXXX)
    printf '%s\n' "$SCHEMII_CREDENTIAL_DIR/missing/.credential.test"
    exit 0 ;;
  cp:copy:*\/.credential.*)
    exit 71 ;;
  chmod:chmod-temp:*\/.credential.*)
    exit 71 ;;
  chmod:chmod-target:*)
    if [ -f "$SCHEMII_TEST_COPY_COMPLETED" ] && [ "$last" = "$SCHEMII_CREDENTIAL_DIR/metadata_bootstrap_password" ]; then exit 71; fi ;;
  rm:remove:*\/.credential.*)
    exit 71 ;;
  mv:move:*\.credential-transaction)
    exit 71 ;;
esac
if [ "$tool" = cp ] && [ "$SCHEMII_TEST_MUTATION_FAILURE" = chmod-target ]; then
  "$SCHEMII_TEST_REAL_CP" "$@" || exit $?
  : > "$SCHEMII_TEST_COPY_COMPLETED"
  exit 0
fi
case "$tool" in
  mktemp) exec "$SCHEMII_TEST_REAL_MKTEMP" "$@" ;;
  cp) exec "$SCHEMII_TEST_REAL_CP" "$@" ;;
  chmod) exec "$SCHEMII_TEST_REAL_CHMOD" "$@" ;;
  mv) exec "$SCHEMII_TEST_REAL_MV" "$@" ;;
  rm) exec "$SCHEMII_TEST_REAL_RM" "$@" ;;
esac
exit 127
"""
                for name in real_tools:
                    path = binary / name
                    path.write_text(wrapper, encoding="utf-8")
                    path.chmod(0o755)
                command = ["/bin/bash", str(ROOT / "start.sh")]
                if failure == "move":
                    command.append("credentials-rotate")
                else:
                    command.extend(("credentials-restore", str(source)))
                result = subprocess.run(
                    command,
                    cwd=ROOT,
                    env={
                        **os.environ,
                        "PATH": f"{binary}:/usr/bin:/bin",
                        "SCHEMII_INSTANCE": project,
                        "SCHEMII_CREDENTIAL_DIR": str(credentials),
                        "SCHEMII_TEST_MUTATION_FAILURE": failure,
                        "SCHEMII_TEST_COPY_COMPLETED": str(root / "copy-completed"),
                        **{f"SCHEMII_TEST_REAL_{name.upper()}": path for name, path in real_tools.items()},
                    },
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                if failure in {"mktemp", "write", "copy", "chmod-temp", "move"}:
                    self.assertEqual(
                        (credentials / "metadata_bootstrap_password").read_text(encoding="utf-8"),
                        "a" * 32 + "\n",
                    )
                if failure in {"copy", "chmod-target", "remove"}:
                    self.assertTrue(any(path.name.startswith(".credential.") for path in credentials.iterdir()))

    def test_windows_credential_acls_are_recursive_verified_and_fail_closed(self):
        powershell = (ROOT / "start.ps1").read_text(encoding="utf-8")

        self.assertIn("WindowsIdentity]::GetCurrent().User", powershell)
        self.assertIn("SetAccessRuleProtection($true, $false)", powershell)
        self.assertIn("Set-Acl -LiteralPath", powershell)
        self.assertIn("Get-Acl -LiteralPath", powershell)
        self.assertIn("Credential ACL verification failed closed", powershell)
        self.assertIn("Protect-CredentialTree $credentialDirectory", powershell)
        self.assertIn("Protect-CredentialTree $backupDirectory", powershell)
        self.assertNotIn("Protect-CredentialTree $sourceDirectory", powershell)
        self.assertIn("[System.IO.FileMode]::CreateNew", powershell)
        self.assertEqual(powershell.count("[System.IO.FileMode]::OpenOrCreate"), 1)
        self.assertIn("$credentialLockPath, [System.IO.FileMode]::OpenOrCreate", powershell)
        self.assertIn("Copy-ProtectedRestoreSource $sourceDirectory", powershell)
        self.assertIn(".restore-source.", powershell)
        self.assertIn("Protect-CredentialPath $staging $true", powershell)
        self.assertNotIn("icacls.exe", powershell)

    def test_container_secret_consumers_enforce_one_credential_format(self):
        paths = (
            ROOT / "docker/metadata/001_roles.sh",
            ROOT / "docker/metadata/secret-entrypoint.sh",
            ROOT / "docker/runtime-secret-entrypoint.sh",
            ROOT / "ai/secret-entrypoint.sh",
        )
        for path in paths:
            source = path.read_text(encoding="utf-8")
            self.assertIn("[!A-Za-z0-9_-]", source)
            self.assertIn('"${#', source)

    def test_compose_allows_a_clean_browser_shutdown_to_remain_stopped(self):
        compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")

        schemii_service = compose.split("  schemii:\n", 1)[1].split("\n  schemii-ingress:", 1)[0]
        self.assertIn("restart: on-failure", schemii_service)
        self.assertNotIn("restart: unless-stopped", schemii_service)

    def test_schemer_is_a_separate_service_with_shared_profiles(self):
        compose = (ROOT / "compose.schemer.yaml").read_text(encoding="utf-8")
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        package = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

        self.assertNotIn("target:", compose)
        self.assertIn('127.0.0.1:${SCHEMER_HOST_PORT:-8081}:8080', compose)
        schemer_service = compose.split("  schemer:\n", 1)[1].split("\n  schemer-ingress:", 1)[0]
        self.assertNotIn("    ports:", schemer_service)
        self.assertIn("schemii-config:/data/config", compose)
        self.assertIn("schemer-dashboards:/data/dashboards", compose)
        self.assertIn("SCHEMER_DASHBOARD_DIR: /data/dashboards", compose)
        self.assertNotIn("schemer-runtime", dockerfile)
        self.assertIn('image: ${SCHEMII_IMAGE:-schemii:local}', compose)
        self.assertIn('command: ["schemer"]', compose)
        self.assertIn("/data/config /data/schemas /data/dashboards", dockerfile)
        self.assertIn('schemer = "schemii.schemer_server:main"', package)

    def test_container_runtime_is_cross_platform_and_self_checking(self):
        compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
        shell = (ROOT / "start.sh").read_text(encoding="utf-8")
        powershell = (ROOT / "start.ps1").read_text(encoding="utf-8")

        self.assertIn("healthcheck:", compose)
        self.assertIn("urllib.request.urlopen", compose)
        self.assertNotIn("host-gateway", compose)
        self.assertIn(".State.Health.Status", shell)
        self.assertIn(".State.Health.Status", powershell)
        self.assertIn("docker run --rm python:3.12-slim", shell)
        self.assertIn("RandomNumberGenerator]::Fill", powershell)
        self.assertIn("[Convert]::ToHexString", powershell)

    def test_ai_navigation_tools_accept_only_logical_ids_and_public_labels(self):
        tools = "\n".join(path.read_text(encoding="utf-8") for path in sorted((ROOT / "ai" / "workspace" / ".opencode" / "tools").glob("schema_*_open.ts")))
        instructions = (ROOT / "ai" / "workspace" / "AGENTS.md").read_text(encoding="utf-8")

        self.assertIn("schemaId", tools)
        self.assertNotRegex(tools, r"\b(?:password|path|url|host|shell|command)\b")
        project_create = (ROOT / "ai" / "workspace" / ".opencode" / "tools" / "schema_project_create.ts").read_text(encoding="utf-8")
        self.assertNotRegex(project_create, r"\b(?:password|path|url|host|shell|command|schemaId)\b")

    def test_ai_schema_mutation_tools_return_fixed_acknowledgements(self):
        tool_dir = ROOT / "ai" / "workspace" / ".opencode" / "tools"
        for name in ("schema_populate.ts", "schema_add_table.ts", "schema_add_relationship.ts"):
            source = (tool_dir / name).read_text(encoding="utf-8")
            self.assertIn('return "Proposal arguments received."', source)
            self.assertNotIn("SCHEMII_ACTION:", source)
            self.assertNotRegex(source, r"\b(?:password|path|url|host|shell|command)\b")


if __name__ == "__main__":
    unittest.main()
