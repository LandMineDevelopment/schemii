import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from schemii.server_runtime import begin_http_shutdown, parse_port, parse_proxy_setting, postgres_runtime_config, run_server, validate_static_directory


class ServerRuntimeTests(unittest.TestCase):
    def test_environment_parsers_are_strict(self):
        self.assertTrue(parse_proxy_setting("1", "PROXY"))
        self.assertFalse(parse_proxy_setting("0", "PROXY"))
        self.assertEqual(parse_port("8080", "PORT"), 8080)
        for value in ("yes", ""):
            with self.subTest(proxy=value), self.assertRaises(SystemExit):
                parse_proxy_setting(value, "PROXY")
        for value in ("nope", "0", "65536"):
            with self.subTest(port=value), self.assertRaises(SystemExit):
                parse_port(value, "PORT")

    def test_postgres_runtime_configuration_is_independent_and_strict(self):
        config = postgres_runtime_config({
            "SCHEMII_POSTGRES_GLOBAL_CAPACITY": "20",
            "SCHEMII_POSTGRES_TARGET_CAPACITY": "3",
            "SCHEMII_POSTGRES_READ_CAPACITY": "7",
            "SCHEMII_MIGRATION_PLAN_TTL_SECONDS": "41",
            "SCHEMII_TEMPORAL_MANIFEST_TTL_SECONDS": "13",
            "SCHEMII_CONSOLE_TRANSACTION_MAXIMUM": "6",
            "SCHEMII_CONSOLE_TRANSACTION_IDLE_SECONDS": "17",
            "SCHEMII_CONSOLE_TRANSACTION_LIFETIME_SECONDS": "43",
        })
        self.assertEqual(config.global_capacity, 20)
        self.assertEqual(config.target_capacity, 3)
        self.assertEqual(config.class_capacities["read"], 7)
        self.assertEqual(config.migration_plan_ttl_seconds, 41)
        self.assertEqual(config.temporal_manifest_ttl_seconds, 13)
        self.assertEqual((config.console_transaction_maximum, config.console_transaction_idle_seconds,
                          config.console_transaction_lifetime_seconds), (6, 17, 43))
        for variable, value in (
            ("SCHEMII_POSTGRES_GLOBAL_CAPACITY", "0"),
            ("SCHEMII_POSTGRES_CATALOG_CAPACITY", "1.5"),
            ("SCHEMII_MIGRATION_PLAN_TTL_SECONDS", "+2"),
            ("SCHEMII_TEMPORAL_MANIFEST_TTL_SECONDS", ""),
            ("SCHEMII_CONSOLE_TRANSACTION_MAXIMUM", "65"),
            ("SCHEMII_CONSOLE_TRANSACTION_IDLE_SECONDS", "86401"),
            ("SCHEMII_CONSOLE_TRANSACTION_LIFETIME_SECONDS", "604801"),
        ):
            with self.subTest(variable=variable), self.assertRaises(SystemExit):
                postgres_runtime_config({variable: value})
        with self.assertRaises(SystemExit):
            postgres_runtime_config({
                "SCHEMII_POSTGRES_GLOBAL_CAPACITY": "4",
                "SCHEMII_POSTGRES_TARGET_CAPACITY": "4",
            })
        with self.assertRaises(SystemExit):
            postgres_runtime_config({
                "SCHEMII_CONSOLE_TRANSACTION_IDLE_SECONDS": "11",
                "SCHEMII_CONSOLE_TRANSACTION_LIFETIME_SECONDS": "10",
            })

    def test_static_directory_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            validate_static_directory(Path(directory))
            with self.assertRaises(SystemExit):
                validate_static_directory(Path(directory) / "missing")

    def test_server_is_announced_and_closed_on_keyboard_interrupt(self):
        events = []

        class Server:
            def __init__(self, address, handler):
                events.append((address, handler))

            def serve_forever(self):
                raise KeyboardInterrupt

            def server_close(self):
                events.append("closed")

        with patch("sys.stdout", new_callable=io.StringIO) as output:
            run_server("127.0.0.1", 8080, object, "Demo", server_factory=Server)
        self.assertIn("Demo running at http://127.0.0.1:8080/", output.getvalue())
        self.assertEqual(events[-1], "closed")

    def test_server_runs_shutdown_callback_before_close(self):
        events = []

        class Server:
            def __init__(self, address, handler):
                pass

            def serve_forever(self):
                raise KeyboardInterrupt

            def server_close(self):
                events.append("closed")

        run_server(
            "127.0.0.1", 8080, object, "Demo", server_factory=Server,
            shutdown_callback=lambda: events.append("shutdown"),
        )
        self.assertEqual(events, ["shutdown", "closed"])

    def test_server_starts_and_stops_lifecycle_service_around_serving(self):
        events = []

        class Lifecycle:
            def start(self): events.append("start")
            def close(self): events.append("stop")

        class Server:
            def __init__(self, address, handler): pass
            def serve_forever(self): events.append("serve"); raise KeyboardInterrupt
            def server_close(self): events.append("close")

        run_server("127.0.0.1", 8080, object, "Demo", server_factory=Server, lifecycle_services=(Lifecycle(),))
        self.assertEqual(events, ["start", "serve", "stop", "close"])

    def test_shutdown_response_is_flushed_before_thread_start(self):
        events = []

        class Writer:
            def flush(self):
                events.append("flush")

        class Server:
            def shutdown(self):
                events.append("shutdown")

        class Handler:
            server = Server()
            wfile = Writer()

            def send_json(self, status, payload):
                events.append((status, payload))

        begin_http_shutdown(Handler(), "test-shutdown")
        self.assertEqual(events[:2], [(202, {"shuttingDown": True}), "flush"])


if __name__ == "__main__":
    unittest.main()
