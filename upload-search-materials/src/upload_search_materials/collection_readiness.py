"""Stage-one readiness checks and machine-local selector bootstrap."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

import yaml

from .browser.config import (
    SelectorConfigError,
    load_selector_profile,
    required_selectors,
)
from .browser.material_page import (
    PAGINATION_SELECTOR_FIELDS,
    PaginationStateError,
    read_pagination_state,
)
from .browser.session import (
    HumanCheckRequired,
    LoginInteractionRequired,
    StoreIdentityError,
    inspect_cdp_endpoint,
    validate_collection_page,
)
from .collection_runtime import COLLECTION_RUNTIME_SCHEMA_VERSION
from .runtime_config import RuntimeConfig
from .time_utils import iso_timestamp


READINESS_REASON_MESSAGES = {
    "READY": "已就绪",
    "ENVIRONMENT_NOT_PREPARED": "项目虚拟环境尚未准备",
    "SELECTOR_PROFILE_NOT_FOUND": "尚未配置本机生产选择器",
    "SELECTOR_PROFILE_INVALID": "本机选择器配置无效",
    "SELECTOR_DOM_NOT_VALIDATED": "选择器尚未通过当前页面验证",
    "PAGINATION_ORIGIN_UNVERIFIED": "无法确认搜推高价值列表的当前页码",
    "CDP_UNAVAILABLE": "用于登录和采集的 CDP Chrome 尚未连接",
    "LOGIN_INTERACTION_REQUIRED": "需要用户在 CDP Chrome 完成登录",
    "HUMAN_CHECK": "需要用户在 CDP Chrome 完成人机验证",
    "MATERIAL_PAGE_REQUIRED": "CDP Chrome 尚未打开官方素材中心",
    "STORE_IDENTITY_REQUIRED": "尚未确认当前登录店铺",
    "STORE_IDENTITY_MISMATCH": "当前登录店铺与目标店铺不一致",
}

DEFAULT_CANDIDATE_SELECTORS = {
    "store_name": (
        '[data-store-name], [class*="shopName"], [class*="ShopName"]'
    ),
    "human_check": (
        'iframe[src*="captcha"], [class*="captcha"], '
        '[class*="verify"], :text-is("验证码"), '
        ':text-is("扫码验证"), :text-is("安全验证")'
    ),
    "promotion_tab": (
        'li[role="tab"]:has-text("搜推素材"), '
        '[role="tab"]:has-text("搜推素材")'
    ),
    "high_value_filter": (
        '[role="checkbox"]:has-text("搜推高价值"), '
        'label:has-text("搜推高价值")'
    ),
    "promotion_rows": "tbody tr",
    "promotion_current_page": (
        'nav[aria-label*="分页"] [aria-current="page"], '
        '.next-pagination-list > .next-pagination-item.next-current'
    ),
    "promotion_first_page": (
        'nav[aria-label*="分页"] [data-page="1"], '
        '.next-pagination-list > .next-pagination-item:first-of-type'
    ),
    "promotion_terminal_page": (
        'nav[aria-label*="分页"] [data-page]:last-of-type, '
        '.next-pagination-list > .next-pagination-item:last-of-type'
    ),
    "promotion_next_page": (
        'button:has-text("下一页"), [aria-label="下一页"]'
    ),
    "safe_popup_progress": (
        '#react-joyride-portal :text-is("下一步"), '
        '#react-joyride-portal :text-is("完成"), '
        '#react-joyride-portal :text-is("知道了")'
    ),
    "safe_popup_close_priority": (
        '#react-joyride-portal [data-test-id="button-close"], '
        '#react-joyride-portal [data-test-id="button-skip"], '
        '.next-overlay-wrapper.opened .next-dialog-close'
    ),
    "safe_popup_close": (
        'button:has-text("稍后再看"), button:has-text("关闭"), '
        'button:has-text("知道了"), [aria-label*="关闭"]'
    ),
}


def _check(
    check_id: str,
    *,
    ready: bool,
    reason_code: str,
    category: str,
    next_action: str = "",
    evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": check_id,
        "ready": bool(ready),
        "reason_code": reason_code,
        "message": READINESS_REASON_MESSAGES.get(reason_code, reason_code),
        "category": category,
        "next_action": next_action,
        "checked_at": iso_timestamp(),
        "evidence": dict(evidence or {}),
    }


def project_environment_status(runtime: RuntimeConfig) -> dict[str, Any]:
    project = runtime.workspace_root / "upload-search-materials"
    executable = project / ".venv" / "Scripts" / "tmall-materials.exe"
    python = project / ".venv" / "Scripts" / "python.exe"
    lock = project / "uv.lock"
    source_package = project / "src" / "upload_search_materials"
    ready = (
        python.is_file()
        and lock.is_file()
        and (executable.is_file() or source_package.is_dir())
    )
    return _check(
        "environment",
        ready=ready,
        reason_code="READY" if ready else "ENVIRONMENT_NOT_PREPARED",
        category="environment",
        next_action=(
            ""
            if ready
            else "运行 scripts/bootstrap.cmd 单独准备依赖环境"
        ),
        evidence={
            "project_root": str(project),
            "executable": str(executable),
            "python": str(python),
            "source_package": str(source_package),
            "lock": str(lock),
            "uv_cache": str(project / ".uv-cache"),
        },
    )


def selector_schema_status(runtime: RuntimeConfig) -> dict[str, Any]:
    selected = runtime.selectors_file
    if selected is None:
        return _check(
            "selector_schema",
            ready=False,
            reason_code="SELECTOR_PROFILE_NOT_FOUND",
            category="blocking_configuration",
            next_action="在配置页创建并验证本机选择器候选",
        )
    try:
        profile = load_selector_profile(
            selected,
            purpose="high_value_collection",
            production=True,
        )
    except SelectorConfigError as error:
        return _check(
            "selector_schema",
            ready=False,
            reason_code="SELECTOR_PROFILE_INVALID",
            category="blocking_configuration",
            next_action="修复本机候选并重新执行当前页面验证",
            evidence={"path": str(selected), "detail": str(error)},
        )
    return _check(
        "selector_schema",
        ready=True,
        reason_code="READY",
        category="blocking_configuration",
        evidence={
            "path": str(profile.path),
            "profile_name": profile.name,
            "profile_version": profile.version,
            "profile_sha256": profile.sha256,
            "purpose": profile.purpose,
        },
    )


def build_collection_readiness(
    runtime: RuntimeConfig,
    *,
    dom_evidence: Mapping[str, Any] | None = None,
    expected_store: str = "",
) -> dict[str, Any]:
    """Build independent checks; no browser mutation or login automation occurs."""

    environment = project_environment_status(runtime)
    selector = selector_schema_status(runtime)
    cdp = inspect_cdp_endpoint(runtime.cdp_url, timeout_seconds=0.25)
    cdp_check = _check(
        "cdp_connection",
        ready=cdp.connected,
        reason_code="READY" if cdp.connected else "CDP_UNAVAILABLE",
        category="cdp_chrome",
        next_action=cdp.next_action,
        evidence={"endpoint": cdp.endpoint, "pages": list(cdp.pages)},
    )
    evidence = dict(dom_evidence or {})
    dom_ready = bool(
        evidence.get("store_match")
        and evidence.get("page_identity") == "material_center"
        and evidence.get("pagination_state", {}).get("verified") is True
        and evidence.get("selector_profile", {}).get("sha256")
        == selector.get("evidence", {}).get("profile_sha256")
    )
    dom_check = _check(
        "selector_current_dom",
        ready=dom_ready,
        reason_code="READY" if dom_ready else "SELECTOR_DOM_NOT_VALIDATED",
        category="blocking_configuration",
        next_action=(
            ""
            if dom_ready
            else _dom_validation_next_action(evidence)
        ),
        evidence=evidence,
    )
    observed_store = str(evidence.get("observed_store", "")).strip()
    target_store = str(expected_store).strip()
    if not observed_store:
        store_reason = "STORE_IDENTITY_REQUIRED"
        store_ready = False
    elif target_store and observed_store != target_store:
        store_reason = "STORE_IDENTITY_MISMATCH"
        store_ready = False
    else:
        store_reason = "READY"
        store_ready = bool(target_store)
    store_check = _check(
        "store_identity",
        ready=store_ready,
        reason_code=store_reason,
        category="user_action",
        next_action=(
            ""
            if store_ready
            else "在 CDP Chrome 核对店铺，并在配置页确认目标店铺"
        ),
        evidence={
            "target_store": target_store,
            "observed_store": observed_store,
        },
    )
    login_reason = str(evidence.get("reason_code", ""))
    if login_reason not in {
        "LOGIN_INTERACTION_REQUIRED",
        "HUMAN_CHECK",
        "MATERIAL_PAGE_REQUIRED",
    }:
        login_reason = (
            "READY"
            if (
                evidence.get("page_identity") == "material_center"
                and observed_store
            )
            else "LOGIN_INTERACTION_REQUIRED"
        )
    login_check = _check(
        "login_and_human_check",
        ready=login_reason == "READY",
        reason_code=login_reason,
        category="user_action",
        next_action=(
            ""
            if login_reason == "READY"
            else "在 CDP Chrome 完成原生登录或验证后重新检测"
        ),
    )
    checks = [
        environment,
        selector,
        cdp_check,
        dom_check,
        login_check,
        store_check,
    ]
    return {
        "schema_version": COLLECTION_RUNTIME_SCHEMA_VERSION,
        "status": "ready" if all(item["ready"] for item in checks) else "blocked",
        "ready": all(item["ready"] for item in checks),
        "checks": checks,
        "checked_at": iso_timestamp(),
    }


def _dom_validation_next_action(evidence: Mapping[str, Any]) -> str:
    fields = evidence.get("field_results", {})
    high_value = (
        fields.get("high_value_filter", {})
        if isinstance(fields, Mapping)
        else {}
    )
    if (
        isinstance(high_value, Mapping)
        and high_value.get("configured")
        and int(high_value.get("count") or 0) == 0
    ):
        return (
            "由处理器自动重新验证；系统会自动切换到"
            "“搜推素材 → 搜推高价值”并复位到第 1 页"
        )
    pagination = evidence.get("pagination_state", {})
    if (
        isinstance(pagination, Mapping)
        and pagination.get("verified") is False
    ):
        detail = str(
            pagination.get("detail")
            or pagination.get("reason_code")
            or ""
        ).strip()
        return (
            "由处理器自动重新确认第 1 页"
            + (f"（{detail}）" if detail else "")
        )
    return "由处理器自动重新检查当前素材中心页面"


def create_selector_candidate(
    target: Path,
    *,
    material_center_url: str,
) -> dict[str, Any]:
    target = Path(target).expanduser().resolve()
    document: dict[str, Any] = {
        "schema_version": 1,
        "profile_name": "machine-local-high-value-candidate",
        "profile_version": "candidate-1",
        "production": False,
        "supported_purposes": ["high_value_collection"],
        "material_center_url": material_center_url,
        **DEFAULT_CANDIDATE_SELECTORS,
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    temporary.write_text(
        yaml.safe_dump(document, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    temporary.replace(target)
    return {
        "schema_version": COLLECTION_RUNTIME_SCHEMA_VERSION,
        "status": "candidate",
        "production": False,
        "path": str(target),
        "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        "next_action": "由处理器在已登录的素材中心自动验证当前页面",
        "created_at": iso_timestamp(),
    }


def validate_selector_candidate(
    path: Path,
    page: Any,
    *,
    expected_store: str,
) -> dict[str, Any]:
    """Validate a candidate against current DOM without promoting it."""

    profile = load_selector_profile(
        path,
        purpose="high_value_collection",
        production=False,
    )
    field_results: dict[str, dict[str, Any]] = {}
    for field in sorted(required_selectors("high_value_collection")):
        selector = str(profile.selectors.get(field, "")).strip()
        if field == "human_check":
            try:
                count = int(page.locator(selector).count())
            except Exception as error:
                field_results[field] = {
                    "ready": False,
                    "count": None,
                    "detail": type(error).__name__,
                }
            else:
                field_results[field] = {
                    "ready": bool(selector),
                    "count": count,
                    "meaning": (
                        "visible_human_check"
                        if count
                        else "selector_valid_no_visible_challenge"
                    ),
                }
            continue
        try:
            count = int(page.locator(selector).count())
        except Exception as error:
            field_results[field] = {
                "ready": False,
                "count": None,
                "detail": type(error).__name__,
            }
        else:
            exact_one = field in {
                *PAGINATION_SELECTOR_FIELDS,
                "promotion_next_page",
            }
            field_results[field] = {
                "ready": count == 1 if exact_one else count > 0,
                "count": count,
                "expected_count": 1 if exact_one else "at_least_one",
            }
    failed = [
        field for field, result in field_results.items() if not result["ready"]
    ]
    pagination_evidence: dict[str, Any]
    try:
        pagination_state = read_pagination_state(page, profile.selectors)
    except PaginationStateError as error:
        pagination_evidence = {
            "verified": False,
            "reason_code": str(error).split(":", 1)[0],
            "detail": str(error),
        }
        failed.append("pagination_origin")
    else:
        pagination_evidence = {
            "verified": True,
            **pagination_state.as_dict(),
        }
    try:
        page_evidence = validate_collection_page(
            page,
            profile.selectors,
            expected_store=expected_store,
            profile_name=profile.name,
            profile_version=profile.version,
            profile_sha256=profile.sha256,
        )
    except HumanCheckRequired as error:
        reason_code = "HUMAN_CHECK"
        page_evidence = {"reason_code": reason_code, "detail": str(error)}
    except LoginInteractionRequired as error:
        reason_code = str(error).split(":", 1)[0]
        page_evidence = {"reason_code": reason_code, "detail": str(error)}
    except StoreIdentityError as error:
        reason_code = "STORE_IDENTITY_MISMATCH"
        page_evidence = {"reason_code": reason_code, "detail": str(error)}
    else:
        reason_code = "READY"
    page_evidence["pagination_state"] = pagination_evidence
    ready = not failed and reason_code == "READY"
    return {
        "schema_version": COLLECTION_RUNTIME_SCHEMA_VERSION,
        "status": "ready" if ready else "invalid",
        "ready": ready,
        "reason_code": (
            "READY"
            if ready
            else (
                (
                    str(
                        pagination_evidence.get(
                            "reason_code",
                            "PAGINATION_ORIGIN_UNVERIFIED",
                        )
                    )
                    if failed[0] == "pagination_origin"
                    else f"SELECTOR_FIELD_INVALID:{failed[0]}"
                )
                if failed
                else reason_code
            )
        ),
        "profile_name": profile.name,
        "profile_version": profile.version,
        "candidate_sha256": profile.sha256,
        "field_results": field_results,
        "page_evidence": page_evidence,
        "repair_action": (
            ""
            if ready
            else (
                "保存本次失败字段和页面证据；仅在可复现后使用 "
                "Playwright 修复现有选择器/采集器，再回到同一入口重试"
            )
        ),
        "validated_at": iso_timestamp(),
    }


def promote_selector_candidate(
    path: Path,
    validation: Mapping[str, Any],
) -> dict[str, Any]:
    if validation.get("ready") is not True:
        raise SelectorConfigError("SELECTOR_CANDIDATE_NOT_VALIDATED")
    target = Path(path).expanduser().resolve()
    raw = yaml.safe_load(target.read_text(encoding="utf-8-sig")) or {}
    if not isinstance(raw, dict) or raw.get("production") is not False:
        raise SelectorConfigError("SELECTOR_CANDIDATE_STATE_INVALID")
    if hashlib.sha256(target.read_bytes()).hexdigest() != validation.get(
        "candidate_sha256"
    ):
        raise SelectorConfigError("SELECTOR_CANDIDATE_CHANGED")
    raw["production"] = True
    raw["profile_version"] = str(validation["validated_at"])
    raw["current_dom_validation"] = {
        "validated_at": validation["validated_at"],
        "field_results": validation["field_results"],
        "page_identity": validation["page_evidence"].get("page_identity"),
        "observed_store": validation["page_evidence"].get("observed_store"),
        "page_url": validation["page_evidence"].get("page_url"),
        "pagination_state": validation["page_evidence"].get(
            "pagination_state"
        ),
        "candidate_sha256": validation["candidate_sha256"],
    }
    temporary = target.with_name(f".{target.name}.tmp")
    temporary.write_text(
        yaml.safe_dump(raw, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    temporary.replace(target)
    promoted = load_selector_profile(
        target,
        purpose="high_value_collection",
        production=True,
    )
    return {
        "schema_version": COLLECTION_RUNTIME_SCHEMA_VERSION,
        "status": "production",
        "production": True,
        "path": str(promoted.path),
        "profile_name": promoted.name,
        "profile_version": promoted.version,
        "profile_sha256": promoted.sha256,
        "purpose": promoted.purpose,
        "validated_at": validation["validated_at"],
    }
