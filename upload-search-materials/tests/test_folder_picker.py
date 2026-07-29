import json
from pathlib import Path
import subprocess

import pytest

from upload_search_materials.interaction import folder_picker
from upload_search_materials.interaction.folder_picker import (
    FolderPickerError,
    choose_directory,
)


class ResultProcess:
    returncode = 0

    def __init__(self, command, result):
        self.command = command
        self.result = result

    def wait(self, timeout):
        result_path = Path(
            self.command[self.command.index("-ResultPath") + 1]
        )
        result_path.write_text(
            json.dumps(self.result, ensure_ascii=False),
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

    process = TimedOut()
    with pytest.raises(
        FolderPickerError, match="FOLDER_PICKER_TIMEOUT"
    ):
        choose_directory(
            platform="nt",
            powershell="powershell.exe",
            popen=lambda *_args, **_kwargs: process,
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
