"""Durable local gallery jobs that never claim a Codex handoff."""

from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import uuid
from typing import Any

from .asset_matching_workflow import (
    FOLDER_REVIEW,
    IMAGE_SELECTION,
    LEGACY_FOLDER_HANDOFF,
    build_gallery_identity,
    classify_asset_handoff,
    folder_decisions_sha256,
    gallery_covers_folder_decisions,
)
from .confirmed_assets import (
    CANDIDATE_STRATEGY_VERSION,
    build_confirmed_folder_gallery,
    extract_folder_decisions,
)
from .image_compliance import default_image_policy
from .interaction.session import InteractionConflict, SessionStore
from .io_tables import SchemaError, read_product_csv
from .runtime_identity import (
    LocalResourceIdentityMismatch,
    current_runtime_identity,
    require_local_resource_identity,
)


GALLERY_JOB_SCHEMA_VERSION = 1
GALLERY_JOB_STATUSES = frozenset(
    {"running", "completed", "failed", "stale"}
)
GALLERY_JOB_LEASE_SECONDS = 300
GALLERY_JOB_FAILED = "GALLERY_JOB_FAILED"
GALLERY_PATH_UNREADABLE = "CONFIRMED_FOLDER_UNREADABLE"
GALLERY_IDENTITY_STALE = "GALLERY_IDENTITY_STALE"
GALLERY_RESOURCE_IDENTITY_MISMATCH = "LOCAL_RESOURCE_IDENTITY_MISMATCH"
GALLERY_WORKER_HEARTBEAT_EXPIRED = "GALLERY_WORKER_HEARTBEAT_EXPIRED"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).replace(microsecond=0).isoformat()


def _identity_projection(identity: dict[str, Any]) -> dict[str, Any]:
    return {
        "platform": identity.get("platform"),
        "sid": identity.get("sid"),
        "login_session_id": identity.get("login_session_id"),
        "interactive_desktop": identity.get("interactive_desktop"),
        "remote_drive_letters": list(identity.get("remote_drive_letters", [])),
    }


def _identity_sha(identity: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            identity,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def gallery_job_path(store: SessionStore, session_id: str) -> Path:
    return store._stage_path(session_id, "asset_matching") / "gallery-job.json"


def read_gallery_job(
    store: SessionStore, session_id: str
) -> dict[str, Any] | None:
    path = gallery_job_path(store, session_id)
    if not path.is_file():
        return None
    return store._read_json(path, "gallery-job")


def reconcile_gallery_job(
    store: SessionStore, session_id: str
) -> dict[str, Any] | None:
    """Turn an expired running lease into an explicit retryable state."""

    with store._session_lock(session_id):
        job = read_gallery_job(store, session_id)
        if job is None or job.get("status") != "running":
            return job
        lease = job.get("lease_expires_at")
        try:
            expired = bool(
                lease
                and datetime.fromisoformat(str(lease)) <= _now()
            )
        except ValueError:
            expired = True
        if not expired:
            return job
        job.update(
            {
                "status": "failed",
                "reason_code": GALLERY_WORKER_HEARTBEAT_EXPIRED,
                "message": "本地图片加载进程心跳已过期",
                "updated_at": _iso(),
                "lease_expires_at": None,
                "recovery_action": "重试加载图片",
            }
        )
        store._write_json_atomic(gallery_job_path(store, session_id), job)
        attempt_dir = (
            store._stage_path(session_id, "asset_matching")
            / "gallery-attempts"
            / str(job.get("attempt_id", "unknown"))
        )
        attempt_dir.mkdir(parents=True, exist_ok=True)
        store._write_json_atomic(
            attempt_dir / "error.json",
            {
                "schema_version": GALLERY_JOB_SCHEMA_VERSION,
                "job_id": job.get("job_id"),
                "attempt_id": job.get("attempt_id"),
                "status": "failed",
                "reason_code": GALLERY_WORKER_HEARTBEAT_EXPIRED,
                "message": job["message"],
                "created_at": _iso(),
                "recovery_action": "重试加载图片",
            },
        )
        store._append_event(
            store._session_path(session_id),
            "local_gallery_failed",
            session_id=session_id,
            stage_id="asset_matching",
            job_id=job.get("job_id"),
            attempt_id=job.get("attempt_id"),
            reason_code=GALLERY_WORKER_HEARTBEAT_EXPIRED,
        )
        return job


def migrate_legacy_gallery_handoff(
    store: SessionStore, session_id: str
) -> tuple[dict[str, Any] | None, bool]:
    """Convert an unclaimed or expired folder handoff into a local job."""

    with store._session_lock(session_id):
        stage_path = store._stage_path(session_id, "asset_matching")
        handoff_path = stage_path / "handoff.json"
        input_path = stage_path / "input.json"
        if not handoff_path.is_file() or not input_path.is_file():
            return read_gallery_job(store, session_id), False
        handoff = store._read_json(handoff_path, "handoff")
        input_document = store._read_json(input_path, "input")
        if (
            classify_asset_handoff(handoff, input_document)
            != LEGACY_FOLDER_HANDOFF
        ):
            return read_gallery_job(store, session_id), False
        state = store.load_session(session_id)
        claim = store.processing_claim(session_id, "asset_matching")
        if claim and not claim.get("expired"):
            return read_gallery_job(store, session_id), False
        archive = (
            stage_path
            / "legacy-handoffs"
            / f"folder-r{int(handoff['revision']):04d}.json"
        )
        archive.parent.mkdir(parents=True, exist_ok=True)
        if not archive.is_file():
            store._write_json_atomic(archive, handoff)
        handoff_path.unlink()
        state["processing_claim"] = None
        wait = state.get("agent_wait")
        if isinstance(wait, dict) and wait.get("stage_id") == "asset_matching":
            state["agent_wait"] = None
        stage_state = state["stages"]["asset_matching"]
        stage_state["status"] = "draft"
        state["current_stage"] = "asset_matching"
        store._write_session_state(session_id, state)
        store._append_event(
            store._session_path(session_id),
            "legacy_folder_handoff_migrated",
            session_id=session_id,
            stage_id="asset_matching",
            revision=int(handoff["revision"]),
            input_sha256=handoff["input_sha256"],
            prior_claim_status=(
                "expired"
                if claim and claim.get("expired")
                else "unclaimed"
            ),
        )
        decisions = extract_folder_decisions(input_document)
        local_commit = {
            "revision": int(handoff["revision"]),
            "input_sha256": str(handoff["input_sha256"]),
        }
    return create_or_reuse_gallery_job(
        store, session_id, local_commit, decisions
    )


def invalidate_gallery_if_scope_expands(
    store: SessionStore,
    session_id: str,
    decisions: list[dict[str, Any]],
) -> dict[str, Any] | None:
    gallery_path = (
        store._stage_path(session_id, "asset_matching")
        / "confirmed-gallery.json"
    )
    if not gallery_path.is_file():
        return read_gallery_job(store, session_id)
    gallery = store._read_json(gallery_path, "confirmed-gallery")
    if gallery_covers_folder_decisions(
        gallery.get("gallery_identity"),
        decisions,
        session_id=session_id,
    ):
        return read_gallery_job(store, session_id)
    with store._session_lock(session_id):
        job = read_gallery_job(store, session_id)
        if job is None:
            return None
        job.update(
            {
                "status": "stale",
                "reason_code": GALLERY_IDENTITY_STALE,
                "message": "采用文件夹范围已扩大，请重新加载图片",
                "updated_at": _iso(),
                "lease_expires_at": None,
                "recovery_action": "重试加载图片",
            }
        )
        store._write_json_atomic(gallery_job_path(store, session_id), job)
        store._append_event(
            store._session_path(session_id),
            "local_gallery_stale",
            session_id=session_id,
            stage_id="asset_matching",
            job_id=job.get("job_id"),
            attempt_id=job.get("attempt_id"),
            reason_code=GALLERY_IDENTITY_STALE,
        )
        return job


def _job_identity(
    *,
    session_id: str,
    revision: int,
    input_sha256: str,
    decisions: list[dict[str, Any]],
    resource_identity: dict[str, Any],
) -> dict[str, Any]:
    selected_products = sorted(
        {
            str(item.get("product_id", ""))
            for item in decisions
            if item.get("decision") == "confirmed" and item.get("product_id")
        }
    )
    policy = default_image_policy()
    policy_sha = hashlib.sha256(
        json.dumps(
            policy,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    identity = {
        "session_id": session_id,
        "stage_id": "asset_matching",
        "revision": int(revision),
        "input_sha256": str(input_sha256),
        "folder_decisions_sha256": folder_decisions_sha256(decisions),
        "selected_product_ids": selected_products,
        "candidate_strategy_version": CANDIDATE_STRATEGY_VERSION,
        "image_policy_sha256": policy_sha,
        "local_resource_identity": _identity_projection(resource_identity),
    }
    return {**identity, "identity_sha256": _identity_sha(identity)}


def create_or_reuse_gallery_job(
    store: SessionStore,
    session_id: str,
    local_commit: dict[str, Any],
    decisions: list[dict[str, Any]],
    *,
    retry: bool = False,
) -> tuple[dict[str, Any], bool]:
    resource_identity = current_runtime_identity()
    identity = _job_identity(
        session_id=session_id,
        revision=int(local_commit["revision"]),
        input_sha256=str(local_commit["input_sha256"]),
        decisions=decisions,
        resource_identity=resource_identity,
    )
    with store._session_lock(session_id):
        existing = read_gallery_job(store, session_id)
        if (
            existing
            and existing.get("identity", {}).get("identity_sha256")
            == identity["identity_sha256"]
            and existing.get("status") in {"running", "completed"}
            and not retry
        ):
            return existing, False
        job_id = (
            str(existing.get("job_id"))
            if existing
            and existing.get("identity", {}).get("identity_sha256")
            == identity["identity_sha256"]
            else uuid.uuid4().hex
        )
        attempt_id = uuid.uuid4().hex
        now = _now()
        attempt_dir = (
            store._stage_path(session_id, "asset_matching")
            / "gallery-attempts"
            / attempt_id
        )
        attempt_dir.mkdir(parents=True, exist_ok=True)
        job = {
            "schema_version": GALLERY_JOB_SCHEMA_VERSION,
            "job_schema_version": GALLERY_JOB_SCHEMA_VERSION,
            "job_id": job_id,
            "attempt_id": attempt_id,
            "status": "running",
            "identity": identity,
            "created_at": (
                existing.get("created_at") if existing else _iso(now)
            ),
            "updated_at": _iso(now),
            "heartbeat_at": _iso(now),
            "lease_expires_at": _iso(
                now + timedelta(seconds=GALLERY_JOB_LEASE_SECONDS)
            ),
            "pid": None,
            "progress": {
                "workflow_step": "gallery_preparing",
                "current_product": None,
                "current_folder": None,
                "discovered_count": 0,
                "prepared_count": 0,
                "discovered_path_count": 0,
                "planned_inspection_count": 0,
                "inspected_count": 0,
                "inspection_failure_count": 0,
                "content_duplicate_count": 0,
                "final_candidate_count": 0,
                "pending_count": 0,
            },
            "reason_code": "",
            "message": "",
            "recovery_action": "重试加载图片",
        }
        store._write_json_atomic(gallery_job_path(store, session_id), job)
        store._write_json_atomic(attempt_dir / "progress.json", job)
        store._append_event(
            store._session_path(session_id),
            "local_gallery_retried" if retry else "local_gallery_started",
            session_id=session_id,
            stage_id="asset_matching",
            revision=int(local_commit["revision"]),
            job_id=job_id,
            attempt_id=attempt_id,
        )
        return job, True


def launch_gallery_worker(
    store: SessionStore, session_id: str, job: dict[str, Any]
) -> dict[str, Any]:
    stage_path = store._stage_path(session_id, "asset_matching")
    log_dir = store._session_path(session_id) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = log_dir / "gallery-worker.stdout.log"
    stderr_path = log_dir / "gallery-worker.stderr.log"
    command = [
        sys.executable,
        "-m",
        "upload_search_materials.cli",
        "process-gallery-job",
        "--runs-root",
        str(store.runs_root),
        "--session",
        session_id,
        "--job",
        str(job["job_id"]),
        "--attempt",
        str(job["attempt_id"]),
    ]
    creationflags = 0
    startupinfo = None
    if sys.platform == "win32":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
    with stdout_path.open("ab") as stdout, stderr_path.open("ab") as stderr:
        process = subprocess.Popen(
            command,
            cwd=str(Path(__file__).resolve().parents[2]),
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            close_fds=True,
            creationflags=creationflags,
            startupinfo=startupinfo,
        )
    with store._session_lock(session_id):
        current = read_gallery_job(store, session_id)
        if (
            current is None
            or current.get("job_id") != job.get("job_id")
            or current.get("attempt_id") != job.get("attempt_id")
        ):
            raise InteractionConflict(GALLERY_IDENTITY_STALE)
        current["pid"] = int(process.pid)
        current["updated_at"] = _iso()
        store._write_json_atomic(stage_path / "gallery-job.json", current)
        return current


def _classify_error(error: Exception) -> str:
    if isinstance(error, LocalResourceIdentityMismatch):
        return GALLERY_RESOURCE_IDENTITY_MISMATCH
    if "not readable" in str(error).casefold():
        return GALLERY_PATH_UNREADABLE
    if isinstance(error, InteractionConflict):
        return GALLERY_IDENTITY_STALE
    return GALLERY_JOB_FAILED


def process_gallery_job(
    store: SessionStore,
    session_id: str,
    job_id: str,
    attempt_id: str,
) -> dict[str, Any]:
    stage_path = store._stage_path(session_id, "asset_matching")
    attempt_dir = stage_path / "gallery-attempts" / attempt_id
    try:
        with store._session_lock(session_id):
            job = read_gallery_job(store, session_id)
            if (
                job is None
                or job.get("job_id") != job_id
                or job.get("attempt_id") != attempt_id
                or job.get("status") != "running"
            ):
                raise InteractionConflict(GALLERY_IDENTITY_STALE)
        require_local_resource_identity(
            job["identity"]["local_resource_identity"]
        )
        input_path = stage_path / "input.json"
        actual_input_sha = hashlib.sha256(input_path.read_bytes()).hexdigest()
        if actual_input_sha != job["identity"]["input_sha256"]:
            raise InteractionConflict(GALLERY_IDENTITY_STALE)
        input_document = store._read_json(input_path, "input")
        decisions = extract_folder_decisions(input_document)
        if (
            folder_decisions_sha256(decisions)
            != job["identity"]["folder_decisions_sha256"]
        ):
            raise InteractionConflict(GALLERY_IDENTITY_STALE)
        session_path = store._session_path(session_id)
        products = read_product_csv(session_path / "inputs" / "products.csv")
        status_path = (
            session_path
            / "collected"
            / "promotion"
            / "current"
            / "promotion-material-status.csv"
        )
        if not status_path.is_file():
            status_path = (
                session_path
                / "collected"
                / "promotion"
                / "promotion-material-status.csv"
            )
        with status_path.open("r", encoding="utf-8-sig", newline="") as stream:
            status_rows = list(csv.DictReader(stream))

        def update_progress(progress: dict[str, Any]) -> None:
            with store._session_lock(session_id):
                active = read_gallery_job(store, session_id)
                if (
                    active is None
                    or active.get("job_id") != job_id
                    or active.get("attempt_id") != attempt_id
                    or active.get("status") != "running"
                ):
                    raise InteractionConflict(GALLERY_IDENTITY_STALE)
                now = _now()
                active["progress"] = {
                    "workflow_step": "gallery_preparing",
                    **progress,
                }
                active["heartbeat_at"] = _iso(now)
                active["updated_at"] = _iso(now)
                active["lease_expires_at"] = _iso(
                    now + timedelta(seconds=GALLERY_JOB_LEASE_SECONDS)
                )
                store._write_json_atomic(
                    gallery_job_path(store, session_id), active
                )
                store._write_json_atomic(
                    attempt_dir / "progress.json", active
                )

        data = build_confirmed_folder_gallery(
            products,
            status_rows,
            decisions,
            candidate_limit=100,
            page_size=30,
            sampling_seed=session_id,
            preview_dir=stage_path / "preview-cache",
            progress_callback=update_progress,
        )
        prior = store.read_optional_stage_document(
            session_id, "asset_matching", "review-context"
        )
        prior_data = (
            prior.get("data")
            if isinstance(prior, dict) and isinstance(prior.get("data"), dict)
            else {}
        )
        folder_candidates = [
            dict(item)
            for item in prior_data.get("folder_candidates", [])
            if isinstance(item, dict)
        ]
        allocation_by_folder: dict[
            tuple[str, str], dict[str, Any]
        ] = {}
        for product_summary in data.get("scan_summary", {}).get(
            "per_product", []
        ):
            if not isinstance(product_summary, dict):
                continue
            product_id = str(
                product_summary.get("product_id", "")
            )
            for allocation in product_summary.get(
                "folder_allocations", []
            ):
                if isinstance(allocation, dict):
                    allocation_by_folder[
                        (
                            product_id,
                            str(allocation.get("folder_id", "")),
                        )
                    ] = allocation
        for candidate in folder_candidates:
            allocation = allocation_by_folder.get(
                (
                    str(candidate.get("product_id", "")),
                    str(candidate.get("folder_id", "")),
                )
            )
            if allocation is None:
                continue
            candidate.update(
                {
                    "image_count_status": "ready",
                    "raw_recursive_image_count": int(
                        allocation.get("raw_discovered_images", 0)
                    ),
                    "gallery_unique_path_count": int(
                        allocation.get("discovered_images", 0)
                    ),
                    "gallery_sampled_inspection_count": int(
                        allocation.get("sampled_images", 0)
                    ),
                    "gallery_final_candidate_count": int(
                        allocation.get("final_candidate_count", 0)
                    ),
                    "image_count_reason_code": str(
                        allocation.get("zero_allocation_reason", "")
                    ),
                }
            )
        data["folder_candidates"] = folder_candidates
        data["workflow_step"] = IMAGE_SELECTION
        data["gallery_identity"] = build_gallery_identity(
            session_id=session_id,
            revision=int(job["identity"]["revision"]),
            input_sha256=str(job["identity"]["input_sha256"]),
            folder_decisions=decisions,
        )
        data["gallery_job_id"] = job_id
        with store._session_lock(session_id):
            current = read_gallery_job(store, session_id)
            current_input_sha = hashlib.sha256(input_path.read_bytes()).hexdigest()
            if (
                current is None
                or current.get("job_id") != job_id
                or current.get("attempt_id") != attempt_id
                or current.get("identity", {}).get("identity_sha256")
                != job["identity"]["identity_sha256"]
                or current_input_sha != job["identity"]["input_sha256"]
            ):
                raise InteractionConflict(GALLERY_IDENTITY_STALE)
            store._write_json_atomic(
                stage_path / "confirmed-gallery.json", data
            )
            review = {
                "schema_version": 1,
                "session_id": session_id,
                "stage_id": "asset_matching",
                "revision": int(job["identity"]["revision"]),
                "status": "needs_user_input",
                "summary": (
                    f"已为 {len(data.get('requirements', []))} 个商品准备 "
                    f"{len(data.get('asset_candidates', []))} 张候选图片"
                ),
                "blocking_reasons": [],
                "evidence": [str(stage_path / "confirmed-gallery.json")],
                "next_action": "选择图片并提交给 Codex",
                "created_at": _iso(),
                "data": data,
            }
            store.write_review_context(
                session_id, "asset_matching", review
            )
            current["status"] = "completed"
            current["updated_at"] = _iso()
            current["heartbeat_at"] = _iso()
            current["lease_expires_at"] = None
            current["progress"] = {
                "workflow_step": IMAGE_SELECTION,
                "current_product": None,
                "current_folder": None,
                "discovered_count": int(
                    data.get("scan_summary", {}).get("discovered_images", 0)
                ),
                "prepared_count": len(data.get("asset_candidates", [])),
                "discovered_path_count": int(
                    data.get("scan_summary", {}).get(
                        "discovered_path_count", 0
                    )
                ),
                "planned_inspection_count": int(
                    data.get("scan_summary", {}).get(
                        "planned_inspection_count", 0
                    )
                ),
                "inspected_count": int(
                    data.get("scan_summary", {}).get(
                        "inspected_count", 0
                    )
                ),
                "inspection_failure_count": int(
                    data.get("scan_summary", {}).get(
                        "inspection_failure_count", 0
                    )
                ),
                "content_duplicate_count": int(
                    data.get("scan_summary", {}).get(
                        "content_duplicate_count", 0
                    )
                ),
                "final_candidate_count": len(
                    data.get("asset_candidates", [])
                ),
                "pending_count": 0,
            }
            current["result"] = {
                "candidate_count": len(data.get("asset_candidates", [])),
                "product_count": len(data.get("requirements", [])),
                **current["progress"],
            }
            current["recovery_action"] = None
            store._write_json_atomic(
                gallery_job_path(store, session_id), current
            )
            store._write_json_atomic(attempt_dir / "result.json", review)
            store._append_event(
                store._session_path(session_id),
                "local_gallery_completed",
                session_id=session_id,
                stage_id="asset_matching",
                revision=int(job["identity"]["revision"]),
                job_id=job_id,
                attempt_id=attempt_id,
            )
            return current
    except (
        OSError,
        SchemaError,
        ValueError,
        InteractionConflict,
        LocalResourceIdentityMismatch,
    ) as error:
        reason = _classify_error(error)
        with store._session_lock(session_id):
            current = read_gallery_job(store, session_id)
            failure = {
                "schema_version": GALLERY_JOB_SCHEMA_VERSION,
                "job_id": job_id,
                "attempt_id": attempt_id,
                "status": (
                    "stale"
                    if reason == GALLERY_IDENTITY_STALE
                    else "failed"
                ),
                "reason_code": reason,
                "message": str(error),
                "created_at": _iso(),
                "recovery_action": "重试加载图片",
            }
            attempt_dir.mkdir(parents=True, exist_ok=True)
            store._write_json_atomic(attempt_dir / "error.json", failure)
            if (
                current
                and current.get("job_id") == job_id
                and current.get("attempt_id") == attempt_id
            ):
                current.update(
                    {
                        "status": failure["status"],
                        "reason_code": reason,
                        "message": str(error),
                        "updated_at": _iso(),
                        "heartbeat_at": _iso(),
                        "lease_expires_at": None,
                        "recovery_action": "重试加载图片",
                    }
                )
                store._write_json_atomic(
                    gallery_job_path(store, session_id), current
                )
            store._append_event(
                store._session_path(session_id),
                "local_gallery_stale"
                if reason == GALLERY_IDENTITY_STALE
                else "local_gallery_failed",
                session_id=session_id,
                stage_id="asset_matching",
                job_id=job_id,
                attempt_id=attempt_id,
                reason_code=reason,
            )
        raise
