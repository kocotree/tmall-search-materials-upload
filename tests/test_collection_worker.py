from __future__ import annotations

import csv
from contextlib import contextmanager
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

from upload_search_materials.collection_worker import (
    collection_status,
    launch_collection_worker,
    run_collection_worker,
)
from upload_search_materials.collection_runtime import (
    CollectionBinding,
    attempt_path,
    create_attempt_document,
)
from upload_search_materials.collection_readiness import SelectorBootstrapError
from upload_search_materials.browser.config import load_selector_profile
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
    monkeypatch.setattr(
        worker_module,
        "_prepare_collection_page_early",
        lambda *_args, **_kwargs: None,
    )


def test_collection_page_starts_before_runtime_preflight(tmp_path, monkeypatch):
    runtime, runs, _, session, selectors = prepare(tmp_path)
    events = []

    def prepare_page(_cdp_url, material_center_url, _selectors):
        events.append(("page", material_center_url))

    def preflight(_runtime, **_kwargs):
        events.append(("preflight", ""))
        return {
            "ready": True,
            "environment": worker_module.environment_fingerprint(
                runtime.workspace_root
            ),
        }

    monkeypatch.setattr(worker_module, "_prepare_collection_page_early", prepare_page)
    monkeypatch.setattr(
        "upload_search_materials.runtime_preflight.preflight_runtime_environment",
        preflight,
    )

    launched = launch_collection_worker(
        runs_root=runs,
        session_id=session.session_id,
        runtime=runtime,
        selectors_path=selectors,
        popen=lambda *_args, **_kwargs: Process(),
        identity_provider=lambda _pid: "test-process",
    )

    assert launched["status"] == "processing"
    assert [event[0] for event in events[:2]] == ["page", "preflight"]
    assert events[0][1].endswith("?tab=recommend")


def test_worker_ownership_token_is_one_argument_when_it_starts_with_dash(
    tmp_path, monkeypatch
):
    runtime, runs, _, session, selectors = prepare(tmp_path)
    captured = {}
    monkeypatch.setattr(worker_module.secrets, "token_urlsafe", lambda _size: "-token")
    monkeypatch.setattr(
        worker_module,
        "process_identity",
        lambda pid: f"windows:{pid}:created",
    )

    def popen(argv, **_kwargs):
        captured["argv"] = list(argv)
        return Process()

    launch_collection_worker(
        runs_root=runs,
        session_id=session.session_id,
        runtime=runtime,
        selectors_path=selectors,
        popen=popen,
        identity_provider=lambda pid: f"windows:{pid}:created",
    )

    assert "--ownership-token=-token" in captured["argv"]
    assert "--ownership-token" not in captured["argv"]


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
    monkeypatch.setattr(
        "upload_search_materials.collection_worker.open_cdp_page",
        lambda *_args, **_kwargs: pytest.fail(
            "an existing production profile must not open bootstrap CDP"
        ),
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
    assert Path(calls[0][0][0][1]).name == "run-plugin.py"
    assert "-m" not in calls[0][0][0]
    assert "ownership_token" not in first["worker"]


def test_mismatched_dead_attempt_is_preserved_and_rotated(
    tmp_path, monkeypatch
):
    runtime, runs, store, session, selectors = prepare(tmp_path)
    handoff = store.read_optional_stage_document(
        session.session_id, "setup", "handoff"
    )
    store.wait_for_handoff(
        session.session_id,
        "setup",
        timeout_seconds=0.1,
        claimant_id="collection-worker",
    )
    original_claim = store.processing_claim(session.session_id, "setup")
    original_attempt_id = str(original_claim["attempt_id"])
    original_attempt_path = attempt_path(session.path, original_attempt_id)
    original_attempt_path.mkdir(parents=True)
    stale_attempt = create_attempt_document(
        CollectionBinding(
            session_id=session.session_id,
            stage_id="setup",
            revision=0,
            input_sha256="stale-input",
            selector_sha256="stale-selector",
            target_store="测试店铺",
        ),
        attempt_id=original_attempt_id,
        claim_id=str(original_claim["claim_id"]),
        claimant_id="collection-worker",
    )
    (original_attempt_path / "attempt.json").write_text(
        json.dumps(stale_attempt), encoding="utf-8"
    )

    launched = launch_collection_worker(
        runs_root=runs,
        session_id=session.session_id,
        runtime=runtime,
        selectors_path=selectors,
        popen=lambda *_args, **_kwargs: Process(),
        identity_provider=lambda _pid: "test-process",
    )

    assert launched["attempt_id"] != original_attempt_id
    assert json.loads(
        (original_attempt_path / "attempt.json").read_text(encoding="utf-8")
    )["revision"] == 0
    replacement_path = attempt_path(session.path, launched["attempt_id"])
    replacement = json.loads(
        (replacement_path / "attempt.json").read_text(encoding="utf-8")
    )
    assert replacement["revision"] == handoff["revision"]
    assert replacement["input_sha256"] == handoff["input_sha256"]
    current_claim = store.processing_claim(session.session_id, "setup")
    assert current_claim["attempt_id"] == launched["attempt_id"]
    events = [
        json.loads(line)
        for line in (session.path / "events.ndjson")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert events[-1]["event"] == "processing_attempt_rotated"
    assert events[-1]["previous_attempt_id"] == original_attempt_id


def test_missing_selector_bootstraps_then_launches_collection(
    tmp_path, monkeypatch
):
    runtime, runs, _, session, selectors = prepare(tmp_path)
    runtime = replace(runtime, selectors_file=None)
    profile = load_selector_profile(
        selectors,
        purpose="high_value_collection",
        production=True,
    )

    @contextmanager
    def page_context(*_args, **_kwargs):
        yield object()

    monkeypatch.setattr(
        "upload_search_materials.collection_worker.open_cdp_page",
        page_context,
    )
    monkeypatch.setattr(
        "upload_search_materials.collection_worker."
        "ensure_production_selector_profile",
        lambda selected_runtime, _page, **_kwargs: (
            replace(selected_runtime, selectors_file=selectors),
            profile,
            {
                "ready": True,
                "reason_code": "READY",
                "field_results": {},
                "page_evidence": {
                    "page_identity": "material_center",
                    "observed_store": "测试店铺",
                    "store_match": True,
                    "pagination_state": {"verified": True},
                },
                "validated_at": "2026-08-18T08:00:00+00:00",
            },
        ),
    )
    monkeypatch.setattr(
        "upload_search_materials.collection_worker.process_identity",
        lambda pid: f"windows:{pid}:created",
    )
    repeated_page_preparations = []
    monkeypatch.setattr(
        worker_module,
        "_prepare_collection_page_early",
        lambda *_args, **_kwargs: repeated_page_preparations.append(True),
    )

    launched = launch_collection_worker(
        runs_root=runs,
        session_id=session.session_id,
        runtime=runtime,
        popen=lambda *_args, **_kwargs: Process(),
        identity_provider=lambda pid: f"windows:{pid}:created",
    )

    assert launched["status"] == "processing"
    assert repeated_page_preparations == []
    evidence = (
        session.path
        / "collected"
        / "promotion"
        / "collection-readiness-evidence.json"
    )
    assert json.loads(evidence.read_text(encoding="utf-8"))["ready"] is True


def test_failed_selector_bootstrap_writes_retryable_result_and_releases_claim(
    tmp_path, monkeypatch
):
    runtime, runs, store, session, _ = prepare(tmp_path)
    runtime = replace(runtime, selectors_file=None)

    @contextmanager
    def page_context(*_args, **_kwargs):
        yield object()

    validation = {
        "ready": False,
        "reason_code": "SELECTOR_FIELD_INVALID:promotion_rows",
        "field_results": {"promotion_rows": {"ready": False}},
        "page_evidence": {
            "reason_code": "SELECTOR_FIELD_INVALID:promotion_rows"
        },
        "validated_at": "2026-08-18T08:00:00+00:00",
    }
    monkeypatch.setattr(
        "upload_search_materials.collection_worker.open_cdp_page",
        page_context,
    )
    monkeypatch.setattr(
        "upload_search_materials.collection_worker."
        "ensure_production_selector_profile",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            SelectorBootstrapError(
                "SELECTOR_FIELD_INVALID:promotion_rows", validation
            )
        ),
    )

    launched = launch_collection_worker(
        runs_root=runs,
        session_id=session.session_id,
        runtime=runtime,
    )

    assert launched["status"] == "needs_user_input"
    assert launched["result"]["blocking_reasons"] == [
        "SELECTOR_FIELD_INVALID:promotion_rows"
    ]
    assert store.processing_claim(session.session_id, "setup") is None
    assert store.load_session(session.session_id)["stages"]["setup"]["status"] == (
        "needs_user_input"
    )
    diagnostic = json.loads(
        (session.path / "agent-diagnostics" / "current.json").read_text(
            encoding="utf-8"
        )
    )
    assert diagnostic["reason_code"] == "SELECTOR_FIELD_INVALID"


def test_preflight_failure_writes_result_and_releases_claim(tmp_path, monkeypatch):
    runtime, runs, store, session, selectors = prepare(tmp_path)

    def fail_preflight(*_args, **_kwargs):
        from upload_search_materials.runtime_preflight import RuntimePreflightError

        raise RuntimePreflightError(
            "ENVIRONMENT_NOT_PREPARED", "运行 bootstrap 后恢复同一会话"
        )

    monkeypatch.setattr(
        "upload_search_materials.runtime_preflight.preflight_runtime_environment",
        fail_preflight,
    )

    launched = launch_collection_worker(
        runs_root=runs,
        session_id=session.session_id,
        runtime=runtime,
        selectors_path=selectors,
    )

    assert launched["status"] == "needs_user_input"
    assert launched["result"]["blocking_reasons"] == [
        "ENVIRONMENT_NOT_PREPARED"
    ]
    assert store.processing_claim(session.session_id, "setup") is None
    diagnostic = json.loads(
        (session.path / "agent-diagnostics" / "current.json").read_text(
            encoding="utf-8"
        )
    )
    assert diagnostic["reason_code"] == "ENVIRONMENT_NOT_PREPARED"


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
