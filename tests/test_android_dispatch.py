"""The explicit Android launcher never enters the desktop Docker path."""

from pathlib import Path
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def test_android_dispatch_preserves_arguments_and_backend_failure(tmp_path):
    shutil.copy2(ROOT / "start.sh", tmp_path / "start.sh")
    backend = tmp_path / "dev/android/termux.sh"
    backend.parent.mkdir(parents=True)
    backend.write_text('printf "<%s>\\n" "$@"\nexit 37\n')
    result = subprocess.run(
        [
            "bash",
            str(tmp_path / "start.sh"),
            "--android",
            "logs",
            "argument with spaces",
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 37
    assert result.stdout == "<logs>\n<argument with spaces>\n"
    assert result.stderr == ""
