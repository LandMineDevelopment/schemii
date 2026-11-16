"""Compile/run the production completion gate with deterministic I/O interleavings."""

from pathlib import Path
import subprocess
import tempfile
import unittest

HERE = Path(__file__).resolve().parents[1]


class CompletionControls(unittest.TestCase):
    def test_actual_completion_gate_read_and_journal_cancellation(self):
        with tempfile.TemporaryDirectory() as directory:
            subprocess.run(
                [
                    "javac",
                    "-source",
                    "8",
                    "-target",
                    "8",
                    "-d",
                    directory,
                    str(
                        HERE
                        / "app/src/main/java/development/landmine/schemii/runtimeprobe/CollectorCompletion.java"
                    ),
                    str(HERE / "tests/java/CollectorCompletionTest.java"),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            result = subprocess.run(
                [
                    "java",
                    "-cp",
                    directory,
                    "development.landmine.schemii.runtimeprobe.CollectorCompletionTest",
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(len(result.stdout.splitlines()), 9)
            self.assertTrue(
                all(line.endswith(":PASS") for line in result.stdout.splitlines())
            )


if __name__ == "__main__":
    unittest.main()
