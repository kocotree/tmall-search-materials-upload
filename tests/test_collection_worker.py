from __future__ import annotations

import csv
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest
import yaml

from upload_search_materials.collection_worker import (
    collection_status,
    launch_collection_worker,
    run_collection_worker,
)
from upload_search_materials.collection_runtime import attempt_path
import upload_search_materials.collection_worker as worker_module
from upload_search_materials.interaction.session import SessionStore
from upload_search_materials.io_tables import PRODUCT_REQUIRED_COLUMNS
from upload_search_materials.runtime_config import load_runtime_config


class Process:
    pid = 4321


@pytest.fixture(autouse=True)
def prepared_runtime_preflight(monkeypatch):
    monkeypatch.setattr(
        "upload_search_materials.runtime_preflight."
        "preflight_runtime_environment",
        lambda runtime, **_kwargs: {
            "ready": True,
            "environment": worker_module.environment_fingerprint(
                runtime.workspace_root
            ),
        },
    )


@pytest.mark.skipif(os.name != "nt", reason="Windows process identity contract")
def test_windows_process_identity_matches_between_parent_and_child():
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import os,time;"
                "from upload_search_materials.collection_worker "
                "import process_identity;"
                "print(os.getpid(), process_identity(os.getpid()), flush=True);"
                "time.sleep(5)"
            ),
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        line = child.stdout.readline().strip()
        child_pid_text, child_identity = line.split(" ", 1)
        child_pid = int(child_pid_text)

        assert worker_module.process_identity(child_pid) == child_identity
    finally:
        child.terminate()
        child.wait(timeout=5)


@pytest.mark.skipif(os.name == "nt", reason="POSIX process identity contract")
def test_posix_process_identity_uses_stable_ps_fallback(monkeypatch):
    monkeypatch.setattr(
        worker_module.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout="Sun Aug 10 16:20:00 2026 501\n",
        ),
    )

    first = worker_module.process_identity(987654)
    second = worker_module.process_identity(987654)

    assert first == second
    assert first.startswith("posix:987654:")


def write_products(path: Path) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(
            stream, fieldnames=sorted(PRODUCT_REQUIRED_COLUMNS)
        )
        writer.writeheader()
        writer.writerow(
            {
                "商品ID": "886506466908",
                "商品名称（查找引用）": "分龄成长太阳镜",
                "货号（查找引用）": "KQ-TEST",
                "产品等级": "A",
                "链接": "https://item.taobao.com/item.htm?id=886506466908",
                "运营": "测试",
                "组别": "测试组",
                "品类-公司维度划分": "太阳镜",
            }
        )


def prepare(tmp_path: Path):
    workspace = tmp_path / "workspace"
    (workspace / "docs").mkdir(parents=True)
    project = workspace / "upload-search-materials"
    (project / "config").mkdir(parents=True)
    scripts = project / ".venv" / "Scripts"
    scripts.mkdir(parents=True)
    (scripts / "python.exe").write_bytes(b"prepared")
    (scripts / "tmall-materials.exe").write_bytes(b"prepared")
    (project / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    products = workspace / "products.csv"
    rules = workspace / "rules.csv"
    selectors = project / "config" / "selectors.local.yaml"
    write_products(products)
    rules.write_text("month,rule\n7,include\n", encoding="utf-8")
    selectors.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "profile_name": "test",
                "profile_version": "1",
                "production": True,
                "supported_purposes": ["high_value_collection"],
                "store_name": "[data-store]",
                "human_check": "[data-human-check]",
                "promotion_tab": "#promotion",
                "high_value_filter": "#high-value",
                "promotion_rows": "tbody tr",
                "promotion_current_page": "#current",
                "promotion_first_page": "#first",
                "promotion_terminal_page": "#terminal",
                "promotion_next_page": "#next",
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    runtime = load_runtime_config(environ={}, start=workspace)
    runs = workspace / "runs"
    store = SessionStore(runs)
    session = store.create_session()
    store.save_input(
        session.session_id,
        "setup",
        {
            "store": "测试店铺",
            "products_csv": str(products),
            "rules_csv": str(rules),
        },
    )
    return runtime, runs, store, session, selectors


def test_launcher_returns_promptly_and_reuses_matching_live_worker(
    tmp_path, monkeypatch
):
    runtime, runs, _, session, selectors = prepare(tmp_path)
    calls = []

    def popen(*args, **kwargs):
        calls.append((args, kwargs))
        return Process()

    monkeypatch.setattr(
        "upload_search_materials.collection_worker.process_identity",
        lambda pid: f"windows:{pid}:created",
    )
    first = launch_collection_worker(
        runs_root=runs,
        session_id=session.session_id,
        runtime=runtime,
        selectors_path=selectors,
        popen=popen,
        identity_provider=lambda pid: f"windows:{pid}:created",
    )
    second = launch_collection_worker(
        runs_root=runs,
        session_id=session.session_id,
        runtime=runtime,
        selectors_path=selectors,
        popen=popen,
        identity_provider=lambda pid: f"windows:{pid}:created",
    )

    assert first["status"] == "processing"
    assert first["reused"] is False
    assert second["reused"] is True
    assert len(calls) == 1
    assert calls[0][1]["stdin"] is not None
    assert calls[0][1]["stdout"] is calls[0][1]["stderr"]
    assert calls[0][1]["cwd"] == str(runtime.workspace_root)
    assert "ownership_token" not in first["worker"]


def test_posix_worker_defers_identity_check_to_child_process(
    tmp_path, monkeypatch
):
    runtime, runs, _, session, selectors = prepare(tmp_path)
    local_identity = {
        "platform": "posix",
        "sid": "uid:501",
        "login_session_id": 123,
        "interactive_desktop": False,
    }
    monkeypatch.setattr(
        worker_module,
        "require_local_resource_identity",
        lambda expected: expected,
    )
    monkeypatch.setattr(
        worker_module,
        "runtime_identity_for_pid",
        lambda _pid: pytest.fail("POSIX child identity is verified in the child"),
    )

    launched = launch_collection_worker(
        runs_root=runs,
        session_id=session.session_id,
        runtime=runtime,
        selectors_path=selectors,
        popen=lambda *_args, **_kwargs: Process(),
        identity_provider=lambda pid: f"process:{pid}",
        local_resource_identity=local_identity,
    )

    assert launched["status"] == "processing"


def test_status_separates_old_selector_result_from_current_worker(
    tmp_path, monkeypatch
):
    runtime, runs, store, session, selectors = prepare(tmp_path)
    handoff = store.read_optional_stage_document(
        session.session_id, "setup", "handoff"
    )
    old_result_path = store._stage_path(
        session.session_id, "setup"
    ) / "result.json"
    old_result_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "session_id": session.session_id,
                "stage_id": "setup",
                "revision": 1,
                "input_sha256": handoff["input_sha256"],
                "status": "needs_user_input",
                "blocking_reasons": ["SELECTOR_PROFILE_NOT_FOUND"],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "upload_search_materials.collection_worker.process_identity",
        lambda pid: f"windows:{pid}:created",
    )
    launch_collection_worker(
        runs_root=runs,
        session_id=session.session_id,
        runtime=runtime,
        selectors_path=selectors,
        popen=lambda *_args, **_kwargs: Process(),
        identity_provider=lambda pid: f"windows:{pid}:created",
    )

    status = collection_status(runs, session.session_id)

    assert status["status"] == "processing"
    assert status["current_result"] is None
    assert status["history"][0]["blocking_reasons"] == [
        "SELECTOR_PROFILE_NOT_FOUND"
    ]
    assert status["history"][0]["superseded"] is True


def test_confirmed_dead_owned_worker_is_recoverable_before_lease_expiry(
    tmp_path, monkeypatch
):
    runtime, runs, _, session, selectors = prepare(tmp_path)
    monkeypatch.setattr(
        "upload_search_materials.collection_worker.process_identity",
        lambda pid: f"windows:{pid}:created",
    )
    launch_collection_worker(
        runs_root=runs,
        session_id=session.session_id,
        runtime=runtime,
        selectors_path=selectors,
        popen=lambda *_args, **_kwargs: Process(),
        identity_provider=lambda pid: f"windows:{pid}:created",
    )
    monkeypatch.setattr(
        "upload_search_materials.collection_worker.process_identity",
        lambda _pid: None,
    )

    status = collection_status(runs, session.session_id)

    assert status["status"] == "recoverable"
    assert status["recovery_action"] == "resume_exact_session"


@pytest.mark.parametrize(
    ("processor_result", "raises", "terminal"),
    [
        ({"status": "completed"}, False, "completed"),
        (None, True, "failed"),
    ],
)
def test_worker_persists_terminal_status_for_completion_and_crash(
    tmp_path,
    monkeypatch,
    processor_result,
    raises,
    terminal,
):
    runtime, runs, _, session, selectors = prepare(tmp_path)

    class CurrentProcess:
        pid = os.getpid()

    monkeypatch.setattr(
        worker_module, "process_identity", lambda _pid: "current-process"
    )
    launched = launch_collection_worker(
        runs_root=runs,
        session_id=session.session_id,
        runtime=runtime,
        selectors_path=selectors,
        popen=lambda *_args, **_kwargs: CurrentProcess(),
        identity_provider=lambda _pid: "current-process",
    )
    private = (
        attempt_path(session.path, launched["attempt_id"])
        / "worker.private.json"
    )
    manifest = json.loads(private.read_text(encoding="utf-8"))

    def process(**_kwargs):
        if raises:
            raise RuntimeError("worker crashed")
        return processor_result

    monkeypatch.setattr(worker_module, "process_setup_collection", process)
    arguments = {
        "runs_root": runs,
        "session_id": session.session_id,
        "attempt_id": launched["attempt_id"],
        "ownership_token": manifest["ownership_token"],
        "claimant_id": "collection-worker",
        "selectors_path": selectors,
        "cdp_url": runtime.cdp_url,
    }
    if raises:
        with pytest.raises(RuntimeError, match="worker crashed"):
            run_collection_worker(**arguments)
    else:
        run_collection_worker(**arguments)

    final = json.loads(private.read_text(encoding="utf-8"))
    assert final["terminal_status"] == terminal
    assert final["phase"] == terminal


def test_worker_securely_claims_actual_pid_from_verified_python_launcher(
    tmp_path,
    monkeypatch,
):
    runtime, runs, _, session, selectors = prepare(tmp_path)
    launcher_pid = 4321
    current_pid = os.getpid()

    monkeypatch.setattr(
        worker_module,
        "process_identity",
        lambda pid: (
            "launcher-process"
            if pid == launcher_pid
            else "actual-worker-process"
            if pid == current_pid
            else None
        ),
    )
    launched = launch_collection_worker(
        runs_root=runs,
        session_id=session.session_id,
        runtime=runtime,
        selectors_path=selectors,
        popen=lambda *_args, **_kwargs: Process(),
        identity_provider=lambda _pid: "launcher-process",
    )
    private = (
        attempt_path(session.path, launched["attempt_id"])
        / "worker.private.json"
    )
    manifest = json.loads(private.read_text(encoding="utf-8"))
    monkeypatch.setattr(
        worker_module,
        "process_setup_collection",
        lambda **_kwargs: {"status": "completed"},
    )

    run_collection_worker(
        runs_root=runs,
        session_id=session.session_id,
        attempt_id=launched["attempt_id"],
        ownership_token=manifest["ownership_token"],
        claimant_id="collection-worker",
        selectors_path=selectors,
        cdp_url=runtime.cdp_url,
    )

    final = json.loads(private.read_text(encoding="utf-8"))
    assert final["launcher_pid"] == launcher_pid
    assert final["launcher_process_identity"] == "launcher-process"
    assert final["pid"] == current_pid
    assert final["process_identity"] == "actual-worker-process"
