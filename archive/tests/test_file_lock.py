import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from schemii.file_lock import RefCountedKeyedFileGuard, exclusive_file_lock


class FileLockTests(unittest.TestCase):
    def test_keyed_guard_is_reentrant_independent_and_ref_counted(self):
        with tempfile.TemporaryDirectory() as directory:
            guard = RefCountedKeyedFileGuard(lambda key: Path(directory) / f"{key}.lock")
            other_entered = threading.Event()

            def enter_other():
                with guard.exclusive("two"):
                    other_entered.set()

            with guard.exclusive("one"):
                with guard.exclusive("one"):
                    thread = threading.Thread(target=enter_other)
                    thread.start()
                    self.assertTrue(other_entered.wait(1))
                    thread.join(1)

            self.assertEqual(guard._entries, {})

    def test_exclusive_lock_blocks_another_process(self):
        with tempfile.TemporaryDirectory() as directory:
            lock_path = Path(directory) / "shared.lock"
            waiting_path = Path(directory) / "waiting"
            marker_path = Path(directory) / "acquired"
            script = (
                "import sys\n"
                "from pathlib import Path\n"
                "from schemii.file_lock import exclusive_file_lock\n"
                "Path(sys.argv[2]).write_text('waiting', encoding='utf-8')\n"
                "with exclusive_file_lock(sys.argv[1]):\n"
                "    Path(sys.argv[3]).write_text('acquired', encoding='utf-8')\n"
            )
            environment = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
            with exclusive_file_lock(lock_path):
                process = subprocess.Popen(
                    [sys.executable, "-c", script, str(lock_path), str(waiting_path), str(marker_path)],
                    env=environment,
                )
                for _ in range(100):
                    if waiting_path.exists() or process.poll() is not None:
                        break
                    threading.Event().wait(0.05)
                self.assertTrue(waiting_path.exists())
                self.assertIsNone(process.poll())
                self.assertFalse(marker_path.exists())
            process.wait(timeout=5)
            self.assertEqual(process.returncode, 0)
            self.assertEqual(marker_path.read_text(encoding="utf-8"), "acquired")


if __name__ == "__main__":
    unittest.main()
