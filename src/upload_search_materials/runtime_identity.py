"""Safe process identity facts for local-resource access on Windows."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from typing import Any, Mapping


IDENTITY_SCHEMA_VERSION = 1


class LocalResourceIdentityMismatch(RuntimeError):
    reason_code = "LOCAL_RESOURCE_IDENTITY_MISMATCH"

    def __init__(self, detail: str = "") -> None:
        self.detail = detail
        super().__init__(
            self.reason_code + (f":{detail}" if detail else "")
        )


def _windows_sid() -> str:
    class SidAndAttributes(ctypes.Structure):
        _fields_ = [
            ("sid", ctypes.c_void_p),
            ("attributes", wintypes.DWORD),
        ]

    advapi = ctypes.windll.advapi32
    kernel = ctypes.windll.kernel32
    advapi.OpenProcessToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    advapi.OpenProcessToken.restype = wintypes.BOOL
    advapi.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi.GetTokenInformation.restype = wintypes.BOOL
    advapi.ConvertSidToStringSidW.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.LPWSTR),
    ]
    advapi.ConvertSidToStringSidW.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    token = wintypes.HANDLE()
    if not advapi.OpenProcessToken(
        kernel.GetCurrentProcess(),
        0x0008,
        ctypes.byref(token),
    ):
        raise ctypes.WinError()
    try:
        size = wintypes.DWORD()
        advapi.GetTokenInformation(
            token, 1, None, 0, ctypes.byref(size)
        )
        buffer = ctypes.create_string_buffer(size.value)
        if not advapi.GetTokenInformation(
            token,
            1,
            buffer,
            size,
            ctypes.byref(size),
        ):
            raise ctypes.WinError()
        sid_pointer = ctypes.cast(
            buffer, ctypes.POINTER(SidAndAttributes)
        ).contents.sid
        text = wintypes.LPWSTR()
        if not advapi.ConvertSidToStringSidW(
            sid_pointer, ctypes.byref(text)
        ):
            raise ctypes.WinError()
        try:
            return str(text.value)
        finally:
            kernel.LocalFree(text)
    finally:
        kernel.CloseHandle(token)


def _windows_session_id() -> int:
    value = ctypes.c_ulong()
    if not ctypes.windll.kernel32.ProcessIdToSessionId(
        os.getpid(), ctypes.byref(value)
    ):
        raise ctypes.WinError()
    return int(value.value)


def _windows_interactive_desktop() -> bool:
    handle = ctypes.windll.user32.OpenInputDesktop(
        0, False, 0x0100
    )
    if not handle:
        return False
    ctypes.windll.user32.CloseDesktop(handle)
    return True


def _windows_remote_drives() -> list[str]:
    mask = int(ctypes.windll.kernel32.GetLogicalDrives())
    visible: list[str] = []
    for index in range(26):
        if not mask & (1 << index):
            continue
        letter = f"{chr(65 + index)}:\\"
        if int(ctypes.windll.kernel32.GetDriveTypeW(letter)) == 4:
            visible.append(letter[:2])
    return visible


def current_runtime_identity() -> dict[str, Any]:
    """Return non-sensitive identity facts; never enumerate source contents."""

    if os.name == "nt":
        try:
            sid = _windows_sid()
        except OSError:
            sid = ""
        try:
            session_id = _windows_session_id()
        except OSError:
            session_id = -1
        return {
            "schema_version": IDENTITY_SCHEMA_VERSION,
            "platform": "windows",
            "sid": sid,
            "login_session_id": session_id,
            "interactive_desktop": _windows_interactive_desktop(),
            "remote_drive_letters": _windows_remote_drives(),
            "pid": os.getpid(),
        }
    inherited_login_session = os.environ.get(
        "TMALL_DESKTOP_LOGIN_SESSION_ID", ""
    ).strip()
    try:
        login_session_id = int(inherited_login_session)
    except ValueError:
        login_session_id = os.getsid(0) if hasattr(os, "getsid") else -1
    return {
        "schema_version": IDENTITY_SCHEMA_VERSION,
        "platform": os.name,
        "sid": f"uid:{os.getuid()}" if hasattr(os, "getuid") else "",
        "login_session_id": login_session_id,
        "interactive_desktop": False,
        "remote_drive_letters": [],
        "pid": os.getpid(),
    }


def runtime_identity_for_pid(pid: int) -> dict[str, Any] | None:
    """Read SID/session identity for an owned child without opening files."""

    if os.name != "nt":
        return None
    kernel = ctypes.windll.kernel32
    advapi = ctypes.windll.advapi32
    kernel.OpenProcess.argtypes = [
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.DWORD,
    ]
    kernel.OpenProcess.restype = wintypes.HANDLE
    advapi.OpenProcessToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    advapi.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi.ConvertSidToStringSidW.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(wintypes.LPWSTR),
    ]
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    process = kernel.OpenProcess(0x1000, False, int(pid))
    if not process:
        return None
    token = wintypes.HANDLE()
    try:
        if not advapi.OpenProcessToken(
            process, 0x0008, ctypes.byref(token)
        ):
            return None
        size = wintypes.DWORD()
        advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
        buffer = ctypes.create_string_buffer(size.value)
        if not advapi.GetTokenInformation(
            token, 1, buffer, size, ctypes.byref(size)
        ):
            return None
        sid_pointer = ctypes.cast(
            buffer, ctypes.POINTER(ctypes.c_void_p)
        ).contents.value
        text = wintypes.LPWSTR()
        if not advapi.ConvertSidToStringSidW(
            sid_pointer, ctypes.byref(text)
        ):
            return None
        try:
            sid = str(text.value)
        finally:
            kernel.LocalFree(text)
        session_id = wintypes.DWORD()
        if not kernel.ProcessIdToSessionId(
            int(pid), ctypes.byref(session_id)
        ):
            return None
        return {
            "schema_version": IDENTITY_SCHEMA_VERSION,
            "platform": "windows",
            "sid": sid,
            "login_session_id": int(session_id.value),
            "interactive_desktop": _windows_interactive_desktop(),
            "remote_drive_letters": _windows_remote_drives(),
            "pid": int(pid),
        }
    finally:
        if token:
            kernel.CloseHandle(token)
        kernel.CloseHandle(process)


def same_local_resource_identity(
    expected: Mapping[str, Any], actual: Mapping[str, Any]
) -> bool:
    """Compare the desktop security context, not its mutable mounts.

    ``remote_drive_letters`` is diagnostic-only. Network mounts can change
    while the same user session remains active, and saved image sources may
    not be used by the task currently being configured.
    """

    base_matches = bool(
        expected.get("sid")
        and expected.get("sid") == actual.get("sid")
        and expected.get("login_session_id")
        == actual.get("login_session_id")
    )
    if not base_matches:
        return False
    if "interactive_desktop" in expected and bool(
        expected.get("interactive_desktop")
    ) != bool(actual.get("interactive_desktop")):
        return False
    return True


def require_local_resource_identity(
    expected: Mapping[str, Any],
) -> dict[str, Any]:
    actual = current_runtime_identity()
    if not same_local_resource_identity(expected, actual):
        raise LocalResourceIdentityMismatch("sid_or_session_changed")
    if expected.get("interactive_desktop") and not actual.get(
        "interactive_desktop"
    ):
        raise LocalResourceIdentityMismatch("interactive_desktop_lost")
    return actual
