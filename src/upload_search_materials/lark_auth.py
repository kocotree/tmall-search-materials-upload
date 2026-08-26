"""Workbench-owned Feishu user authorization for the Windows workflow.

The device code and verification URL are intentionally kept in process memory
only.  The public status never exposes a device code, token, app secret, or raw
CLI response.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
import threading
from typing import Any, Mapping, Sequence

from .lark_base_sync import (
    LarkCliResult,
    Runner,
    default_lark_cli_runner,
)


REQUIRED_LARK_USER_SCOPES = (
    "base:block:read",
    "base:field:read",
    "base:record:read",
    "base:record:create",
)
DEFAULT_LARK_APP_ID = "cli_aa07506f94785beb"
AUTH_STATUS_TIMEOUT_SECONDS = 20.0
AUTH_COMPLETION_TIMEOUT_SECONDS = 600.0


@dataclass(frozen=True)
class LarkAuthStatus:
    status: str
    message: str
    verification_url: str = ""
    user_name: str = ""

    def public_document(self) -> dict[str, object]:
        return {
            "status": self.status,
            "message": self.message,
            "verification_url": self.verification_url,
            "user_name": self.user_name,
        }


class LarkAuthCoordinator:
    """Coordinate one split-flow Feishu login for a managed workbench."""

    def __init__(
        self,
        *,
        runner: Runner | None = None,
        required_scopes: Sequence[str] = REQUIRED_LARK_USER_SCOPES,
        app_id: str = DEFAULT_LARK_APP_ID,
        status_timeout_seconds: float = AUTH_STATUS_TIMEOUT_SECONDS,
        completion_timeout_seconds: float = AUTH_COMPLETION_TIMEOUT_SECONDS,
    ) -> None:
        self._runner = runner or default_lark_cli_runner
        self._required_scopes = tuple(
            scope.strip() for scope in required_scopes if scope.strip()
        )
        self._app_id = app_id.strip()
        self._status_timeout_seconds = status_timeout_seconds
        self._completion_timeout_seconds = completion_timeout_seconds
        self._lock = threading.Lock()
        self._status = LarkAuthStatus(
            status="idle",
            message="请在工作台完成飞书授权。",
        )
        self._attempt = 0
        self._device_code = ""

    def status(self, *, refresh: bool = True) -> dict[str, object]:
        """Return a safe public status, optionally checking the local login."""

        with self._lock:
            current = self._status
            if current.status in {"awaiting_user", "failed"} or not refresh:
                return current.public_document()

        result = self._runner(
            ["auth", "status", "--json", "--verify"],
            self._status_timeout_seconds,
        )
        next_status = self._status_from_result(result)
        with self._lock:
            if self._status.status != "awaiting_user":
                self._status = next_status
            return self._status.public_document()

    def start(self) -> dict[str, object]:
        """Start or reuse one in-memory device authorization attempt."""

        with self._lock:
            if self._status.status == "awaiting_user":
                return self._status.public_document()

        status_result = self._runner(
            ["auth", "status", "--json", "--verify"],
            self._status_timeout_seconds,
        )
        if _app_configuration_missing(status_result):
            configured = self._runner(
                [
                    "config",
                    "init",
                    "--app-id",
                    self._app_id,
                    "--brand",
                    "feishu",
                ],
                self._status_timeout_seconds,
            )
            if not configured.ok:
                failed = LarkAuthStatus(
                    status="failed",
                    message="飞书授权环境准备未完成，请联系维护者后重试。",
                )
                with self._lock:
                    self._status = failed
                return failed.public_document()
            status_result = self._runner(
                ["auth", "status", "--json", "--verify"],
                self._status_timeout_seconds,
            )
        if (
            status_result.ok
            and _user_is_authorized(status_result.payload)
            and set(self._required_scopes).issubset(
                _granted_scopes(status_result.payload)
            )
        ):
            authorized = LarkAuthStatus(
                status="authorized",
                message="飞书授权有效，正在验证默认数据表。",
                user_name=_user_name(status_result.payload),
            )
            with self._lock:
                self._status = authorized
            return authorized.public_document()

        start_result = self._runner(
            [
                "auth",
                "login",
                "--scope",
                " ".join(self._required_scopes),
                "--no-wait",
                "--json",
            ],
            self._status_timeout_seconds,
        )
        if not start_result.ok:
            failed = _friendly_failure(start_result, completion=False)
            with self._lock:
                self._status = failed
            return failed.public_document()

        device_code = _find_text(
            start_result.payload,
            ("device_code", "deviceCode"),
        )
        verification_url = _find_text(
            start_result.payload,
            (
                "verification_url",
                "verificationUrl",
                "verification_uri_complete",
                "verificationUriComplete",
                "verification_uri",
                "verificationUri",
            ),
        )
        if not device_code or not verification_url:
            failed = LarkAuthStatus(
                status="failed",
                message="飞书授权页面暂时无法打开，请稍后重试。",
            )
            with self._lock:
                self._status = failed
            return failed.public_document()

        with self._lock:
            self._attempt += 1
            attempt = self._attempt
            self._device_code = device_code
            self._status = LarkAuthStatus(
                status="awaiting_user",
                message="请在新打开的飞书页面完成授权，工作台会自动继续。",
                verification_url=verification_url,
                user_name=_user_name(status_result.payload),
            )
            public_status = self._status.public_document()

        try:
            threading.Thread(
                target=self._complete,
                args=(attempt, device_code),
                name="tmall-lark-auth",
                daemon=True,
            ).start()
        except RuntimeError:
            failed = LarkAuthStatus(
                status="failed",
                message="飞书授权流程未能启动，请重新点击授权。",
            )
            with self._lock:
                if self._attempt == attempt:
                    self._device_code = ""
                    self._status = failed
            return failed.public_document()
        return public_status

    def _complete(self, attempt: int, device_code: str) -> None:
        result = self._runner(
            [
                "auth",
                "login",
                "--device-code",
                device_code,
                "--json",
            ],
            self._completion_timeout_seconds,
        )
        if result.ok:
            completed = LarkAuthStatus(
                status="authorized",
                message="飞书授权已完成，正在验证默认数据表。",
                user_name=_user_name(result.payload),
            )
        else:
            completed = _friendly_failure(result, completion=True)
        with self._lock:
            if self._attempt != attempt or self._device_code != device_code:
                return
            self._device_code = ""
            self._status = completed

    def _status_from_result(self, result: LarkCliResult) -> LarkAuthStatus:
        if not result.ok:
            return _friendly_failure(result, completion=False)
        if _user_is_authorized(result.payload):
            return LarkAuthStatus(
                status="authorized",
                message="当前飞书账号已授权。",
                user_name=_user_name(result.payload),
            )
        return LarkAuthStatus(
            status="authorization_required",
            message="请授权飞书账号，以同步负责人并记录成功上传。",
            user_name=_user_name(result.payload),
        )


def _user_is_authorized(payload: Any) -> bool:
    user = _user_identity(payload)
    if not user:
        return False
    available = user.get("available")
    if isinstance(available, bool):
        return available
    status = str(user.get("status") or "").strip().casefold()
    token_status = str(
        user.get("tokenStatus") or user.get("token_status") or ""
    ).strip().casefold()
    return status in {"available", "authorized", "ready", "valid", "ok"} or (
        token_status in {"available", "ready", "valid", "ok"}
    )


def _app_configuration_missing(result: LarkCliResult) -> bool:
    if result.ok:
        return not _find_text(result.payload, ("appId", "app_id"))
    text = " ".join(
        part for part in (result.message, result.stderr, result.stdout) if part
    ).casefold()
    return any(
        marker in text
        for marker in (
            "config init",
            "missing app id",
            "app id is required",
            "app configuration",
            "尚未配置应用",
        )
    )


def _user_name(payload: Any) -> str:
    user = _user_identity(payload)
    return str(user.get("userName") or user.get("user_name") or "").strip()


def _user_identity(payload: Any) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping):
        return {}
    identities = payload.get("identities")
    if isinstance(identities, Mapping):
        user = identities.get("user")
        if isinstance(user, Mapping):
            return user
    data = payload.get("data")
    if isinstance(data, Mapping):
        return _user_identity(data)
    return {}


def _granted_scopes(payload: Any) -> set[str]:
    scopes: set[str] = set()

    def collect(value: Any) -> None:
        if isinstance(value, str):
            scopes.update(
                part for part in re.split(r"[\s,]+", value.strip()) if part
            )
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            for item in value:
                collect(item)

    def walk(value: Any) -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                normalized = str(key).replace("_", "").casefold()
                if normalized in {"scope", "scopes", "grantedscope", "grantedscopes"}:
                    collect(item)
                elif normalized not in {"missingscope", "missingscopes"}:
                    walk(item)
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            for item in value:
                walk(item)

    walk(payload)
    return scopes


def _find_text(payload: Any, names: Sequence[str]) -> str:
    wanted = {name.casefold() for name in names}
    if isinstance(payload, Mapping):
        for key, value in payload.items():
            if str(key).casefold() in wanted and isinstance(value, str):
                return value.strip()
        for value in payload.values():
            found = _find_text(value, names)
            if found:
                return found
    elif isinstance(payload, Sequence) and not isinstance(payload, (str, bytes)):
        for value in payload:
            found = _find_text(value, names)
            if found:
                return found
    return ""


def _friendly_failure(
    result: LarkCliResult,
    *,
    completion: bool,
) -> LarkAuthStatus:
    text = " ".join(
        part for part in (result.message, result.stderr, result.stdout) if part
    ).casefold()
    if result.reason_code == "LARK_CLI_NOT_FOUND":
        message = "当前工作台缺少飞书授权组件，请联系维护者完成环境准备。"
    elif any(
        marker in text
        for marker in (
            "config init",
            "app id",
            "appid",
            "not configured",
            "尚未配置",
        )
    ):
        message = "飞书授权组件尚未初始化，请联系维护者完成一次环境准备。"
    elif any(marker in text for marker in ("missing_scope", "console_url", "权限")):
        message = "飞书应用尚未开放所需权限，请联系维护者更新应用权限后重试。"
    elif completion and result.reason_code == "LARK_CLI_TIMEOUT":
        message = "本次飞书授权等待已结束，请重新点击授权。"
    elif completion and any(
        marker in text for marker in ("cancel", "denied", "reject", "取消", "拒绝")
    ):
        message = "本次飞书授权未完成，请重新点击授权。"
    else:
        message = (
            "飞书授权未完成，请检查网络后重试。"
            if completion
            else "暂时无法检查飞书授权，请稍后重试。"
        )
    return LarkAuthStatus(status="failed", message=message)
