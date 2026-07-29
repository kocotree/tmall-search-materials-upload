import json
from pathlib import Path
import shutil
import subprocess

from upload_search_materials.interaction.session import SessionStore
from upload_search_materials.collection_worker import environment_fingerprint
from upload_search_materials.runtime_config import load_runtime_config


def test_default_runtime_session_is_git_ignored_and_has_no_authentication_data(
    tmp_path,
):
    workspace = tmp_path / "workspace"
    (workspace / "docs").mkdir(parents=True)
    (workspace / "upload-search-materials").mkdir()
    source_ignore = Path(__file__).parents[2] / ".gitignore"
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
    runtime = load_runtime_config(environ={}, start=workspace)
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

    assert runtime.runs_root == workspace / "runs"
    assert "runs/" not in status.replace("\\", "/")
    for secret_name in (
        "password",
        "cookie",
        "token",
        "sms_code",
        "qr_payload",
    ):
        assert secret_name not in persisted


def test_prepared_environment_is_independent_of_global_uv_cache(tmp_path):
    project = tmp_path / "upload-search-materials"
    scripts = project / ".venv" / "Scripts"
    scripts.mkdir(parents=True)
    (scripts / "python.exe").write_bytes(b"prepared")
    (scripts / "tmall-materials.exe").write_bytes(b"prepared")
    (project / "uv.lock").write_text("version = 1\n", encoding="utf-8")

    status = environment_fingerprint(project)

    assert status["prepared"] is True
    assert status["uv_cache_dir"] == str(project / ".uv-cache")
    assert "AppData" not in status["uv_cache_dir"]


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
