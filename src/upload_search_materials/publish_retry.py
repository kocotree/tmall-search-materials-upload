"""Safe workbench recovery for failed pre-publish slot uploads."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .interaction.session import InteractionConflict, SessionStore
from .reporting import read_json
from .state_store import StateStore


PRE_PUBLISH_ATTEMPT_MARKER = "pre_publish_attempts="
PRE_PUBLISH_RETRIES_EXHAUSTED_MARKER = (
    "skipped_after_pre_publish_retries=true"
)
REMOTE_SUCCESS_STATUSES = frozenset({"submitted", "under_review", "success"})
UNPUBLISHED_RECOVERABLE_STATUSES = frozenset(
    {"ready_for_review", "approved"}
)
PRE_PUBLISH_FAILURE_STATUSES = frozenset({"failed", "blocked"})
REMOTE_UNCERTAIN_STATUSES = frozenset({"uploading", "publish_uncertain"})


@dataclass(frozen=True)
class PublishRetryError(RuntimeError):
    reason_code: str
    user_message: str

    def __str__(self) -> str:
        return self.reason_code


def _publish_run_path(
    store: SessionStore,
    session_id: str,
    *,
    revision: int,
    input_sha256: str,
) -> Path:
    return (
        store._stage_path(session_id, "approval")
        / "publish-runs"
        / f"r{revision:04d}-{input_sha256[:12]}"
    )


def _task_identity_map(run_path: Path) -> dict[str, dict[str, Any]]:
    path = run_path / "material-items.json"
    if not path.is_file():
        return {}
    try:
        items = read_json(path)
    except (OSError, ValueError):
        return {}
    if not isinstance(items, list):
        return {}
    return {
        str(item.get("task_id", "")): item
        for item in items
        if isinstance(item, dict) and str(item.get("task_id", "")).strip()
    }


def inspect_publish_retry(
    store: SessionStore,
    session_id: str,
) -> dict[str, Any]:
    """Return a business-safe retry projection for the current approval run."""

    state = store.load_session(session_id)
    stage_state = state.get("stages", {}).get("approval", {})
    stage_status = str(stage_state.get("status", ""))
    revision = int(stage_state.get("revision", 0))
    unavailable = {
        "schema_version": 1,
        "available": False,
        "stage_status": stage_status,
        "revision": revision,
        "can_retry": False,
        "retryable_count": 0,
        "uncertain_count": 0,
        "successful_count": 0,
        "manual_review_count": 0,
        "total_count": 0,
        "retryable_tasks": [],
    }
    if state.get("current_stage") != "approval" or revision < 1:
        return unavailable

    result = store.read_optional_stage_document(
        session_id, "approval", "result"
    )
    handoff = store.read_optional_stage_document(
        session_id, "approval", "handoff"
    )
    if not isinstance(result, dict) or not isinstance(handoff, dict):
        return unavailable
    if (
        int(result.get("revision", -1)) != revision
        or int(handoff.get("revision", -1)) != revision
        or result.get("input_sha256") != handoff.get("input_sha256")
        or handoff.get("handoff_kind") != "publish_authorization"
    ):
        return unavailable
    authorization = handoff.get("authorization")
    if not isinstance(authorization, dict):
        return unavailable
    task_ids = [
        str(value).strip()
        for value in authorization.get("task_ids", [])
        if str(value).strip()
    ]
    if not task_ids or len(task_ids) != len(set(task_ids)):
        return unavailable

    input_sha256 = str(handoff.get("input_sha256", ""))
    run_path = _publish_run_path(
        store,
        session_id,
        revision=revision,
        input_sha256=input_sha256,
    )
    required_run_files = (
        "run.json",
        "product-tasks.json",
        "material-items.json",
        "run.sqlite3",
        "approval-manifest.json",
    )
    if not all((run_path / name).is_file() for name in required_run_files):
        return unavailable

    identities = _task_identity_map(run_path)
    retryable_tasks: list[dict[str, Any]] = []
    uncertain_count = 0
    successful_count = 0
    manual_review_count = 0
    state_store = StateStore(run_path / "run.sqlite3")
    try:
        for task_id in task_ids:
            record = state_store.item_record(task_id)
            if record is None:
                manual_review_count += 1
                continue
            status = str(record.get("status", ""))
            remote_material_id = str(
                record.get("remote_material_id") or ""
            ).strip()
            evidence = str(record.get("evidence") or "")
            identity = identities.get(task_id, {})
            if status in REMOTE_SUCCESS_STATUSES and remote_material_id:
                successful_count += 1
                continue
            if status in REMOTE_UNCERTAIN_STATUSES:
                uncertain_count += 1
                continue
            safe_unpublished = not remote_material_id and (
                status in UNPUBLISHED_RECOVERABLE_STATUSES
                or (
                    status in PRE_PUBLISH_FAILURE_STATUSES
                    and PRE_PUBLISH_ATTEMPT_MARKER in evidence
                    and PRE_PUBLISH_RETRIES_EXHAUSTED_MARKER in evidence
                )
            )
            if safe_unpublished:
                retryable_tasks.append(
                    {
                        "task_id": task_id,
                        "product_id": str(
                            identity.get("product_id", "")
                        ).strip(),
                        "slot_index": identity.get("slot_index"),
                        "status": status,
                        "status_label": (
                            "尚未上传"
                            if status in UNPUBLISHED_RECOVERABLE_STATUSES
                            else "上轮未完成"
                        ),
                    }
                )
                continue
            manual_review_count += 1
    finally:
        state_store.close()

    can_retry = (
        bool(retryable_tasks)
        and uncertain_count == 0
        and manual_review_count == 0
        and stage_status == "blocked"
    )
    if stage_status in {"ready_for_agent", "processing"}:
        message = "正在继续上传未完成坑位，已成功的坑位不会重复上传。"
    elif uncertain_count:
        message = (
            "存在上传状态待确认的坑位，请先确认千牛中的实际结果，"
            "系统不会直接重复上传。"
        )
    elif manual_review_count:
        message = (
            "存在不能直接重试的坑位，请先按页面提示处理，"
            "系统不会跳过安全检查继续上传。"
        )
    elif retryable_tasks:
        message = (
            f"有 {len(retryable_tasks)} 个坑位可以继续上传；"
            "已成功的坑位不会重复处理。"
        )
    else:
        message = "当前没有能够安全继续上传的坑位。"
    return {
        "schema_version": 1,
        "available": True,
        "stage_status": stage_status,
        "revision": revision,
        "retry_generation": int(stage_state.get("retry_generation", 0)),
        "can_retry": can_retry,
        "retryable_count": len(retryable_tasks),
        "uncertain_count": uncertain_count,
        "successful_count": successful_count,
        "manual_review_count": manual_review_count,
        "total_count": len(task_ids),
        "retryable_tasks": retryable_tasks,
        "message": message,
    }


def retry_failed_publish_tasks(
    store: SessionStore,
    session_id: str,
    *,
    request_id: str,
    authorized_user_name: str,
) -> dict[str, Any]:
    """Requeue only tasks proven not to have reached a successful publish."""

    summary = inspect_publish_retry(store, session_id)
    if not summary.get("available"):
        raise PublishRetryError(
            "UPLOAD_RETRY_NOT_AVAILABLE",
            "当前上传记录还不能安全恢复，请刷新页面后重试。",
        )

    handoff = store.read_optional_stage_document(
        session_id, "approval", "handoff"
    )
    authorization = (
        handoff.get("authorization", {})
        if isinstance(handoff, dict)
        else {}
    )
    original_user = str(authorization.get("confirmed_by", "")).strip()
    current_user = str(authorized_user_name).strip()
    if not current_user or current_user != original_user:
        raise PublishRetryError(
            "UPLOAD_RETRY_USER_CHANGED",
            "当前飞书账号与本批次上传负责人不一致，请切换回原账号后重试。",
        )
    if int(summary.get("uncertain_count", 0)):
        raise PublishRetryError(
            "UPLOAD_RETRY_REMOTE_STATUS_REQUIRED",
            "存在上传状态待确认的坑位，请先确认千牛中的实际结果。",
        )
    if int(summary.get("manual_review_count", 0)):
        raise PublishRetryError(
            "UPLOAD_RETRY_MANUAL_REVIEW_REQUIRED",
            "存在不能直接重试的坑位，请先按页面提示处理。",
        )

    retryable_tasks = list(summary.get("retryable_tasks") or [])
    retryable_task_ids = [
        str(item.get("task_id", ""))
        for item in retryable_tasks
        if str(item.get("task_id", "")).strip()
    ]
    if not retryable_task_ids:
        raise PublishRetryError(
            "UPLOAD_RETRY_NO_SAFE_TASKS",
            "当前没有能够安全继续上传的坑位。",
        )

    revision = int(summary["revision"])
    input_sha256 = str(handoff.get("input_sha256", ""))
    run_path = _publish_run_path(
        store,
        session_id,
        revision=revision,
        input_sha256=input_sha256,
    )
    if summary.get("stage_status") == "blocked":
        state_store = StateStore(run_path / "run.sqlite3")
        try:
            for task_id in retryable_task_ids:
                record = state_store.item_record(task_id)
                if record is None:
                    raise PublishRetryError(
                        "UPLOAD_RETRY_STATE_MISSING",
                        "部分坑位的上传记录不完整，请刷新页面后重试。",
                    )
                old_status = str(record.get("status", ""))
                if old_status in UNPUBLISHED_RECOVERABLE_STATUSES:
                    continue
                if (
                    old_status not in PRE_PUBLISH_FAILURE_STATUSES
                    or str(record.get("remote_material_id") or "").strip()
                    or PRE_PUBLISH_ATTEMPT_MARKER
                    not in str(record.get("evidence") or "")
                    or PRE_PUBLISH_RETRIES_EXHAUSTED_MARKER
                    not in str(record.get("evidence") or "")
                ):
                    raise PublishRetryError(
                        "UPLOAD_RETRY_STATE_CHANGED",
                        "坑位状态已经变化，请刷新页面后重新检查。",
                    )
                state_store.record_transition(
                    task_id,
                    old_status,
                    "ready_for_review",
                    reason="USER_RETRY_SAFE_PRE_PUBLISH_FAILURE",
                    evidence=(
                        str(record.get("evidence") or "")
                        + ";user_retry_authorized=true"
                    ).strip(";"),
                )
        finally:
            state_store.close()

    response = store.requeue_blocked_handoff(
        session_id,
        "approval",
        expected_revision=revision,
        request_id=request_id,
        reason="USER_RETRY_FAILED_UPLOADS",
        details={
            "retry_task_ids": retryable_task_ids,
            "confirmed_by": current_user,
        },
    )
    return {
        **response,
        "retryable_count": len(retryable_task_ids),
        "retry_task_ids": retryable_task_ids,
        "message": (
            f"已开始继续上传 {len(retryable_task_ids)} 个未完成坑位，"
            "已成功坑位不会重复上传。"
        ),
    }
