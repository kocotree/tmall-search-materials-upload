from __future__ import annotations

from threading import Event, Lock
import time

from upload_search_materials.agent_handoff import create_agent_request
import upload_search_materials.interaction.web as web_module
from upload_search_materials.interaction.session import SessionStore
from upload_search_materials.interaction.workflow_dispatcher import (
    WorkflowDispatcher,
)
from upload_search_materials.runtime_config import DiscoveredPath, RuntimeConfig


class RecordingProcessor:
    def __init__(self) -> None:
        self.called = Event()
        self._lock = Lock()
        self.tasks = []

    def __call__(self, task):
        with self._lock:
            self.tasks.append(task)
        self.called.set()
        return {"status": "completed"}


def test_dispatcher_recovers_handoff_submitted_before_start(tmp_path):
    store = SessionStore(tmp_path / "runs")
    session = store.create_session()
    handoff = store.save_input(
        session.session_id,
        "setup",
        {"store": "测试店铺"},
    )
    processor = RecordingProcessor()
    dispatcher = WorkflowDispatcher(
        store,
        session.session_id,
        processor,
        poll_seconds=0.05,
    )

    dispatcher.start()
    try:
        assert processor.called.wait(2)
        assert len(processor.tasks) == 1
        task = processor.tasks[0]
        assert task.action == "process-setup"
        assert task.revision == handoff["revision"]
        assert task.input_sha256 == handoff["input_sha256"]
        deadline = time.monotonic() + 2
        while (
            dispatcher.public_status()["status"] != "completed"
            and time.monotonic() < deadline
        ):
            time.sleep(0.01)
        assert dispatcher.public_status()["status"] == "completed"

        dispatcher.notify()
        time.sleep(0.1)
        assert len(processor.tasks) == 1
    finally:
        dispatcher.stop()


def test_dispatcher_retries_existing_selector_failure_once_after_restart(
    tmp_path,
):
    store = SessionStore(tmp_path / "runs")
    session = store.create_session()
    handoff = store.save_input(
        session.session_id,
        "setup",
        {"store": "测试店铺"},
    )
    store.wait_for_handoff(
        session.session_id,
        "setup",
        timeout_seconds=0.5,
        claimant_id="workbench-dispatcher",
    )
    claim = store.processing_claim(session.session_id, "setup")
    assert claim is not None
    store.write_result(
        session.session_id,
        "setup",
        int(handoff["revision"]),
        str(handoff["input_sha256"]),
        status="needs_user_input",
        summary="生产选择器配置无效",
        blocking_reasons=["SELECTOR_PROFILE_NOT_FOUND"],
        claim_id=str(claim["claim_id"]),
    )
    processor = RecordingProcessor()
    dispatcher = WorkflowDispatcher(
        store,
        session.session_id,
        processor,
        poll_seconds=30,
    )

    dispatcher.start()
    try:
        assert processor.called.wait(2)
        assert len(processor.tasks) == 1
        assert processor.tasks[0].generation == (
            f"ready-r{handoff['revision']}"
        )
        assert dispatcher.notify() == 0
        time.sleep(0.05)
        assert len(processor.tasks) == 1
    finally:
        dispatcher.stop()


def test_dispatcher_retries_missing_team_index_once_after_restart(tmp_path):
    store = SessionStore(tmp_path / "runs")
    session = store.create_session()
    setup = store.save_input(
        session.session_id,
        "setup",
        {"store": "测试店铺"},
    )
    store.write_result(
        session.session_id,
        "setup",
        int(setup["revision"]),
        str(setup["input_sha256"]),
        status="completed",
        summary="配置完成",
    )
    handoff = store.save_input(
        session.session_id,
        "completeness",
        {"selected_product_ids": ["1"]},
    )
    store.wait_for_handoff(
        session.session_id,
        "completeness",
        timeout_seconds=0.5,
        claimant_id="workbench-dispatcher",
    )
    claim = store.processing_claim(session.session_id, "completeness")
    assert claim is not None
    store.write_result(
        session.session_id,
        "completeness",
        int(handoff["revision"]),
        str(handoff["input_sha256"]),
        status="blocked",
        summary="团队文件夹索引尚未准备完成",
        blocking_reasons=["TEAM_INDEX_NO_VALID_LOCAL_CACHE"],
        claim_id=str(claim["claim_id"]),
    )
    processor = RecordingProcessor()
    dispatcher = WorkflowDispatcher(
        store,
        session.session_id,
        processor,
        poll_seconds=30,
    )

    dispatcher.start()
    try:
        assert processor.called.wait(2)
        assert len(processor.tasks) == 1
        task = processor.tasks[0]
        assert task.action == "process-product-selection"
        assert task.revision == handoff["revision"]
        assert task.input_sha256 == handoff["input_sha256"]
        assert task.generation == f"blocked-r{handoff['revision']}"
        assert dispatcher.notify() == 0
        time.sleep(0.05)
        assert len(processor.tasks) == 1
    finally:
        dispatcher.stop()


def test_dispatcher_wakes_for_handoff_submitted_after_start(tmp_path):
    store = SessionStore(tmp_path / "runs")
    session = store.create_session()
    processor = RecordingProcessor()
    dispatcher = WorkflowDispatcher(
        store,
        session.session_id,
        processor,
        poll_seconds=30,
    )

    dispatcher.start()
    try:
        store.save_input(
            session.session_id,
            "setup",
            {"store": "测试店铺"},
        )
        assert dispatcher.notify() == 1
        assert processor.called.wait(2)
        assert processor.tasks[0].action == "process-setup"
    finally:
        dispatcher.stop()


def test_dispatcher_retries_a_new_expired_processing_attempt(tmp_path):
    store = SessionStore(tmp_path / "runs")
    session = store.create_session()
    store.save_input(
        session.session_id,
        "setup",
        {"store": "测试店铺"},
    )
    calls = []
    called = Event()

    def claiming_processor(task):
        calls.append(task)
        if len(calls) == 1:
            store.wait_for_handoff(
                session.session_id,
                "setup",
                timeout_seconds=0.1,
                claimant_id="workbench-dispatcher",
            )
        called.set()
        return {"status": "completed"}

    dispatcher = WorkflowDispatcher(
        store,
        session.session_id,
        claiming_processor,
        poll_seconds=30,
    )
    dispatcher.start()
    try:
        assert called.wait(2)
        deadline = time.monotonic() + 2
        while (
            dispatcher.public_status()["status"] != "completed"
            and time.monotonic() < deadline
        ):
            time.sleep(0.01)
        state = store.load_session(session.session_id)
        state["processing_claim"]["lease_expires_at"] = (
            "2000-01-01T00:00:00+00:00"
        )
        store._write_json_atomic(
            store._session_path(session.session_id) / "session.json",
            state,
        )
        called.clear()

        assert dispatcher.notify() == 1
        assert called.wait(2)
        assert len(calls) == 2
        assert calls[0].generation != calls[1].generation
    finally:
        dispatcher.stop()


def test_dispatcher_recovers_pending_copy_request_without_agent_wait(tmp_path):
    store = SessionStore(tmp_path / "runs")
    session = store.create_session()
    request = create_agent_request(
        store,
        session.session_id,
        kind="copy_draft",
        candidates=[],
        context_revision=0,
        request_context={
            "processor": "process-copy-request",
            "context_fingerprint": "copy-context",
        },
    )
    processor = RecordingProcessor()
    dispatcher = WorkflowDispatcher(
        store,
        session.session_id,
        processor,
        poll_seconds=0.05,
    )

    dispatcher.start()
    try:
        assert processor.called.wait(2)
        assert len(processor.tasks) == 1
        task = processor.tasks[0]
        assert task.action == "process-copy-request"
        assert task.request_id == request["request_id"]
    finally:
        dispatcher.stop()


def test_managed_workbench_submit_notifies_its_dispatcher(tmp_path, monkeypatch):
    runs_root = tmp_path / "runs"
    store = SessionStore(runs_root)
    session = store.create_session()
    table = tmp_path / "table.csv"
    table.write_text("ok", encoding="utf-8")
    runtime = RuntimeConfig(
        workspace_root=tmp_path,
        products=DiscoveredPath(table, "configured"),
        rules=DiscoveredPath(table, "configured"),
        image_sources=({"label": "source", "path": str(tmp_path)},),
        runs_root=runs_root,
    )
    observed = {"started": 0, "notified": 0}

    class FakeDispatcher:
        def __init__(self, _store, session_id, _processor):
            self.session_id = session_id

        def start(self):
            observed["started"] += 1

        def notify(self):
            observed["notified"] += 1
            return 1

        def public_status(self):
            return {"online": True, "status": "idle"}

    monkeypatch.setattr(web_module, "WorkflowDispatcher", FakeDispatcher)
    monkeypatch.setattr(
        web_module,
        "WorkflowProcessor",
        lambda *_args, **_kwargs: object(),
    )
    app = web_module.create_app(
        runs_root,
        runtime_config=runtime,
        enforce_stage_order=False,
        service_identity={"runtime_identity": {}},
        managed_session_id=session.session_id,
    )
    client = app.test_client()

    response = client.post(
        f"/api/sessions/{session.session_id}/stages/setup/submit",
        json={
            "values": {
                "store": "测试店铺",
                "store_confirmed": True,
                "products_csv": str(table),
                "rules_csv": str(table),
                "image_source_labels": ["source"],
                "image_roots": [str(tmp_path)],
                "folder_index_root": str(tmp_path / ".index"),
                "asset_manifest": "",
                "historical_basic_xlsx": "",
                "historical_promotion_csv": "",
                "user_notes": "",
            }
        },
    )

    assert response.status_code == 202
    assert observed == {"started": 1, "notified": 1}
    status = client.get(
        f"/api/sessions/{session.session_id}/stages/setup/status"
    )
    assert status.json["workflow_dispatch"] == {
        "online": True,
        "status": "idle",
    }
