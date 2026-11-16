import subprocess
import sys


def test_help_exits_zero():
    result = subprocess.run(
        [sys.executable, "-m", "ffp", "--help"], capture_output=True, text=True
    )
    assert result.returncode == 0
    assert "predict" in result.stdout
