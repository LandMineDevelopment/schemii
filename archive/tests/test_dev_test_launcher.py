import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DevTestLauncherTests(unittest.TestCase):
    def test_launcher_is_external_synthetic_and_uses_only_approved_seed(self):
        source = (ROOT / "scripts/dev-test.py").read_text(encoding="utf-8")
        self.assertIn('"synthetic-development-and-test-only"', source)
        self.assertIn('"examples/postgres/001_bookstore.sql"', source)
        self.assertNotIn("postgres_profiles.json", source)
        self.assertIn("must be outside the repository", source)
        self.assertIn("Refusing to reset unmarked directory", source)
        self.assertNotIn('["docker"', source)

    def test_launcher_has_no_nonstdlib_import_requirement(self):
        spec = importlib.util.spec_from_file_location("dev_test_launcher", ROOT / "scripts/dev-test.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(module.SEED.resolve(), (ROOT / "examples/postgres/001_bookstore.sql").resolve())


if __name__ == "__main__":
    unittest.main()
