import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from schemii.atomic_json import write_json


class AtomicJsonTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.destination = self.root / "record.json"

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_writes_json_newline_mode_and_leaves_no_temporary_file(self):
        write_json(self.destination, {"b": 1, "a": 2}, mode=0o600, sort_keys=True)
        self.assertEqual(self.destination.read_text(encoding="utf-8"), '{\n  "a": 2,\n  "b": 1\n}\n')
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(self.destination.stat().st_mode), 0o600)
        self.assertEqual(list(self.root.glob(".*.tmp")), [])

    def test_failure_before_replace_preserves_existing_destination(self):
        self.destination.write_text('{"old": true}\n', encoding="utf-8")
        with patch("schemii.atomic_json.os.fsync", side_effect=OSError("sync failed")):
            with self.assertRaises(OSError):
                write_json(self.destination, {"new": True})
        self.assertEqual(json.loads(self.destination.read_text(encoding="utf-8")), {"old": True})
        self.assertEqual(list(self.root.glob(".*.tmp")), [])

    def test_replacement_happens_after_file_sync(self):
        events = []
        real_fsync = os.fsync
        real_replace = os.replace

        def sync(descriptor):
            events.append("sync")
            return real_fsync(descriptor)

        def replace(source, destination):
            events.append("replace")
            return real_replace(source, destination)

        with patch("schemii.atomic_json.os.fsync", side_effect=sync), patch("schemii.atomic_json.os.replace", side_effect=replace):
            write_json(self.destination, {"ok": True})
        self.assertEqual(events[0:2], ["sync", "replace"])
        self.assertEqual(events[-1], "replace" if os.name == "nt" else "sync")


if __name__ == "__main__":
    unittest.main()
