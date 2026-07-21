import shutil
import subprocess
from pathlib import Path

import pytest


def test_executable_browser_state_regressions():
    node = shutil.which("node")
    if node is None:
        pytest.fail("Node.js is required for the executable interaction UI state tests")
    test_file = Path(__file__).with_name("ui_state.test.cjs")

    completed = subprocess.run(
        [node, "--test", str(test_file)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
