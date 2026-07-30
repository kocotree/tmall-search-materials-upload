import json
from pathlib import Path
import subprocess
import threading
import time

import pytest

from upload_search_materials.interaction import folder_picker
from upload_search_materials.interaction.folder_picker import (
    FolderPickerError,
    choose_directory,
)


class ResultProcess:
    returncode = 0
    pid = 4242

    def __init__(self, command, result):
        self.command = command
        self.result = result
        _write_visible(command, self.pid)

    def poll(self):
        return None

    def wait(self, timeout):
        request_path = Path(
            self.command[self.command.index("-RequestPath") + 1]
        )
        request = json.loads(request_path.read_text(encoding="utf-8"))
        result_path = Path(
            self.command[self.command.index("-ResultPath") + 1]
        )
        result_path.write_text(
            json.dumps(
                {
                    **self.result,
                    "request_id": request["request_id"],
                    "ownership_token": request["ownership_token"],
                    "helper_pid": self.pid,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )


def _write_visible(command, pid):
    request_path = Path(command[command.index("-RequestPath") + 1])
    request = json.loads(request_path.read_text(encoding="utf-8"))
    state_path = Path(command[command.index("-StatePath") + 1])
    state_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "window_visible",
                "request_id": request["request_id"],
                "ownership_token": request["ownership_token"],
                "helper_pid": pid,
            }
        ),
        encoding="utf-8",
    )


def picker_popen(result):
    return lambda command, **_kwargs: ResultProcess(command, result)


def test_native_picker_returns_selected_absolute_directory(tmp_path):
    selected = choose_directory(
        str(tmp_path),
        platform="nt",
        powershell="powershell.exe",
        popen=picker_popen(
            {"schema_version": 1, "status": "selected", "path": str(tmp_path)}
        ),
    )

    assert selected == str(tmp_path)


def test_native_picker_cancel_preserves_frontend_value():
    selected = choose_directory(
        None,
        platform="nt",
        powershell="powershell.exe",
        popen=picker_popen(
            {"schema_version": 1, "status": "cancelled", "path": ""}
        ),
    )

    assert selected is None


def test_native_picker_rejects_invalid_result():
    with pytest.raises(
        FolderPickerError, match="FOLDER_PICKER_INVALID_RESULT"
    ):
        choose_directory(
            None,
            platform="nt",
            powershell="powershell.exe",
            popen=picker_popen(
                {
                    "schema_version": 1,
                    "status": "selected",
                    "path": "relative/path",
                }
            ),
        )


def test_native_picker_reports_unsupported_platform():
    with pytest.raises(
        FolderPickerError, match="FOLDER_PICKER_UNSUPPORTED"
    ):
        choose_directory(platform="posix")


def test_native_picker_reports_busy_without_starting_second_helper():
    assert folder_picker._PICKER_LOCK.acquire(blocking=False)
    try:
        with pytest.raises(
            FolderPickerError, match="FOLDER_PICKER_BUSY"
        ):
            choose_directory(platform="nt", powershell="powershell.exe")
    finally:
        folder_picker._PICKER_LOCK.release()


def test_native_picker_timeout_kills_owned_helper():
    class TimedOut:
        returncode = None
        pid = 4243

        def __init__(self):
            self.killed = False
            self.waits = 0

        def wait(self, timeout):
            self.waits += 1
            if self.waits == 1:
                raise subprocess.TimeoutExpired("picker", timeout)
            self.returncode = -9

        def kill(self):
            self.killed = True

        def poll(self):
            return None

    process = TimedOut()
    def timed_out_popen(command, **_kwargs):
        _write_visible(command, process.pid)
        return process

    with pytest.raises(
        FolderPickerError, match="FOLDER_PICKER_TIMEOUT"
    ):
        choose_directory(
            platform="nt",
            powershell="powershell.exe",
            popen=timed_out_popen,
            timeout_seconds=1,
        )
    assert process.killed is True


def test_native_picker_maps_helper_gui_error():
    with pytest.raises(
        FolderPickerError, match="FOLDER_PICKER_GUI_UNAVAILABLE"
    ):
        choose_directory(
            platform="nt",
            powershell="powershell.exe",
            popen=picker_popen(
                {
                    "schema_version": 1,
                    "status": "error",
                    "reason_code": "FOLDER_PICKER_GUI_UNAVAILABLE",
                }
            ),
        )


def test_native_picker_preserves_helper_error_before_window_is_visible():
    class EarlyError:
        returncode = 1
        pid = 4247

        def __init__(self, command):
            request_path = Path(
                command[command.index("-RequestPath") + 1]
            )
            request = json.loads(request_path.read_text(encoding="utf-8"))
            result_path = Path(
                command[command.index("-ResultPath") + 1]
            )
            result_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "status": "error",
                        "reason_code": "FOLDER_PICKER_GUI_UNAVAILABLE",
                        "detail": "desktop unavailable",
                        "request_id": request["request_id"],
                        "ownership_token": request["ownership_token"],
                        "helper_pid": self.pid,
                    }
                ),
                encoding="utf-8",
            )

        def poll(self):
            return self.returncode

    with pytest.raises(
        FolderPickerError, match="FOLDER_PICKER_GUI_UNAVAILABLE"
    ) as captured:
        choose_directory(
            platform="nt",
            powershell="powershell.exe",
            popen=lambda command, **_kwargs: EarlyError(command),
        )
    assert captured.value.detail == "desktop unavailable"


def test_native_picker_maps_helper_start_failure():
    def fail_to_start(*_args, **_kwargs):
        raise OSError("not available")

    with pytest.raises(
        FolderPickerError, match="FOLDER_PICKER_START_FAILED"
    ):
        choose_directory(
            platform="nt",
            powershell="powershell.exe",
            popen=fail_to_start,
        )


def test_native_picker_rejects_unknown_protocol_version():
    with pytest.raises(
        FolderPickerError, match="FOLDER_PICKER_PROTOCOL_ERROR"
    ):
        choose_directory(
            platform="nt",
            powershell="powershell.exe",
            popen=picker_popen(
                {
                    "schema_version": 2,
                    "status": "cancelled",
                    "path": "",
                }
            ),
        )


def test_native_picker_reports_hidden_window_and_kills_only_helper():
    class Hidden:
        pid = 4244
        returncode = None
        killed = False

        def poll(self):
            return None

        def kill(self):
            self.killed = True

        def wait(self, timeout):
            self.returncode = -9

    process = Hidden()
    with pytest.raises(
        FolderPickerError, match="FOLDER_PICKER_NOT_VISIBLE"
    ):
        choose_directory(
            platform="nt",
            powershell="powershell.exe",
            popen=lambda *_args, **_kwargs: process,
            visibility_timeout_seconds=0.01,
        )
    assert process.killed is True


def test_native_picker_accepts_delayed_visibility(tmp_path):
    class Delayed(ResultProcess):
        pid = 4245

        def __init__(self, command, result):
            self.command = command
            self.result = result
            threading.Thread(
                target=self._publish_visibility,
                daemon=True,
            ).start()

        def _publish_visibility(self):
            time.sleep(0.05)
            _write_visible(self.command, self.pid)

    selected = choose_directory(
        platform="nt",
        powershell="powershell.exe",
        popen=lambda command, **_kwargs: Delayed(
            command,
            {
                "schema_version": 1,
                "status": "selected",
                "path": str(tmp_path),
            },
        ),
        visibility_timeout_seconds=0.5,
    )

    assert selected == str(tmp_path)


def test_native_picker_rejects_stale_helper_identity():
    class Stale(ResultProcess):
        pid = 4246

        def __init__(self, command):
            self.command = command
            self.result = {}
            _write_visible(command, 9999)

        def kill(self):
            self.returncode = -9

    with pytest.raises(
        FolderPickerError, match="FOLDER_PICKER_PROTOCOL_ERROR"
    ):
        choose_directory(
            platform="nt",
            powershell="powershell.exe",
            popen=lambda command, **_kwargs: Stale(command),
        )
