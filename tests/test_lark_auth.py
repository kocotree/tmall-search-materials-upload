import threading
import time

from upload_search_materials.lark_auth import (
    LarkAuthCoordinator,
    REQUIRED_LARK_USER_SCOPES,
)
from upload_search_materials.lark_base_sync import LarkCliResult


def _authorized_payload(*, scopes=()):
    return {
        "appId": "cli_test",
        "identities": {
            "user": {
                "status": "available",
                "available": True,
                "userName": "测试用户",
                "scope": list(scopes),
            }
        }
    }


def test_auth_status_reports_only_safe_user_state():
    coordinator = LarkAuthCoordinator(
        runner=lambda _args, _timeout: LarkCliResult(
            ok=True,
            payload=_authorized_payload(scopes=REQUIRED_LARK_USER_SCOPES),
        )
    )

    status = coordinator.status()

    assert status == {
        "status": "authorized",
        "message": "当前飞书账号已授权。",
        "verification_url": "",
        "user_name": "测试用户",
    }


def test_auth_start_reuses_login_when_required_scopes_are_present():
    calls = []

    def runner(args, _timeout):
        calls.append(list(args))
        return LarkCliResult(
            ok=True,
            payload=_authorized_payload(scopes=REQUIRED_LARK_USER_SCOPES),
        )

    status = LarkAuthCoordinator(runner=runner).start()

    assert status["status"] == "authorized"
    assert len(calls) == 1
    assert calls[0][:2] == ["auth", "status"]


def test_auth_start_requests_missing_wiki_scope_for_existing_base_login():
    calls = []
    base_only_scopes = tuple(
        scope for scope in REQUIRED_LARK_USER_SCOPES if scope != "wiki:node:read"
    )

    def runner(args, _timeout):
        calls.append(list(args))
        if args[:2] == ["auth", "status"]:
            return LarkCliResult(
                ok=True,
                payload=_authorized_payload(scopes=base_only_scopes),
            )
        if "--no-wait" in args:
            return LarkCliResult(
                ok=True,
                payload={
                    "device_code": "wiki-scope-code",
                    "verification_url": "https://accounts.feishu.cn/wiki-scope",
                },
            )
        return LarkCliResult(
            ok=True,
            payload=_authorized_payload(scopes=REQUIRED_LARK_USER_SCOPES),
        )

    started = LarkAuthCoordinator(runner=runner).start()

    assert started["status"] == "awaiting_user"
    login_call = next(args for args in calls if "--no-wait" in args)
    requested_scopes = login_call[login_call.index("--scope") + 1].split()
    assert "wiki:node:read" in requested_scopes


def test_auth_start_returns_url_without_exposing_device_code_and_completes():
    calls = []

    def runner(args, _timeout):
        calls.append(list(args))
        if args[:2] == ["auth", "status"]:
            return LarkCliResult(
                ok=True,
                payload={
                    "appId": "cli_test",
                    "identities": {
                        "user": {"status": "missing", "available": False}
                    }
                },
            )
        if "--no-wait" in args:
            return LarkCliResult(
                ok=True,
                payload={
                    "device_code": "private-device-code",
                    "verification_url": "https://accounts.feishu.cn/device",
                },
            )
        assert args[-2:] == ["private-device-code", "--json"]
        return LarkCliResult(ok=True, payload=_authorized_payload())

    coordinator = LarkAuthCoordinator(runner=runner)
    started = coordinator.start()

    assert started["status"] == "awaiting_user"
    assert started["verification_url"] == "https://accounts.feishu.cn/device"
    assert "device_code" not in started
    assert "private-device-code" not in str(started)
    for _ in range(100):
        completed = coordinator.status(refresh=False)
        if completed["status"] == "authorized":
            break
        time.sleep(0.01)
    assert completed["status"] == "authorized"
    assert any("--device-code" in args for args in calls)


def test_repeated_start_does_not_create_a_second_pending_authorization():
    release = threading.Event()
    no_wait_calls = 0

    def runner(args, _timeout):
        nonlocal no_wait_calls
        if args[:2] == ["auth", "status"]:
            return LarkCliResult(
                ok=True,
                payload={
                    "appId": "cli_test",
                    "identities": {
                        "user": {"status": "missing", "available": False}
                    }
                },
            )
        if "--no-wait" in args:
            no_wait_calls += 1
            return LarkCliResult(
                ok=True,
                payload={
                    "device_code": "pending-code",
                    "verification_url": "https://accounts.feishu.cn/pending",
                },
            )
        release.wait(2)
        return LarkCliResult(ok=True, payload=_authorized_payload())

    coordinator = LarkAuthCoordinator(runner=runner)
    first = coordinator.start()
    second = coordinator.start()

    assert first == second
    assert no_wait_calls == 1
    release.set()


def test_auth_start_initializes_the_team_app_before_login():
    calls = []
    status_calls = 0

    def runner(args, _timeout):
        nonlocal status_calls
        calls.append(list(args))
        if args[:2] == ["auth", "status"]:
            status_calls += 1
            if status_calls == 1:
                return LarkCliResult(
                    ok=False,
                    reason_code="LARK_COMMAND_FAILED",
                    message="run config init: missing app id",
                )
            return LarkCliResult(
                ok=True,
                payload={
                    "appId": "cli_team",
                    "identities": {
                        "user": {"status": "missing", "available": False}
                    },
                },
            )
        if args[:2] == ["config", "init"]:
            return LarkCliResult(ok=True, payload={"appId": "cli_team"})
        if "--no-wait" in args:
            return LarkCliResult(
                ok=True,
                payload={
                    "device_code": "configured-code",
                    "verification_url": "https://accounts.feishu.cn/configured",
                },
            )
        return LarkCliResult(ok=True, payload=_authorized_payload())

    started = LarkAuthCoordinator(
        runner=runner,
        app_id="cli_team",
    ).start()

    assert started["status"] == "awaiting_user"
    assert ["config", "init", "--app-id", "cli_team", "--brand", "feishu"] in calls


def test_missing_cli_is_reported_without_raw_command_details():
    coordinator = LarkAuthCoordinator(
        runner=lambda _args, _timeout: LarkCliResult(
            ok=False,
            reason_code="LARK_CLI_NOT_FOUND",
            message="raw implementation detail",
        )
    )

    status = coordinator.start()

    assert status["status"] == "failed"
    assert status["message"] == "当前工作台缺少飞书授权组件，请联系维护者完成环境准备。"
    assert "raw implementation detail" not in str(status)
