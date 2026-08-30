import json
import os
import socket
import subprocess
import time
import unittest
import urllib.error
import urllib.request
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NGINX_IMAGE = "nginx:1.29.1-alpine@sha256:42a516af16b852e33b7682d5ef8acbd5d13fe08fecadc7ed98605ba5e3b26ab8"
CURL_IMAGE = "curlimages/curl:8.15.0@sha256:4026b29997dc7c823b51c164b71e2b51e0fd95cce4601f78202c513d97da2922"


@unittest.skipUnless(os.environ.get("SCHEMII_RUN_DOCKER_INTEGRATION") == "1", "Docker integration is opt-in")
class IngressSecurityIntegrationTests(unittest.TestCase):
    def run_docker(self, *arguments, check=True):
        return subprocess.run(
            ["docker", *arguments], cwd=ROOT, capture_output=True, text=True, check=check, timeout=120,
        )

    def setUp(self):
        self.name = f"schemii-ingress-test-{uuid.uuid4().hex[:10]}"
        self.dependency_network = f"{self.name}-dependency"
        self.ingress_network = f"{self.name}-ingress"
        self.loopback_network = f"{self.name}-loopback"
        self.backend = f"{self.name}-app"
        self.ingress = f"{self.name}-local-proxy"
        self.ingress_identity = "schemii-ingress"
        self.addCleanup(self.cleanup_docker)
        self.application_image = os.environ.get("SCHEMII_IMAGE", "schemii:local")
        if self.run_docker("image", "inspect", self.application_image, check=False).returncode != 0:
            self.skipTest(f"application image is not loaded: {self.application_image}")
        self.run_docker("network", "create", self.dependency_network)
        self.run_docker("network", "create", "--internal", self.ingress_network)
        self.run_docker("network", "create", self.loopback_network)
        fixture = ROOT / "tests/fixtures/ingress_app.py"
        self.run_docker(
            "run", "-d", "--name", self.backend,
            "--network", self.dependency_network,
            "--network-alias", "schemii-app",
            "--read-only", "--tmpfs", "/tmp:size=32m,mode=1777",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
            "--user", "10001:10001", "--entrypoint", "python",
            "-e", "SCHEMII_BEHIND_LOOPBACK_PROXY=1",
            "-e", f"SCHEMII_TRUSTED_LOCAL_PROXY={self.ingress_identity}",
            "-e", "SCHEMII_PUBLIC_ORIGINS=https://app.example.invalid",
            "-v", f"{fixture}:/fixture/ingress_app.py:ro",
            self.application_image, "/fixture/ingress_app.py",
        )
        self.run_docker("network", "connect", "--alias", "schemii", self.ingress_network, self.backend)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            self.port = listener.getsockname()[1]
        self.run_docker(
            "create", "--name", self.ingress,
            "--network", self.loopback_network,
            "-p", f"127.0.0.1:{self.port}:8080",
            "--entrypoint", "nginx", "--user", "101:101", "--read-only",
            "--tmpfs", "/tmp:size=24m,mode=1777", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges:true",
            "-v", f"{ROOT / 'docker/ingress/nginx.conf'}:/etc/nginx/nginx.conf:ro",
            "-v", f"{ROOT / 'docker/ingress/schemii-upstream.conf'}:/etc/nginx/application-upstream.conf:ro",
            NGINX_IMAGE, "-g", "daemon off;", "-c", "/etc/nginx/nginx.conf",
        )
        self.run_docker(
            "network", "connect", "--alias", self.ingress_identity,
            self.ingress_network, self.ingress,
        )
        self.run_docker("start", self.ingress)
        port_output = self.run_docker("port", self.ingress, "8080/tcp").stdout.strip()
        self.assertEqual(int(port_output.rsplit(":", 1)[1]), self.port)
        self.base_url = f"http://127.0.0.1:{self.port}"
        for _ in range(50):
            try:
                with urllib.request.urlopen(f"{self.base_url}/api/readiness", timeout=1) as response:
                    if response.status == 200:
                        break
            except urllib.error.HTTPError as error:
                error.close()
                time.sleep(0.1)
            except (OSError, urllib.error.URLError):
                time.sleep(0.1)
        else:
            self.fail("ingress fixture did not become ready")
    def cleanup_docker(self):
        for container in (getattr(self, "ingress", ""), getattr(self, "backend", "")):
            if container:
                self.run_docker("rm", "-f", container, check=False)
        for network in (
            getattr(self, "ingress_network", ""), getattr(self, "loopback_network", ""),
            getattr(self, "dependency_network", ""),
        ):
            if network:
                self.run_docker("network", "rm", network, check=False)

    def curl_from(self, network, url, *headers, name=None):
        arguments = ["run", "--rm", "--network", network]
        if name:
            arguments.extend(("--name", name))
        arguments.extend((CURL_IMAGE, "-sS", "-o", "/dev/null", "-w", "%{http_code}"))
        for header in headers:
            arguments.extend(("-H", header))
        arguments.append(url)
        result = self.run_docker(*arguments, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        return int(result.stdout)

    def test_trusted_ingress_accepts_public_origin_and_excludes_dependency_cotenants(self):
        with urllib.request.urlopen(f"{self.base_url}/") as response:
            self.assertEqual(response.read(), b"trusted ingress fixture")
        with urllib.request.urlopen(f"{self.base_url}/api/session") as response:
            self.assertEqual(json.load(response)["serverId"], "ingress-fixture")

        self.assertEqual(self.curl_from(
            self.dependency_network, "http://schemii-app:8080/", "Host: localhost",
        ), 403)
        public_headers = (
            "Host: app.example.invalid",
            "Origin: https://app.example.invalid",
            "X-Forwarded-Host: app.example.invalid",
            "X-Forwarded-Proto: https",
        )
        self.assertEqual(self.curl_from(
            self.dependency_network, "http://schemii-app:8080/", *public_headers,
        ), 403)
        public_request = urllib.request.Request(f"{self.base_url}/", headers={
            name: value for name, value in (header.split(": ", 1) for header in public_headers)
        })
        with urllib.request.urlopen(public_request) as response:
            self.assertEqual(response.status, 200)

        unknown = urllib.request.Request(f"{self.base_url}/", headers={"X-Forwarded-Untrusted": "spoofed"})
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(unknown)
        self.assertEqual(error.exception.code, 403)
        error.exception.close()

        backend_details = json.loads(self.run_docker("inspect", self.backend).stdout)[0]
        self.assertFalse(backend_details["HostConfig"]["PortBindings"])
        ingress_members = json.loads(self.run_docker("network", "inspect", self.ingress_network).stdout)[0]["Containers"]
        self.assertEqual(
            {details["Name"] for details in ingress_members.values()},
            {self.backend, self.ingress},
        )
        loopback_members = json.loads(self.run_docker("network", "inspect", self.loopback_network).stdout)[0]["Containers"]
        self.assertEqual({details["Name"] for details in loopback_members.values()}, {self.ingress})

    def test_stream_download_and_bounded_large_body_cross_ingress_unchanged(self):
        with urllib.request.urlopen(f"{self.base_url}/api/stream", timeout=5) as response:
            self.assertEqual(response.headers.get_content_type(), "application/x-ndjson")
            self.assertEqual(
                [json.loads(line) for line in response],
                [{"sequence": 0}, {"sequence": 1}, {"sequence": 2}],
            )
        with urllib.request.urlopen(f"{self.base_url}/api/download") as response:
            self.assertEqual(response.headers["Content-Disposition"], 'attachment; filename="fixture.txt"')
            self.assertEqual(response.read(), b"fixture-download\n")

        body = json.dumps({"value": "a" * (6 * 1024 * 1024)}, separators=(",", ":")).encode("ascii")
        request = urllib.request.Request(
            f"{self.base_url}/api/body", data=body, headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            self.assertEqual(json.load(response)["bytes"], len(body))
