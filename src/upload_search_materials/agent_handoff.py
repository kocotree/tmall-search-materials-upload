"""Filesystem handoff between the local UI and the current Codex task."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import time
from typing import Any, Iterable, Mapping
from uuid import uuid4

from PIL import Image, ImageOps, UnidentifiedImageError

from .interaction.session import InteractionConflict, SessionStore


PROVIDER_ID = "codex-agent-handoff"
REQUEST_SCHEMA_VERSION = 1
SUPPORTED_REQUEST_SCHEMA_VERSIONS = frozenset({REQUEST_SCHEMA_VERSION})
REQUEST_WORKFLOW_SCHEMA_VERSION = 2
ANALYSIS_SCHEMA_VERSION = 2
SLOT_PLAN_SCHEMA_VERSION = 2
WORKFLOW_MIGRATION_ID = "make-ai-default-slot-planning-v1"
AI_DEFAULT_SLOT_PLANNING_ENV = "UPLOAD_SEARCH_MATERIALS_AI_DEFAULT_SLOT_PLANNING"
THREE_STEP_SLOT_UI_ENV = "UPLOAD_SEARCH_MATERIALS_THREE_STEP_SLOT_UI"
REQUEST_STATES = frozenset(
    {
        "pending_agent",
        "processing",
        "completed",
        "failed",
        "superseded",
        "cancelled",
    }
)
REQUEST_KINDS = frozenset(
    {
        "image_analysis",
        "slot_plan",
        "slot_plan_with_analysis",
        "crop_suggestion",
        "copy_draft",
    }
)


def ai_default_slot_planning_enabled() -> bool:
    """Return the rollback-controlled default for the fifth-stage workflow."""

    return os.getenv(AI_DEFAULT_SLOT_PLANNING_ENV, "1").strip().casefold() not in {
        "0",
        "false",
        "no",
        "off",
    }


def three_step_slot_ui_enabled() -> bool:
    """Return whether the simplified fifth-stage UI is enabled."""

    return os.getenv(THREE_STEP_SLOT_UI_ENV, "1").strip().casefold() not in {
        "0",
        "false",
        "no",
        "off",
    }


class AgentRequestError(ValueError):
    """A stable, user-actionable Agent request failure."""

    def __init__(
        self,
        reason_code: str,
        message: str,
        *,
        allowed_actions: tuple[str, ...] = (),
    ) -> None:
        super().__init__(f"{reason_code}: {message}")
        self.reason_code = reason_code
        self.message = message
        self.allowed_actions = allowed_actions


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_request_id(value: str) -> str:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{7,80}", value):
        raise ValueError("invalid request_id")
    return value


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f"tmp-{uuid4().hex[:8]}.json"
    temporary.write_text(
        json.dumps(dict(value), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(path)


def _request_root(store: SessionStore, session_id: str) -> Path:
    return (
        store._stage_path(session_id, "slots_copy") / "agent-requests"
    ).resolve()


def _request_path(
    store: SessionStore, session_id: str, request_id: str
) -> Path:
    root = _request_root(store, session_id)
    path = (root / _safe_request_id(request_id)).resolve()
    if not path.is_relative_to(root):
        raise ValueError("request path escaped task directory")
    return path


def build_controlled_thumbnail(
    source_path: Path,
    destination: Path,
    *,
    max_edge: int,
) -> dict[str, Any]:
    """Create a task-local JPEG; request files never reference the shared source."""

    if max_edge < 128 or max_edge > 2048:
        raise ValueError("thumbnail max_edge must be between 128 and 2048")
    source = Path(source_path).resolve()
    if not source.is_file():
        raise ValueError("source image is unavailable")
    destination = Path(destination).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.parent / f"tmp-{uuid4().hex[:8]}.jpg"
    try:
        with Image.open(source) as opened:
            image = ImageOps.exif_transpose(opened).convert("RGB")
            image.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
            image.save(temporary, format="JPEG", quality=82, optimize=True)
            width, height = image.size
        temporary.replace(destination)
    except (OSError, UnidentifiedImageError) as error:
        temporary.unlink(missing_ok=True)
        raise ValueError("thumbnail generation failed") from error
    return {
        "thumbnail_path": str(destination),
        "thumbnail_width": width,
        "thumbnail_height": height,
        "thumbnail_sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
        "thumbnail_size_bytes": destination.stat().st_size,
    }


def resolve_agent_thumbnail(
    store: SessionStore,
    session_id: str,
    candidate: Mapping[str, Any],
    destination: Path,
    *,
    max_edge: int,
) -> dict[str, Any]:
    """Resolve a verified task-local preview before touching shared storage."""

    asset_id = str(candidate.get("asset_id", "")).strip()
    source_sha256 = str(candidate.get("source_sha256", "")).strip()
    if not asset_id or len(source_sha256) != 64:
        raise AgentRequestError(
            "AGENT_CANDIDATE_IDENTITY_INCOMPLETE",
            "候选图片缺少稳定资产 ID 或源 SHA-256",
            allowed_actions=("retry_ai", "use_manual"),
        )
    session_root = store._session_path(session_id).resolve()
    preview_candidates = [
        (
            "image_review_cache",
            store._stage_path(session_id, "image_review")
            / "preview-cache"
            / f"{asset_id}.jpg",
        ),
        (
            "asset_matching_cache",
            store._stage_path(session_id, "asset_matching")
            / "preview-cache"
            / f"{asset_id}.jpg",
        ),
    ]
    supplied_preview = str(candidate.get("controlled_preview_path", "")).strip()
    if supplied_preview:
        preview_candidates.insert(
            0, ("supplied_task_cache", Path(supplied_preview))
        )
    for origin, preview in preview_candidates:
        try:
            resolved = preview.resolve()
            if (
                not resolved.is_relative_to(session_root)
                or not resolved.is_file()
                or resolved.suffix.casefold() not in {".jpg", ".jpeg", ".png", ".webp"}
            ):
                continue
            result = build_controlled_thumbnail(
                resolved, destination, max_edge=max_edge
            )
        except (OSError, RuntimeError, ValueError):
            continue
        result.update(
            {
                "thumbnail_origin": origin,
                "source_identity_sha256": source_sha256,
                "asset_id": asset_id,
            }
        )
        return result

    source_path = Path(str(candidate.get("source_path", "")))
    try:
        result = build_controlled_thumbnail(
            source_path, destination, max_edge=max_edge
        )
    except (OSError, RuntimeError, ValueError) as error:
        raise AgentRequestError(
            "AGENT_THUMBNAIL_UNAVAILABLE",
            "任务缓存和原始共享盘图片均不可用",
            allowed_actions=("retry_cache", "retry_ai", "use_manual"),
        ) from error
    result.update(
        {
            "thumbnail_origin": "shared_source",
            "source_identity_sha256": source_sha256,
            "asset_id": asset_id,
        }
    )
    return result


def create_agent_request(
    store: SessionStore,
    session_id: str,
    *,
    kind: str,
    candidates: Iterable[Mapping[str, Any]],
    context_revision: int,
    max_images: int = 30,
    max_edge: int = 768,
    max_proposals: int = 2,
    response_model: str = "codex-current-task",
    request_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist an explicit request after enforcing per-request image budgets."""

    if kind not in REQUEST_KINDS:
        raise AgentRequestError(
            "AGENT_REQUEST_KIND_UNSUPPORTED",
            "不支持的 Agent 请求类型",
            allowed_actions=("retry_ai", "use_manual"),
        )
    if not 1 <= max_images <= 100:
        raise AgentRequestError(
            "AGENT_IMAGE_BUDGET_INVALID",
            "图片预算必须在 1–100 之间",
            allowed_actions=("retry_ai", "use_manual"),
        )
    if not 1 <= max_proposals <= 3:
        raise AgentRequestError(
            "AGENT_PROPOSAL_BUDGET_INVALID",
            "方案预算必须在 1–3 之间",
            allowed_actions=("retry_ai", "use_manual"),
        )
    selected = [dict(item) for item in candidates]
    if len(selected) > max_images:
        raise AgentRequestError(
            "AGENT_IMAGE_BUDGET_EXCEEDED",
            f"候选图片 {len(selected)} 张，超过本次预算 {max_images} 张",
            allowed_actions=("reduce_candidates", "retry_ai", "use_manual"),
        )
    if not selected and kind != "copy_draft":
        raise AgentRequestError(
            "AGENT_CANDIDATES_EMPTY",
            "当前没有可提交给 Agent 的候选图片",
            allowed_actions=("retry_ai", "use_manual"),
        )
    request_id = (
        datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        + "-"
        + uuid4().hex[:10]
    )
    root = _request_path(store, session_id, request_id)
    requests_root = _request_root(store, session_id)
    requests_root.mkdir(parents=True, exist_ok=True)
    temporary_root = requests_root / f"tmp-{uuid4().hex[:10]}"
    temporary_root.mkdir()
    try:
        request_candidates: list[dict[str, Any]] = []
        cache_hits = 0
        for index, candidate in enumerate(selected, start=1):
            asset_id = str(candidate.get("asset_id", "")).strip()
            source_sha256 = str(candidate.get("source_sha256", "")).strip()
            filename = f"{index:03d}-{asset_id}.jpg"
            thumbnail = resolve_agent_thumbnail(
                store,
                session_id,
                candidate,
                temporary_root / "thumbnails" / filename,
                max_edge=max_edge,
            )
            thumbnail["thumbnail_path"] = str(root / "thumbnails" / filename)
            request_candidate = {
                    "asset_id": asset_id,
                    "product_id": str(candidate.get("product_id", "")),
                    "product_title": str(candidate.get("product_title", "")),
                    "source_sha256": source_sha256,
                    "source_system": str(candidate.get("source_system", "")),
                    "width": candidate.get("width"),
                    "height": candidate.get("height"),
                    "size_bytes": candidate.get("size_bytes"),
                    "size_display": str(candidate.get("size_display", "")),
                    "original_ratio": str(
                        candidate.get("original_ratio", "")
                    ),
                    "format": str(candidate.get("format", "")),
                    "ratio_options": candidate.get("ratio_options", {}),
                    "processing_risk": candidate.get("processing_risk", {}),
                    "duplicate_group": candidate.get("duplicate_group"),
                    "information_gain": candidate.get("information_gain"),
                    **thumbnail,
                }
            if kind == "slot_plan_with_analysis":
                cached = read_analysis_cache(
                    store,
                    session_id,
                    source_sha256=source_sha256,
                    response_model=response_model,
                    analysis_schema_version=ANALYSIS_SCHEMA_VERSION,
                )
                if cached is not None:
                    request_candidate["cached_analysis"] = cached["analysis"]
                    cache_hits += 1
            request_candidates.append(request_candidate)
        created_at = _now()
        request = {
            "schema_version": REQUEST_SCHEMA_VERSION,
            "workflow_schema_version": REQUEST_WORKFLOW_SCHEMA_VERSION,
            "provider_id": PROVIDER_ID,
            "request_id": request_id,
            "session_id": session_id,
            "kind": kind,
            "status": "pending_agent",
            "context_revision": int(context_revision),
            "created_at": created_at,
            "status_updated_at": created_at,
            "status_actor": "user",
            "reason_code": None,
            "claimed_at": None,
            "completed_at": None,
            "response_model": response_model,
            "analysis_schema_version": ANALYSIS_SCHEMA_VERSION,
            "slot_plan_schema_version": SLOT_PLAN_SCHEMA_VERSION,
            "workflow_migration_id": WORKFLOW_MIGRATION_ID,
            "request_context": dict(request_context or {}),
            "analysis_cache": {
                "hit_count": cache_hits,
                "pending_count": len(request_candidates) - cache_hits,
                "candidate_count": len(request_candidates),
            },
            "budget": {
                "max_images": max_images,
                "thumbnail_max_edge": max_edge,
                "max_proposals": max_proposals,
            },
            "candidates": request_candidates,
            "transitions": [{
                "from": None,
                "to": "pending_agent",
                "actor": "user",
                "created_at": created_at,
                "reason_code": None,
            }],
        }
        request["recovery_prompt"] = recovery_prompt(
            store, session_id, request_id, kind=kind
        )
        _atomic_json(temporary_root / "request.json", request)
        temporary_root.replace(root)
        return request
    except BaseException:
        shutil.rmtree(temporary_root, ignore_errors=True)
        raise


def read_agent_request(
    store: SessionStore, session_id: str, request_id: str
) -> dict[str, Any]:
    path = _request_path(store, session_id, request_id) / "request.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise InteractionConflict("Agent request is unavailable or invalid") from error
    if (
        not isinstance(value, dict)
        or value.get("schema_version") not in SUPPORTED_REQUEST_SCHEMA_VERSIONS
        or value.get("provider_id") != PROVIDER_ID
        or value.get("request_id") != request_id
        or value.get("session_id") != session_id
        or value.get("status") not in REQUEST_STATES
    ):
        raise InteractionConflict("Agent request identity is invalid")
    if value.get("kind") == "copy_draft":
        # Project the current execution contract even for durable requests
        # created by an older Plugin.  This keeps a resumed Agent from asking
        # for an extra chat confirmation before it invokes the processor.
        value["recovery_prompt"] = recovery_prompt(
            store, session_id, request_id, kind="copy_draft"
        )
    return value


def find_equivalent_agent_request(
    store: SessionStore,
    session_id: str,
    *,
    kind: str,
    context_revision: int,
    context_fingerprint: str | None = None,
) -> dict[str, Any] | None:
    """Find an existing request so page refresh and polling stay side-effect free."""

    for request in list_agent_requests(store, session_id):
        if (
            request.get("kind") == kind
            and int(request.get("context_revision", -1)) == int(context_revision)
            and (
                context_fingerprint is None
                or str(
                    request.get("request_context", {}).get(
                        "context_fingerprint", ""
                    )
                )
                == context_fingerprint
            )
            and request.get("status") in {"pending_agent", "processing", "completed"}
        ):
            return request
    return None


def list_agent_requests(
    store: SessionStore,
    session_id: str,
    *,
    statuses: set[str] | None = None,
) -> list[dict[str, Any]]:
    """List durable requests newest-first, optionally filtered by status."""

    root = _request_root(store, session_id)
    if not root.is_dir():
        return []
    values: list[dict[str, Any]] = []
    for path in sorted(root.iterdir(), reverse=True):
        if not path.is_dir() or path.name.startswith("tmp-"):
            continue
        try:
            value = read_agent_request(store, session_id, path.name)
        except (InteractionConflict, ValueError):
            continue
        if statuses is None or value["status"] in statuses:
            values.append(value)
    return values


def wait_for_agent_request(
    store: SessionStore,
    session_id: str,
    *,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    """Wait for the newest pending request without claiming it."""

    deadline = (
        None
        if timeout_seconds is None
        else time.monotonic() + timeout_seconds
    )
    while True:
        pending = list_agent_requests(
            store, session_id, statuses={"pending_agent"}
        )
        if pending:
            return pending[0]
        if deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError("timed out waiting for Agent request")
        time.sleep(0.25)


def claim_agent_request(
    store: SessionStore,
    session_id: str,
    request_id: str,
    *,
    actor: str = "codex-agent",
) -> dict[str, Any]:
    request = read_agent_request(store, session_id, request_id)
    if request["status"] == "processing":
        return request
    if request["status"] != "pending_agent":
        raise InteractionConflict("Agent request is not claimable")
    return _transition_agent_request(
        store,
        session_id,
        request,
        "processing",
        actor=actor,
        allowed_from={"pending_agent"},
    )


def retry_agent_request(
    store: SessionStore,
    session_id: str,
    request_id: str,
    *,
    actor: str = "codex-agent",
) -> dict[str, Any]:
    """Explicitly requeue one failed request for its idempotent processor."""

    request = read_agent_request(store, session_id, request_id)
    if request["status"] == "pending_agent":
        return request
    if request["status"] != "failed":
        raise InteractionConflict("Agent request is not retryable")
    retried = _transition_agent_request(
        store,
        session_id,
        request,
        "pending_agent",
        actor=actor,
        reason_code="AGENT_REQUEST_RETRY",
        allowed_from={"failed"},
    )
    retried["claimed_at"] = None
    retried["completed_at"] = None
    _atomic_json(
        _request_path(store, session_id, request_id) / "request.json",
        retried,
    )
    return retried


def fail_agent_request(
    store: SessionStore,
    session_id: str,
    request_id: str,
    *,
    actor: str,
    reason_code: str,
) -> dict[str, Any]:
    request = read_agent_request(store, session_id, request_id)
    if request["status"] == "failed":
        return request
    return _transition_agent_request(
        store,
        session_id,
        request,
        "failed",
        actor=actor,
        reason_code=reason_code,
        allowed_from={"pending_agent", "processing"},
    )


def cancel_agent_request(
    store: SessionStore,
    session_id: str,
    request_id: str,
    *,
    actor: str = "user",
    reason_code: str = "AGENT_REQUEST_CANCELLED",
) -> dict[str, Any]:
    request = read_agent_request(store, session_id, request_id)
    if request["status"] == "cancelled":
        return request
    return _transition_agent_request(
        store,
        session_id,
        request,
        "cancelled",
        actor=actor,
        reason_code=reason_code,
        allowed_from={"pending_agent", "processing"},
    )


def supersede_agent_request(
    store: SessionStore,
    session_id: str,
    request_id: str,
    *,
    actor: str = "system",
    reason_code: str = "AGENT_REQUEST_SUPERSEDED",
) -> dict[str, Any]:
    request = read_agent_request(store, session_id, request_id)
    if request["status"] == "superseded":
        return request
    return _transition_agent_request(
        store,
        session_id,
        request,
        "superseded",
        actor=actor,
        reason_code=reason_code,
        allowed_from={"pending_agent", "processing"},
    )


def _transition_agent_request(
    store: SessionStore,
    session_id: str,
    request: dict[str, Any],
    target: str,
    *,
    actor: str,
    reason_code: str | None = None,
    allowed_from: set[str],
) -> dict[str, Any]:
    if request["status"] not in allowed_from:
        raise InteractionConflict(
            f"Agent request cannot transition from {request['status']} to {target}"
        )
    previous = request["status"]
    created_at = _now()
    request["status"] = target
    request["status_updated_at"] = created_at
    request["status_actor"] = actor
    request["reason_code"] = reason_code
    request.setdefault("transitions", []).append(
        {
            "from": previous,
            "to": target,
            "actor": actor,
            "created_at": created_at,
            "reason_code": reason_code,
        }
    )
    if target == "processing":
        request["claimed_at"] = created_at
    if target in {"completed", "failed", "cancelled", "superseded"}:
        request["completed_at"] = created_at
    _atomic_json(
        _request_path(store, session_id, request["request_id"])
        / "request.json",
        request,
    )
    store.append_decision_event(
        session_id,
        "slots_copy",
        "slot_plan",
        "agent_request_transitioned",
        request_id=request["request_id"],
        previous_status=previous,
        status=target,
        actor=actor,
        reason_code=reason_code,
    )
    return request


def _validate_response(
    request: Mapping[str, Any], response: Mapping[str, Any]
) -> dict[str, Any]:
    if response.get("request_id") != request.get("request_id"):
        raise ValueError("AGENT_RESPONSE_IDENTITY_MISMATCH")
    if response.get("kind") != request.get("kind"):
        raise ValueError("AGENT_RESPONSE_KIND_MISMATCH")
    if not isinstance(response.get("result"), Mapping):
        raise ValueError("AGENT_RESPONSE_SCHEMA_INVALID")
    if request.get("kind") == "copy_draft":
        normalized_result = _validate_copy_response(
            request, response["result"]
        )
        return {
            "schema_version": REQUEST_SCHEMA_VERSION,
            "workflow_schema_version": REQUEST_WORKFLOW_SCHEMA_VERSION,
            "provider_id": str(
                response.get("provider_id") or PROVIDER_ID
            ),
            "request_id": request["request_id"],
            "session_id": request["session_id"],
            "kind": request["kind"],
            "response_model": str(
                response.get("response_model")
                or request.get("response_model")
                or "codex-current-task"
            ),
            "result": normalized_result,
            "created_at": _now(),
        }
    if request.get("kind") == "slot_plan_with_analysis":
        normalized_result = _validate_combined_slot_response(
            request, response["result"]
        )
        return {
            "schema_version": REQUEST_SCHEMA_VERSION,
            "provider_id": PROVIDER_ID,
            "request_id": request["request_id"],
            "session_id": request["session_id"],
            "kind": request["kind"],
            "response_model": str(
                response.get("response_model")
                or request.get("response_model")
                or "codex-current-task"
            ),
            "analysis_schema_version": int(
                response.get(
                    "analysis_schema_version", ANALYSIS_SCHEMA_VERSION
                )
            ),
            "slot_plan_schema_version": SLOT_PLAN_SCHEMA_VERSION,
            "result": normalized_result,
            "created_at": _now(),
        }
    proposals = response["result"].get("proposals", [])
    if not isinstance(proposals, list):
        raise ValueError("AGENT_RESPONSE_SCHEMA_INVALID")
    maximum = int(request.get("budget", {}).get("max_proposals", 2))
    if len(proposals) > maximum:
        raise ValueError("AGENT_PROPOSAL_BUDGET_EXCEEDED")
    if request.get("kind") == "slot_plan":
        candidates = {
            str(item.get("asset_id", "")): item
            for item in request.get("candidates", [])
            if isinstance(item, Mapping)
        }
        normalized_proposals: list[dict[str, Any]] = []
        for proposal in proposals:
            if not isinstance(proposal, Mapping):
                raise ValueError("AGENT_RESPONSE_SCHEMA_INVALID")
            ratio = str(proposal.get("target_ratio", ""))
            product_id = str(proposal.get("product_id", ""))
            asset_ids = proposal.get("ordered_asset_ids")
            crop_count = proposal.get("estimated_crop_count")
            compression_count = proposal.get("estimated_compression_count")
            if (
                ratio not in {"1:1", "3:4"}
                or not isinstance(asset_ids, list)
                or not 3 <= len(asset_ids) <= 9
                or len({str(value) for value in asset_ids}) != len(asset_ids)
                or not str(proposal.get("reason", "")).strip()
                or isinstance(proposal.get("ai_confidence"), bool)
                or not isinstance(proposal.get("ai_confidence"), (int, float))
                or not 0 <= float(proposal["ai_confidence"]) <= 1
                or isinstance(crop_count, bool)
                or not isinstance(crop_count, int)
                or not 0 <= crop_count <= len(asset_ids)
                or isinstance(compression_count, bool)
                or not isinstance(compression_count, int)
                or not 0 <= compression_count <= len(asset_ids)
                or not str(proposal.get("crop_risk", "")).strip()
                or not str(proposal.get("diversity_summary", "")).strip()
                or not str(proposal.get("duplicate_summary", "")).strip()
            ):
                raise ValueError("AGENT_SLOT_PLAN_HARD_RULE_REJECTED")
            for asset_id in map(str, asset_ids):
                candidate = candidates.get(asset_id)
                if (
                    not candidate
                    or str(candidate.get("product_id", "")) != product_id
                    or ratio not in candidate.get("ratio_options", {})
                ):
                    raise ValueError("AGENT_SLOT_PLAN_HARD_RULE_REJECTED")
            product_title = next(
                (
                    str(candidates[str(asset_id)].get("product_title", ""))
                    for asset_id in asset_ids
                    if str(candidates[str(asset_id)].get("product_title", ""))
                ),
                str(proposal.get("product_title", "")),
            )
            normalized_proposals.append(
                {
                    **dict(proposal),
                    "product_title": product_title,
                    "plan_source": "agent_assisted",
                }
            )
        proposals = normalized_proposals
    normalized_result = dict(response["result"])
    normalized_result["proposals"] = proposals
    return {
        "schema_version": REQUEST_SCHEMA_VERSION,
        "workflow_schema_version": REQUEST_WORKFLOW_SCHEMA_VERSION,
        "provider_id": PROVIDER_ID,
        "request_id": request["request_id"],
        "session_id": request["session_id"],
        "kind": request["kind"],
        "response_model": str(
            response.get("response_model")
            or request.get("response_model")
            or "codex-current-task"
        ),
        "analysis_schema_version": int(
            response.get("analysis_schema_version", 1)
        ),
        "result": normalized_result,
        "created_at": _now(),
    }


def _validate_copy_response(
    request: Mapping[str, Any], result: Mapping[str, Any]
) -> dict[str, Any]:
    from .copywriting import validate_copy

    drafts = result.get("copy_drafts")
    if not isinstance(drafts, list):
        raise ValueError("AGENT_COPY_RESPONSE_SCHEMA_INVALID")
    context = request.get("request_context", {})
    slots = {
        str(item.get("slot_id", "")): item
        for item in context.get("slots", [])
        if isinstance(item, Mapping)
    }
    prohibited = [
        str(value)
        for value in context.get("prohibited_terms", [])
        if str(value)
    ]
    normalized = []
    seen: set[str] = set()
    for draft in drafts:
        if not isinstance(draft, Mapping):
            raise ValueError("AGENT_COPY_RESPONSE_SCHEMA_INVALID")
        slot_id = str(draft.get("slot_id", ""))
        slot = slots.get(slot_id)
        title = str(draft.get("title", "")).strip()
        description = str(draft.get("description", "")).strip()
        evidence = draft.get("evidence")
        risks = draft.get("risks", [])
        if (
            not slot
            or slot_id in seen
            or not isinstance(evidence, list)
            or not all(str(value).strip() for value in evidence)
            or not isinstance(risks, list)
        ):
            raise ValueError("AGENT_COPY_RESPONSE_SCHEMA_INVALID")
        reason_codes = validate_copy(
            title,
            description,
            {
                str(key): str(value)
                for key, value in dict(
                    slot.get("trusted_source_fields", {})
                ).items()
            },
            prohibited,
        )
        normalized.append(
            {
                **dict(draft),
                "slot_id": slot_id,
                "title": title,
                "description": description,
                "evidence": [str(value) for value in evidence],
                "risks": [str(value) for value in risks],
                "status": (
                    "needs_manual_review" if reason_codes else "valid"
                ),
                "reason_codes": reason_codes,
                "confirmed": False,
            }
        )
        seen.add(slot_id)
    missing = sorted(set(slots) - seen)
    if missing:
        raise ValueError("AGENT_COPY_RESPONSE_MISSING_SLOT")
    return {"copy_drafts": normalized}


_ANALYSIS_TEXT_FIELDS = (
    "scene",
    "subject",
    "shot_type",
    "angle",
    "pose",
    "product_visibility",
    "visual_style",
    "quality",
    "ratio_risk",
    "duplicate_group",
)


def _validate_combined_slot_response(
    request: Mapping[str, Any], result: Mapping[str, Any]
) -> dict[str, Any]:
    """Validate the combined analysis, clustering, and slot-plan envelope.

    Invalid slot combinations are isolated by product and returned as stable
    fallback records. Valid image analysis remains usable and cacheable.
    """

    analyses = result.get("asset_analysis")
    clusters = result.get("clusters")
    raw_slots = result.get("slot_plan")
    if not all(isinstance(value, list) for value in (analyses, clusters, raw_slots)):
        raise ValueError("AGENT_RESPONSE_SCHEMA_INVALID")
    candidates = {
        str(item.get("asset_id", "")): dict(item)
        for item in request.get("candidates", [])
        if isinstance(item, Mapping) and str(item.get("asset_id", ""))
    }
    normalized_analysis: list[dict[str, Any]] = []
    seen_analysis: set[str] = set()
    for item in analyses:
        if not isinstance(item, Mapping):
            raise ValueError("AGENT_ASSET_ANALYSIS_INVALID")
        asset_id = str(item.get("asset_id", ""))
        candidate = candidates.get(asset_id)
        if (
            not candidate
            or asset_id in seen_analysis
            or any(not str(item.get(field, "")).strip() for field in _ANALYSIS_TEXT_FIELDS)
        ):
            raise ValueError("AGENT_ASSET_ANALYSIS_INVALID")
        seen_analysis.add(asset_id)
        normalized_analysis.append(
            {
                **dict(item),
                "asset_id": asset_id,
                "product_id": str(candidate.get("product_id", "")),
                "source_sha256": str(candidate.get("source_sha256", "")),
            }
        )
    normalized_clusters: list[dict[str, Any]] = []
    for cluster in clusters:
        if not isinstance(cluster, Mapping):
            raise ValueError("AGENT_CLUSTER_SCHEMA_INVALID")
        product_id = str(cluster.get("product_id", ""))
        ratio = str(cluster.get("target_ratio", ""))
        asset_ids = [str(value) for value in cluster.get("asset_ids", [])]
        if (
            ratio not in {"1:1", "3:4"}
            or not str(cluster.get("theme", "")).strip()
            or not str(cluster.get("reason", "")).strip()
            or not asset_ids
            or any(
                asset_id not in candidates
                or str(candidates[asset_id].get("product_id", "")) != product_id
                or ratio not in candidates[asset_id].get("ratio_options", {})
                for asset_id in asset_ids
            )
        ):
            raise ValueError("AGENT_CLUSTER_SCHEMA_INVALID")
        normalized_clusters.append({**dict(cluster), "asset_ids": asset_ids})

    remaining_by_product = {
        str(key): int(value)
        for key, value in dict(
            request.get("request_context", {}).get("remaining_slots", {})
        ).items()
    }
    used_assets: set[str] = set()
    slot_counts: dict[str, int] = {}
    valid_slots: list[dict[str, Any]] = []
    fallback_by_product: dict[str, dict[str, Any]] = {}
    for raw in raw_slots:
        if not isinstance(raw, Mapping):
            raise ValueError("AGENT_SLOT_PLAN_SCHEMA_INVALID")
        product_id = str(raw.get("product_id", ""))
        ratio = str(raw.get("target_ratio", ""))
        asset_ids = [str(value) for value in raw.get("ordered_asset_ids", [])]
        image_roles = raw.get("image_roles")
        unused_reasons = raw.get("unused_reasons", [])
        crop_count = raw.get("estimated_crop_count")
        compression_count = raw.get("estimated_compression_count")
        invalid_reason = None
        if (
            ratio not in {"1:1", "3:4"}
            or not 3 <= len(asset_ids) <= 9
            or len(set(asset_ids)) != len(asset_ids)
            or not str(raw.get("theme", "")).strip()
            or not str(raw.get("quantity_reason", "")).strip()
            or not isinstance(image_roles, list)
            or len(image_roles) != len(asset_ids)
            or not isinstance(unused_reasons, list)
            or not all(str(value).strip() for value in unused_reasons)
            or isinstance(crop_count, bool)
            or not isinstance(crop_count, int)
            or not 0 <= crop_count <= len(asset_ids)
            or isinstance(compression_count, bool)
            or not isinstance(compression_count, int)
            or not 0 <= compression_count <= len(asset_ids)
        ):
            invalid_reason = "AGENT_SLOT_PLAN_HARD_RULE_REJECTED"
        elif any(
            asset_id not in candidates
            or str(candidates[asset_id].get("product_id", "")) != product_id
            or ratio not in candidates[asset_id].get("ratio_options", {})
            for asset_id in asset_ids
        ):
            invalid_reason = "AGENT_SLOT_PLAN_HARD_RULE_REJECTED"
        elif used_assets.intersection(asset_ids):
            invalid_reason = "AGENT_SLOT_PLAN_DUPLICATE_ASSET"
        elif slot_counts.get(product_id, 0) >= remaining_by_product.get(
            product_id, 9
        ):
            invalid_reason = "AGENT_SLOT_PLAN_CAPACITY_EXCEEDED"
        elif any(
            not isinstance(role, Mapping)
            or str(role.get("asset_id", "")) != asset_id
            or not str(role.get("role", "")).strip()
            or not str(role.get("reason", "")).strip()
            or not str(role.get("information_gain", "")).strip()
            for role, asset_id in zip(image_roles, asset_ids)
        ):
            invalid_reason = "AGENT_SLOT_PLAN_ROLE_INVALID"
        if invalid_reason:
            fallback_by_product.setdefault(
                product_id,
                {
                    "product_id": product_id,
                    "decision_source": "rules",
                    "fallback_from": "agent_assisted",
                    "reason_code": invalid_reason,
                },
            )
            continue
        used_assets.update(asset_ids)
        slot_counts[product_id] = slot_counts.get(product_id, 0) + 1
        valid_slots.append(
            {
                **dict(raw),
                "product_id": product_id,
                "target_ratio": ratio,
                "ordered_asset_ids": asset_ids,
                "plan_source": "agent_assisted",
            }
        )
    return {
        "asset_analysis": normalized_analysis,
        "clusters": normalized_clusters,
        "slot_plan": valid_slots,
        "fallback_products": list(fallback_by_product.values()),
        "unused_assets": list(result.get("unused_assets", [])),
    }


def complete_agent_request(
    store: SessionStore,
    session_id: str,
    request_id: str,
    response: Mapping[str, Any],
    *,
    actor: str = "codex-agent",
    allow_context_revision_drift: bool = False,
) -> dict[str, Any]:
    request = read_agent_request(store, session_id, request_id)
    if request["status"] != "processing":
        raise InteractionConflict("Agent request must be claimed before completion")
    current_revision = int(
        store.load_session(session_id)["stages"]["slots_copy"]["revision"]
    )
    if (
        current_revision != int(request.get("context_revision", -1))
        and not allow_context_revision_drift
    ):
        supersede_agent_request(
            store,
            session_id,
            request_id,
            reason_code="AGENT_REQUEST_STALE",
        )
        raise InteractionConflict("AGENT_REQUEST_STALE")
    try:
        validated = _validate_response(request, response)
    except ValueError as error:
        _transition_agent_request(
            store,
            session_id,
            request,
            "failed",
            actor=actor,
            reason_code=str(error),
            allowed_from={"processing"},
        )
        raise
    root = _request_path(store, session_id, request_id)
    _atomic_json(root / "response.json", validated)
    if request.get("kind") == "slot_plan_with_analysis":
        for analysis in validated.get("result", {}).get(
            "asset_analysis", []
        ):
            write_analysis_cache(
                store,
                session_id,
                source_sha256=str(analysis["source_sha256"]),
                response_model=str(validated["response_model"]),
                analysis_schema_version=int(
                    validated["analysis_schema_version"]
                ),
                analysis=analysis,
            )
    _transition_agent_request(
        store,
        session_id,
        request,
        "completed",
        actor=actor,
        allowed_from={"processing"},
    )
    return validated


def recovery_prompt(
    store: SessionStore,
    session_id: str,
    request_id: str,
    *,
    kind: str = "",
) -> str:
    request_path = _request_path(store, session_id, request_id) / "request.json"
    if kind == "copy_draft":
        return (
            "继续当前 upload-search-materials 任务，不要创建新 session。"
            f"读取文案请求：{request_path}；"
            "该请求已由用户在工作台完成图片处理时授予限定执行权限，"
            "直接调用唯一 process-copy-request 入口，复用项目现有千牛文案 "
            "Playwright 流程，逐坑上传首张已验证成品图、调用千牛内置 AI、"
            "读取文案并退出未发布表单。禁止在聊天中再次索取同意、授权或确认；"
            "不得点击填充文案、确认或发布，也不要自行生成商品文案。"
        )
    return (
        "继续当前 upload-search-materials 任务，不要创建新 session。"
        f"读取 Agent 请求：{request_path}；"
        f"领取 request_id={request_id} 后按 Schema 生成建议并原子写回 response.json。"
        "请求只引用任务目录缩略图，不要改写共享盘源文件。"
    )


def analysis_cache_key(
    source_sha256: str,
    *,
    response_model: str,
    analysis_schema_version: int,
) -> str:
    if len(source_sha256) != 64:
        raise ValueError("invalid source SHA-256")
    return hashlib.sha256(
        json.dumps(
            {
                "source_sha256": source_sha256,
                "provider_id": PROVIDER_ID,
                "response_model": response_model,
                "analysis_schema_version": int(analysis_schema_version),
            },
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def write_analysis_cache(
    store: SessionStore,
    session_id: str,
    *,
    source_sha256: str,
    response_model: str,
    analysis_schema_version: int,
    analysis: Mapping[str, Any],
) -> dict[str, Any]:
    key = analysis_cache_key(
        source_sha256,
        response_model=response_model,
        analysis_schema_version=analysis_schema_version,
    )
    document = {
        "schema_version": REQUEST_SCHEMA_VERSION,
        "workflow_schema_version": REQUEST_WORKFLOW_SCHEMA_VERSION,
        "cache_key": key,
        "provider_id": PROVIDER_ID,
        "source_sha256": source_sha256,
        "response_model": response_model,
        "analysis_schema_version": int(analysis_schema_version),
        "analysis": dict(analysis),
        "created_at": _now(),
    }
    root = (
        store._stage_path(session_id, "slots_copy") / "agent-analysis-cache"
    ).resolve()
    _atomic_json(root / f"{key[:32]}.json", document)
    return document


def read_analysis_cache(
    store: SessionStore,
    session_id: str,
    *,
    source_sha256: str,
    response_model: str,
    analysis_schema_version: int,
) -> dict[str, Any] | None:
    key = analysis_cache_key(
        source_sha256,
        response_model=response_model,
        analysis_schema_version=analysis_schema_version,
    )
    path = (
        store._stage_path(session_id, "slots_copy")
        / "agent-analysis-cache"
        / f"{key[:32]}.json"
    )
    if not path.is_file():
        return None
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if (
        not isinstance(document, dict)
        or document.get("cache_key") != key
        or document.get("provider_id") != PROVIDER_ID
        or document.get("source_sha256") != source_sha256
        or document.get("response_model") != response_model
        or int(document.get("analysis_schema_version", -1))
        != int(analysis_schema_version)
        or not isinstance(document.get("analysis"), dict)
    ):
        return None
    return document
