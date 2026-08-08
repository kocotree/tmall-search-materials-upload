import json
import hashlib
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


def test_environment_fingerprint_accepts_windows_powershell_utf8_bom(tmp_path):
    project = tmp_path / "upload-search-materials"
    scripts = project / ".venv" / "Scripts"
    scripts.mkdir(parents=True)
    (scripts / "python.exe").write_bytes(b"prepared")
    executable = scripts / "tmall-materials.exe"
    executable.write_bytes(b"prepared")
    lock = project / "uv.lock"
    lock.write_text("version = 1\n", encoding="utf-8")
    fingerprint = {
        "schema_version": 1,
        "lock_sha256": hashlib.sha256(lock.read_bytes()).hexdigest(),
        "executable": str(executable.resolve()),
    }
    (project / ".environment-fingerprint.json").write_bytes(
        json.dumps(fingerprint).encode("utf-8-sig")
    )

    status = environment_fingerprint(project)

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


def test_module_environment_wins_when_repository_root_also_has_venv(tmp_path):
    workspace = tmp_path / "workspace"
    root_python = workspace / ".venv" / "Scripts" / "python.exe"
    module = workspace / "upload-search-materials"
    module_python = module / ".venv" / "Scripts" / "python.exe"
    root_python.parent.mkdir(parents=True)
    root_python.write_bytes(b"incomplete-root")
    module_python.parent.mkdir(parents=True)
    module_python.write_bytes(b"prepared-module")
    (module / "src" / "upload_search_materials").mkdir(parents=True)
    lock = module / "uv.lock"
    lock.write_text("version = 1\n", encoding="utf-8")

    status = environment_fingerprint(module)

    assert status["prepared"] is True
    assert Path(status["python"]) == module_python
    assert Path(status["python"]) != root_python
