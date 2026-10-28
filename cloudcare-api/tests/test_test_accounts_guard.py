"""The demo account helper must never create known passwords in production."""
import os
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "create_test_accounts.py"
API_DIR = Path(__file__).resolve().parents[1]


def test_demo_accounts_refuse_to_run_in_production():
    env = {
        **os.environ,
        "ENV": "production",
        "PYTHONPATH": str(API_DIR),
    }
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=API_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode != 0
    assert "Refusing to create demo accounts" in (result.stdout + result.stderr)
