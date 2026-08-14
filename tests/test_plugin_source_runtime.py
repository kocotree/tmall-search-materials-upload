import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from upload_search_materials.plugin_runtime import (
    plugin_child_environment,
    plugin_cli_command,
)
from upload_search_materials.runtime_preflight import environment_fingerprint


REPOSITORY_ROOT = Path(__file__).parents[1]


def test_run_plugin_prefers_current_source_over_stale_pythonpath(tmp_path):
    plugin = tmp_path / "plugin"
    launcher = plugin / "scripts" / "run-plugin.py"
    launcher.parent.mkdir(parents=True)
    shutil.copy2(REPOSITORY_ROOT / "scripts" / "run-plugin.py", launcher)
    current = plugin / "src" / "upload_search_materials"
    current.mkdir(parents=True)
    (current / "__init__.py").write_text("", encoding="utf-8")
    (current / "cli.py").write_text(
        "def main():\n    print('current-plugin-source')\n    return 0\n",
        encoding="utf-8",
    )
    stale = tmp_path / "stale" / "upload_search_materials"
    stale.mkdir(parents=True)
    (stale / "__init__.py").write_text("", encoding="utf-8")
    (stale / "cli.py").write_text(
        "def main():\n    print('stale-site-packages')\n    return 0\n",
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(stale.parent)

    completed = subprocess.run(
        [sys.executable, str(launcher)],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    assert completed.stdout.strip() == "current-plugin-source"


def test_child_commands_use_current_plugin_launcher(tmp_path):
    launcher = tmp_path / "scripts" / "run-plugin.py"
    launcher.parent.mkdir()
    launcher.touch()

    command = plugin_cli_command(
        tmp_path, "collection-worker", "--session", "test"
    )
    environment = plugin_child_environment(
        tmp_path,
        base={"PYTHONPATH": "/stale/source"},
    )

    assert command[1] == str(launcher.resolve())
    assert "-m" not in command
    assert environment["PYTHONPATH"].split(os.pathsep)[0] == str(
        (tmp_path / "src").resolve()
    )
    assert environment["TMALL_PLUGIN_ROOT"] == str(tmp_path.resolve())


def test_fingerprint_records_dependencies_without_installing_project(tmp_path):
    plugin = tmp_path / "plugin"
    runtime = tmp_path / "runtime"
    python = runtime / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.touch()
    (runtime / "uv-cache").mkdir()
    (plugin / "src" / "upload_search_materials").mkdir(parents=True)
    (plugin / "scripts").mkdir()
    (plugin / "scripts" / "run-plugin.py").touch()
    (plugin / "uv.lock").write_text("version = 1\n", encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            str(REPOSITORY_ROOT / "scripts" / "write-runtime-fingerprint.py"),
            "--project-root",
            str(plugin),
            "--runtime-root",
            str(runtime),
            "--python",
            str(python),
            "--uv-cache-dir",
            str(runtime / "uv-cache"),
        ],
        check=False,
    )

    document = json.loads(
        (runtime / "environment-fingerprint.json").read_text(encoding="utf-8")
    )
    status = environment_fingerprint(plugin, runtime_root=runtime)
    assert completed.returncode == 0
    assert document["dependency_mode"] == "no-install-project"
    assert document["launch_mode"] == "current-plugin-source"
    assert status["prepared"] is True


def test_bootstrap_and_fixed_launchers_do_not_use_installed_business_cli():
    bootstrap_sh = (REPOSITORY_ROOT / "scripts" / "bootstrap.sh").read_text()
    bootstrap_ps1 = (REPOSITORY_ROOT / "scripts" / "bootstrap.ps1").read_text()
    assert "--no-install-project" in bootstrap_sh
    assert '"--no-install-project"' in bootstrap_ps1
    assert "--no-editable" not in bootstrap_sh
    assert '"--no-editable"' not in bootstrap_ps1

    for name in (
        "start-managed-workbench.sh",
        "start-managed-workbench.ps1",
        "start-ui.sh",
        "start-ui.ps1",
        "start-material-executor.sh",
        "start-material-executor.ps1",
        "launch-material-executor-desktop.ps1",
    ):
        text = (REPOSITORY_ROOT / "scripts" / name).read_text(encoding="utf-8")
        assert "run-plugin.py" in text
        assert "upload_search_materials.cli" not in text
