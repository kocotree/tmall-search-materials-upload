import pytest
from playwright.sync_api import Error as PlaywrightError

from upload_search_materials.browser.qianniu_upload import (
    QianniuUploadError,
    _wait_for_upload_completion,
)


class _RaisingLocator:
    def __init__(self, error):
        self.error = error

    def wait_for(self, **_kwargs):
        raise self.error


class _SelectorFrame:
    def __init__(self, error):
        self.error = error

    def get_by_text(self, _text, *, exact=False):
        assert exact is False
        return _RaisingLocator(self.error)


def test_upload_completion_accepts_selector_frame_replacement():
    _wait_for_upload_completion(
        object(),
        _SelectorFrame(PlaywrightError("Frame was detached")),
        1,
    )


def test_upload_completion_wraps_unexpected_playwright_error():
    with pytest.raises(
        QianniuUploadError,
        match="QIANNIU_LOCAL_UPLOAD_RUNTIME_FAILED",
    ):
        _wait_for_upload_completion(
            object(),
            _SelectorFrame(PlaywrightError("Browser process exited")),
            1,
        )
