import sys
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from schemii.http_access import HttpAccessPolicy, PublicOrigin, http_access_policy, request_is_allowed
from schemii.http_common import make_local_app_handler
from schemii.readiness import readiness_report
from tests.http_test_support import FakePostgresService, RunningHttpServer


def request_headers(*pairs):
    headers = Message()
    for name, value in pairs:
        headers.add_header(name, value)
    return headers


class HttpAccessPolicyTests(unittest.TestCase):
    @staticmethod
    def resolved_as(address):
        family = 10 if ":" in address else 2
        return patch(
            "schemii.http_access.socket.getaddrinfo",
            return_value=[(family, 1, 6, "", (address, 0))],
        )

    def test_configuration_is_explicit_strict_and_canonical(self):
        policy = http_access_policy({
            "SCHEMII_BEHIND_LOOPBACK_PROXY": "1",
            "SCHEMII_TRUSTED_LOCAL_PROXY": "schemii-ingress",
            "SCHEMII_PUBLIC_ORIGINS": "https://app.example.invalid:9443,https://alternate.example.invalid:443",
        }, "SCHEMII")
        self.assertEqual(policy.public_origins, (
            PublicOrigin("app.example.invalid", 9443),
            PublicOrigin("alternate.example.invalid", 443),
        ))

        invalid_values = (
            "https://*.example.invalid",
            "http://app.example.invalid",
            "https://app.example.invalid/",
            "https://user@app.example.invalid",
            "https://app.example.invalid?query=1",
            "https://app.example.invalid#fragment",
            "https://app.example.invalid,,https://other.example.invalid",
            "https://app.example.invalid,https://app.example.invalid:443",
            " https://app.example.invalid",
        )
        for value in invalid_values:
            with self.subTest(value=value), self.assertRaises(SystemExit):
                http_access_policy({
                    "SCHEMII_BEHIND_LOOPBACK_PROXY": "1",
                    "SCHEMII_TRUSTED_LOCAL_PROXY": "schemii-ingress",
                    "SCHEMII_PUBLIC_ORIGINS": value,
                }, "SCHEMII")

    def test_proxy_mode_peer_and_public_origins_must_agree(self):
        environments = (
            {"SCHEMER_PUBLIC_ORIGINS": "https://app.example.invalid"},
            {
                "SCHEMER_BEHIND_LOOPBACK_PROXY": "1",
                "SCHEMER_PUBLIC_ORIGINS": "https://app.example.invalid",
            },
            {"SCHEMER_BEHIND_LOOPBACK_PROXY": "1"},
            {"SCHEMER_TRUSTED_LOCAL_PROXY": "schemer-ingress"},
        )
        for environment in environments:
            with self.subTest(environment=environment), self.assertRaises(SystemExit):
                http_access_policy(environment, "SCHEMER")
        for value in ("Local-Ingress", "127.0.0.1", "local.ingress", " local-ingress"):
            with self.subTest(peer=value), self.assertRaises(SystemExit):
                http_access_policy({
                    "SCHEMER_BEHIND_LOOPBACK_PROXY": "1",
                    "SCHEMER_TRUSTED_LOCAL_PROXY": value,
                }, "SCHEMER")

        policy = http_access_policy({
            "SCHEMER_BEHIND_LOOPBACK_PROXY": "1",
            "SCHEMER_TRUSTED_LOCAL_PROXY": "schemer-ingress",
        }, "SCHEMER")
        self.assertEqual(policy.public_origins, ())

    def test_loopback_only_requests_reject_forwarded_headers(self):
        local = request_headers(("Host", "localhost:8080"))
        forwarded = request_headers(
            ("Host", "localhost:8080"),
            ("X-Forwarded-Host", "app.example.invalid"),
            ("X-Forwarded-Proto", "https"),
        )
        self.assertTrue(request_is_allowed("127.0.0.1", local, HttpAccessPolicy()))
        self.assertFalse(request_is_allowed("127.0.0.1", forwarded, HttpAccessPolicy()))

    def test_local_origin_must_match_the_loopback_authority(self):
        policy = HttpAccessPolicy(behind_loopback_proxy=True, trusted_local_proxy="local-ingress")

        def allowed(host, origin):
            return request_is_allowed(
                "172.30.0.2", request_headers(("Host", host), ("Origin", origin)), policy,
            )

        with self.resolved_as("172.30.0.2"):
            self.assertTrue(allowed("localhost:8080", "http://localhost:8080"))
            self.assertTrue(allowed("[::1]:8080", "http://[::1]:8080"))
            self.assertFalse(allowed("localhost:8080", "http://localhost:8081"))
            self.assertFalse(allowed("localhost:8080", "http://127.0.0.1:8080"))
            self.assertFalse(allowed("localhost:8080", "https://localhost:8080"))
            self.assertFalse(allowed("localhost:8080", "https://localhost"))
            self.assertFalse(request_is_allowed(
                "172.30.0.2", request_headers(("Host", "localhost:0")), policy,
            ))

    def test_proxy_peer_resolution_fails_closed_on_failure_ambiguity_and_wrong_source(self):
        policy = HttpAccessPolicy(behind_loopback_proxy=True, trusted_local_proxy="local-ingress")
        headers = request_headers(("Host", "localhost:8080"))
        with patch("schemii.http_access.socket.getaddrinfo", side_effect=OSError("DNS unavailable")):
            self.assertFalse(request_is_allowed("172.30.0.2", headers, policy))
        with patch("schemii.http_access.socket.getaddrinfo", return_value=[
            (2, 1, 6, "", ("172.30.0.2", 0)),
            (2, 1, 6, "", ("172.30.0.3", 0)),
        ]):
            self.assertFalse(request_is_allowed("172.30.0.2", headers, policy))
        with self.resolved_as("172.30.0.3"):
            self.assertFalse(request_is_allowed("172.30.0.2", headers, policy))

    def test_public_origin_request_requires_exact_standard_forwarding_headers(self):
        policy = HttpAccessPolicy(
            behind_loopback_proxy=True,
            trusted_local_proxy="local-ingress",
            public_origins=(PublicOrigin("app.example.invalid", 9443),),
        )

        def allowed(*pairs):
            return request_is_allowed("172.30.0.2", request_headers(*pairs), policy)

        required = (
            ("Host", "app.example.invalid:9443"),
            ("Origin", "https://app.example.invalid:9443"),
            ("X-Forwarded-Host", "app.example.invalid:9443"),
            ("X-Forwarded-Proto", "https"),
        )
        with self.resolved_as("172.30.0.2"):
            self.assertTrue(allowed(*required))
            self.assertTrue(allowed(*(pair for pair in required if pair[0] != "Origin")))
            for required_header in ("X-Forwarded-Host", "X-Forwarded-Proto"):
                with self.subTest(missing=required_header):
                    self.assertFalse(allowed(*(pair for pair in required if pair[0] != required_header)))
            for duplicate in required:
                with self.subTest(duplicate=duplicate[0]):
                    self.assertFalse(allowed(*required, duplicate))
            self.assertFalse(allowed(*required, ("X-Forwarded-For", "192.0.2.1")))
            self.assertFalse(allowed(*required, ("Forwarded", "proto=https")))
            self.assertFalse(allowed(*(
                (name, "https://other.example.invalid" if name == "Origin" else value)
                for name, value in required
            )))
            self.assertFalse(allowed(*(
                (name, "http" if name == "X-Forwarded-Proto" else value)
                for name, value in required
            )))
            self.assertFalse(allowed(*(
                (name, "other.example.invalid" if name == "Host" else value)
                for name, value in required
            )))
            self.assertFalse(allowed(*(
                (name, "other.example.invalid" if name == "X-Forwarded-Host" else value)
                for name, value in required
            )))
        with self.resolved_as("172.30.0.3"):
            self.assertFalse(allowed(*required))

    def test_admission_runs_before_static_and_api_routes(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            web_dir = Path(temporary_directory)
            (web_dir / "index.html").write_text("private", encoding="utf-8")
            base_handler = make_local_app_handler(
                web_dir,
                FakePostgresService(),
                "session-token",
                server_id="server-id",
                access_policy=HttpAccessPolicy(
                    behind_loopback_proxy=True,
                    trusted_local_proxy="local-ingress",
                    public_origins=(PublicOrigin("app.example.invalid", 9443),),
                ),
            )

            class Handler(base_handler):
                def do_GET(self):
                    if not self._handle_common_get(urlparse(self.path).path):
                        super().do_GET()

            server = RunningHttpServer(Handler)
            try:
                public_headers = {
                    "Host": "app.example.invalid:9443",
                    "Origin": "https://app.example.invalid:9443",
                    "X-Forwarded-Host": "app.example.invalid:9443",
                    "X-Forwarded-Proto": "https",
                }
                with patch("schemii.http_access._proxy_peer_matches", return_value=True):
                    self.assertEqual(server.request("/", headers={"Host": "app.example.invalid:9443"})[0], 403)
                    self.assertEqual(server.request("/", headers=public_headers)[0], 200)
                    self.assertEqual(server.request("/api/session", headers=public_headers)[0], 200)
            finally:
                server.close()

    def test_readiness_reports_configuration_without_claiming_external_reachability(self):
        class MetadataAuthority:
            @staticmethod
            def health():
                return {"ok": True}

        status, report = readiness_report(
            MetadataAuthority(), None, FakePostgresService(),
            access_policy=HttpAccessPolicy(
                behind_loopback_proxy=True,
                trusted_local_proxy="local-ingress",
                public_origins=(
                    PublicOrigin("app.example.invalid", 443),
                    PublicOrigin("alternate.example.invalid", 9443),
                ),
            ),
        )

        self.assertEqual(status, 200)
        self.assertEqual(report["components"]["httpAccess"], {
            "required": True,
            "status": "available",
            "mode": "public-origin",
            "behindLoopbackProxy": True,
            "publicOrigins": [
                "https://app.example.invalid",
                "https://alternate.example.invalid:9443",
            ],
        })


if __name__ == "__main__":
    unittest.main()
