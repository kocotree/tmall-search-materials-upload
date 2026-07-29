from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from upload_search_materials.collection_runtime import (
    CollectionBinding,
    CollectionRuntimeError,
    create_attempt_document,
    create_worker_document,
    resolve_collection_status,
    update_worker_progress,
    worker_liveness,
    write_json_atomic,
)


def binding() -> CollectionBinding:
    return CollectionBinding(
        session_id="20260729_090032",
        stage_id="setup",
        revision=1,
        input_sha256="a" * 64,
        selector_sha256="b" * 64,
        target_store="测试店铺",
    )


def active_claim(*, expired: bool = False) -> dict:
    expiry = datetime.now(timezone.utc) + timedelta(
        seconds=-30 if expired else 300
    )
    return {
        "claim_id": "claim-new",
        "attempt_id": "attempt-new",
        "lease_expires_at": expiry.isoformat(),
    }


def test_stale_selector_error_is_history_while_new_worker_is_current(tmp_path):
    attempt = create_attempt_document(
        binding(),
        attempt_id="attempt-new",
        claim_id="claim-new",
        claimant_id="worker",
    )
    worker = create_worker_document(
        attempt,
        pid=123,
        ownership_token="owner",
        process_identity="123:created",
        log_path=tmp_path / "worker.log",
    )
    old_result = {
        "attempt_id": "attempt-old",
        "status": "needs_user_input",
        "blocking_reasons": ["SELECTOR_PROFILE_NOT_FOUND"],
    }

    status = resolve_collection_status(
        stage_status="processing",
        current_attempt=attempt,
        current_result=old_result,
        worker=worker,
        worker_state="live",
        processing_claim=active_claim(),
        historical_results=[old_result],
    )

    assert status["status"] == "processing"
    assert status["source"] == "worker"
    assert status["current_result"] is None
    assert status["history"][0]["superseded"] is True


def test_dead_worker_before_first_checkpoint_is_immediately_recoverable(tmp_path):
    attempt = create_attempt_document(
        binding(),
        attempt_id="attempt-new",
        claim_id="claim-new",
        claimant_id="worker",
    )
    worker = create_worker_document(
        attempt,
        pid=123,
        ownership_token="owner",
        process_identity="123:created",
        log_path=tmp_path / "worker.log",
    )
    assert worker["row_count"] is None
    assert worker["last_completed_page"] is None

    status = resolve_collection_status(
        stage_status="processing",
        current_attempt=attempt,
        current_result=None,
        worker=worker,
        worker_state="dead",
        processing_claim=active_claim(),
    )

    assert status["status"] == "recoverable"
    assert status["source"] == "worker"
    assert status["recovery_action"] == "resume_exact_session"


def test_unproven_pid_remains_indeterminate_until_lease_expiry(tmp_path):
    attempt = create_attempt_document(
        binding(),
        attempt_id="attempt-new",
        claim_id="claim-new",
        claimant_id="worker",
    )
    worker = create_worker_document(
        attempt,
        pid=123,
        ownership_token="owner",
        process_identity="123:created",
        log_path=tmp_path / "worker.log",
    )

    liveness = worker_liveness(
        worker,
        ownership_token="wrong-owner",
        process_identity="123:created",
        process_probe=lambda _pid: False,
    )
    status = resolve_collection_status(
        stage_status="processing",
        current_attempt=attempt,
        current_result=None,
        worker=worker,
        worker_state=liveness,
        processing_claim=active_claim(),
    )

    assert liveness == "indeterminate"
    assert status["status"] == "processing_indeterminate"
    assert status["recovery_action"] == "wait_for_lease_expiry"


def test_worker_progress_rejects_stale_attempt_and_ownership(tmp_path):
    attempt = create_attempt_document(
        binding(),
        attempt_id="attempt-new",
        claim_id="claim-new",
        claimant_id="worker",
    )
    path = tmp_path / "worker.json"
    write_json_atomic(
        path,
        create_worker_document(
            attempt,
            pid=123,
            ownership_token="owner",
            process_identity="123:created",
            log_path=tmp_path / "worker.log",
        ),
    )

    with pytest.raises(CollectionRuntimeError, match="COLLECTION_ATTEMPT_STALE"):
        update_worker_progress(
            path,
            attempt_id="attempt-old",
            ownership_token="owner",
            phase="connecting_cdp",
        )
    with pytest.raises(
        CollectionRuntimeError,
        match="COLLECTION_WORKER_OWNERSHIP_MISMATCH",
    ):
        update_worker_progress(
            path,
            attempt_id="attempt-new",
            ownership_token="wrong",
            phase="connecting_cdp",
        )

    progress = update_worker_progress(
        path,
        attempt_id="attempt-new",
        ownership_token="owner",
        phase="writing_checkpoint",
        current_page=2,
        last_completed_page=1,
        row_count=20,
        last_checkpoint_at="2026-07-29T10:00:00+08:00",
    )
    assert progress["current_page"] == 2
    assert progress["last_completed_page"] == 1
    assert progress["row_count"] == 20
