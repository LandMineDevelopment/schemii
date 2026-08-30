import tempfile
import unittest
from pathlib import Path
from unittest import mock

from schemii import build_identity as identity_module


ROOT = Path(__file__).resolve().parents[1]


class BuildIdentityTests(unittest.TestCase):
    def test_development_identity_uses_package_version(self):
        self.assertEqual(identity_module.build_identity(), {
            "version": (ROOT / "VERSION").read_text(encoding="utf-8").strip(),
            "revision": "development",
        })

    def test_invalid_packaged_revision_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            revision = Path(temporary) / "revision"
            revision.write_text("branch-name\n", encoding="utf-8")
            with mock.patch.object(identity_module, "_REVISION_FILE", revision):
                with self.assertRaisesRegex(RuntimeError, "build revision is invalid"):
                    identity_module.build_identity()


if __name__ == "__main__":
    unittest.main()
