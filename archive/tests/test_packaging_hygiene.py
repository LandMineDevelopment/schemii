import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


class PackagingHygieneTests(unittest.TestCase):
    def test_package_configuration_uses_src_and_includes_runtime_assets(self):
        configuration = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('package-dir = {"" = "src"}', configuration)
        self.assertIn('where = ["src"]', configuration)
        for assets in ("web/*", "schemer_web/*", "shared_web/*", "metadata/migrations/*.sql"):
            self.assertIn(f'"{assets}"', configuration)

    def test_build_and_dist_do_not_contain_stale_importable_or_web_copies(self):
        stale = []
        for output_name in ("build", "dist"):
            output = ROOT / output_name
            if not output.exists():
                continue
            stale.extend(path for path in output.rglob("__init__.py"))
            stale.extend(path for path in output.rglob("app.js") if path.parent.name in {"web", "schemer_web"})
        self.assertEqual(
            stale, [],
            "Generated build/dist copies must not remain in the source tree: " + ", ".join(str(path.relative_to(ROOT)) for path in stale),
        )


if __name__ == "__main__":
    unittest.main()
