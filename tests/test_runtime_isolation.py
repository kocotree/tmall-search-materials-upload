import json
import hashlib
from pathlib import Path
import shutil
import subprocess

from upload_search_materials.interaction.session import SessionStore
from upload_search_materials.collection_worker import environment_fingerprint
from upload_search_materials.runtime_config import load_runtime_config


def prepare_dependency_runtime(
    project: Path,
    runtime_root: Path,
    *,
    windows: bool = True,
    bom: bool = False,
) -> Path:
    python = (
        runtime_root / ".venv" / "Scripts" / "python.exe"
        if windows
        else runtime_root / ".venv" / "bin" / "python"
    )
    python.parent.mkdir(parents=True, exist_ok=True)
    python.write_bytes(b"prepared")
    (project / "src" / "upload_search_materials").mkdir(parents=True, exist_ok=True)
    launcher = project / "scripts" / "run-plugin.py"
    launcher.parent.mkdir(parents=True, exist_ok=True)
    launcher.write_text("# launcher\n", encoding="utf-8")
    lock = project / "uv.lock"
    lock.write_text("version = 1\n", encoding="utf-8")
    fingerprint = {
        "schema_version": 2,
        "lock_sha256": hashlib.sha256(lock.read_bytes()).hexdigest(),
        "python": str(python.resolve()),
        "dependency_mode": "no-install-project",
        "launch_mode": "current-plugin-source",
    }
    encoding = "utf-8-sig" if bom else "utf-8"
    (runtime_root / "environment-fingerprint.json").write_bytes(
        json.dumps(fingerprint).encode(encoding)
    )
    return python


def test_default_runtime_session_is_git_ignored_and_has_no_authentication_data(
    tmp_path,
):
    workspace = tmp_path / "workspace"
    (workspace / "src" / "upload_search_materials").mkdir(parents=True)
    (workspace / "SKILL.md").write_text("---\nname: test\ndescription: test\n---\n")
    (workspace / "pyproject.toml").write_text("[project]\nname='test'\n")
    source_ignore = Path(__file__).parents[1] / ".gitignore"
    shutil.copy2(source_ignore, workspace / ".gitignore")
    subprocess.run(
        ["git", "init", "-q"],
        cwd=workspace,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "add", ".gitignore"],
        cwd=workspace,
        check=True,
        capture_output=True,
        text=True,
    )
    user_data = tmp_path / "user-data"
    runtime = load_runtime_config(
        environ={"TMALL_USER_DATA_ROOT": str(user_data)}, start=workspace
    )
    session = SessionStore(runtime.runs_root).create_session()
    state = json.loads(
        (session.path / "session.json").read_text(encoding="utf-8")
    )

    status = subprocess.run(
        ["git", "status", "--short", "--untracked-files=all"],
        cwd=workspace,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    persisted = json.dumps(state, ensure_ascii=False).casefold()

    assert runtime.runs_root == user_data / "runs"
    assert "runs/" not in status.replace("\\", "/")
    for secret_name in (
        "password",
        "cookie",
        "token",
        "sms_code",
        "qr_payload",
    ):
        assert secret_name not in persisted


def test_prepared_environment_is_independent_of_global_uv_cache(
    tmp_path, monkeypatch
):
    project = tmp_path / "upload-search-materials"
    runtime_root = tmp_path / "user-runtime"
    prepare_dependency_runtime(project, runtime_root)
    monkeypatch.setenv("TMALL_RUNTIME_ROOT", str(runtime_root))
    status = environment_fingerprint(project)

    assert status["prepared"] is True
    assert status["uv_cache_dir"] == str(runtime_root / "uv-cache")


def test_environment_fingerprint_accepts_windows_powershell_utf8_bom(tmp_path):
    project = tmp_path / "upload-search-materials"
    runtime_root = tmp_path / "runtime"
    prepare_dependency_runtime(project, runtime_root, bom=True)

    status = environment_fingerprint(project, runtime_root=runtime_root)

    assert status["prepared"] is True
    assert status["fingerprint_status"] == "matched"


def test_missing_or_stale_prepared_environment_is_reason_coded(tmp_path):
    project = tmp_path / "upload-search-materials"
    project.mkdir()
    (project / "uv.lock").write_text("version = 1\n", encoding="utf-8")

    missing = environment_fingerprint(project)
    (project / ".venv" / "Scripts").mkdir(parents=True)
    (project / ".venv" / "Scripts" / "python.exe").write_bytes(b"python")
    stale = environment_fingerprint(project)

    assert missing["prepared"] is False
    assert stale["prepared"] is False


def test_user_runtime_is_required_even_when_plugin_cache_has_venv(tmp_path):
    workspace = tmp_path / "workspace"
    root_python = workspace / ".venv" / "Scripts" / "python.exe"
    module = workspace / "upload-search-materials"
    plugin_python = module / ".venv" / "Scripts" / "python.exe"
    root_python.parent.mkdir(parents=True)
    root_python.write_bytes(b"incomplete-root")
    plugin_python.parent.mkdir(parents=True)
    plugin_python.write_bytes(b"plugin-cache")
    runtime_root = tmp_path / "user-runtime"
    runtime_python = prepare_dependency_runtime(module, runtime_root)

    status = environment_fingerprint(module, runtime_root=runtime_root)

    assert status["prepared"] is True
    assert Path(status["python"]) == runtime_python
    assert Path(status["python"]) != root_python
    assert Path(status["python"]) != plugin_python


def test_user_runtime_environment_wins_over_plugin_cache_venv(
    tmp_path, monkeypatch
):
    project = tmp_path / "plugin-cache"
    plugin_python = project / ".venv" / "Scripts" / "python.exe"
    plugin_python.parent.mkdir(parents=True)
    plugin_python.write_bytes(b"plugin-cache")
    (project / "src" / "upload_search_materials").mkdir(parents=True)
    (project / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    runtime_root = tmp_path / "user-runtime"
    runtime_python = runtime_root / ".venv" / "Scripts" / "python.exe"
    runtime_python.parent.mkdir(parents=True)
    runtime_python.write_bytes(b"user-runtime")
    monkeypatch.setenv("TMALL_RUNTIME_ROOT", str(runtime_root))

    status = environment_fingerprint(project)

    assert Path(status["python"]) == runtime_python
    assert Path(status["python"]) != plugin_python
