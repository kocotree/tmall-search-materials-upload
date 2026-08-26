"""Lifecycle control for one managed workbench session."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import threading
import time
from typing import Any, Callable


DEFAULT_COMPLETION_GRACE_SECONDS = 10 * 60


class WorkflowCompletionMonitor:
    """Request service shutdown after a terminal upload review window."""

    def __init__(
        self,
        terminal_probe: Callable[[], dict[str, Any] | None],
        request_shutdown: Callable[[], None],
        *,
        grace_seconds: float = DEFAULT_COMPLETION_GRACE_SECONDS,
        poll_seconds: float = 2.0,
    ) -> None:
        self.terminal_probe = terminal_probe
        self.request_shutdown = request_shutdown
        self.grace_seconds = max(0.0, float(grace_seconds))
        self.poll_seconds = max(0.05, float(poll_seconds))
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._status = "watching"
        self._terminal: dict[str, Any] | None = None
        self._deadline_monotonic: float | None = None
        self._scheduled_at: str | None = None
        self._shutdown_at: str | None = None
        self._shutdown_requested_at: str | None = None
        self._shutdown_reason = ""
        self._shutdown_timer: threading.Timer | None = None

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="tmall-workflow-completion-monitor",
                daemon=True,
            )
            self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        timer = self._shutdown_timer
        if timer is not None and timer.is_alive():
            timer.cancel()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(0.0, timeout))

    def public_status(self) -> dict[str, Any]:
        with self._lock:
            remaining_seconds: int | None = None
            if self._deadline_monotonic is not None:
                remaining_seconds = max(
                    0,
                    int(self._deadline_monotonic - time.monotonic() + 0.999),
                )
            return {
                "enabled": True,
                "status": self._status,
                "grace_seconds": int(self.grace_seconds),
                "remaining_seconds": remaining_seconds,
                "scheduled_at": self._scheduled_at,
                "shutdown_at": self._shutdown_at,
                "shutdown_requested_at": self._shutdown_requested_at,
                "shutdown_reason": self._shutdown_reason,
                "terminal_summary": str(
                    (self._terminal or {}).get("summary", "")
                ),
            }

    def request_manual_shutdown(self, *, delay_seconds: float = 0.25) -> None:
        """Close the managed backend after its HTTP response can be delivered."""

        self._request_stop(
            reason="user_requested",
            terminal={"summary": "用户主动结束当前任务"},
            delay_seconds=max(0.0, float(delay_seconds)),
        )

    def _schedule(self, terminal: dict[str, Any]) -> None:
        now = datetime.now(timezone.utc)
        with self._lock:
            if self._deadline_monotonic is not None:
                return
            self._terminal = dict(terminal)
            self._deadline_monotonic = time.monotonic() + self.grace_seconds
            self._scheduled_at = now.isoformat()
            self._shutdown_at = (
                now + timedelta(seconds=self.grace_seconds)
            ).isoformat()
            self._status = "review_window"

    def _request_stop(
        self,
        *,
        reason: str = "terminal_completed",
        terminal: dict[str, Any] | None = None,
        delay_seconds: float = 0.0,
    ) -> None:
        with self._lock:
            if self._status == "closing":
                return
            if terminal is not None:
                self._terminal = dict(terminal)
            self._status = "closing"
            self._shutdown_reason = reason
            self._shutdown_requested_at = datetime.now(timezone.utc).isoformat()
            timer: threading.Timer | None = None
            if delay_seconds > 0:
                timer = threading.Timer(delay_seconds, self.request_shutdown)
                timer.daemon = True
                self._shutdown_timer = timer
        if timer is not None:
            timer.start()
        else:
            self.request_shutdown()

    def _run(self) -> None:
        while not self._stop.is_set():
            if self._deadline_monotonic is None:
                try:
                    terminal = self.terminal_probe()
                except (KeyError, OSError, RuntimeError, TypeError, ValueError):
                    terminal = None
                if terminal is not None:
                    self._schedule(terminal)
            deadline = self._deadline_monotonic
            if deadline is not None and time.monotonic() >= deadline:
                self._request_stop(reason="terminal_completed")
                return
            wait_seconds = self.poll_seconds
            if deadline is not None:
                wait_seconds = min(
                    wait_seconds,
                    max(0.01, deadline - time.monotonic()),
                )
            self._stop.wait(wait_seconds)


__all__ = [
    "DEFAULT_COMPLETION_GRACE_SECONDS",
    "WorkflowCompletionMonitor",
]
