"""User-triggered native folder selection for the localhost interaction UI."""

from __future__ import annotations

from pathlib import Path
import threading


_PICKER_LOCK = threading.Lock()


def choose_directory(initial_path: str | None = None) -> str | None:
    """Open the operating-system folder dialog and return the selected full path."""

    import tkinter as tk
    from tkinter import filedialog

    if not _PICKER_LOCK.acquire(blocking=False):
        raise RuntimeError("another folder picker is already open")
    root = None
    try:
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        options: dict[str, object] = {
            "parent": root,
            "title": "选择图片源根目录",
            "mustexist": True,
        }
        if initial_path:
            candidate = Path(initial_path).expanduser()
            if candidate.is_dir():
                options["initialdir"] = str(candidate)
        selected = filedialog.askdirectory(**options)
    except tk.TclError as error:
        raise RuntimeError(f"native folder dialog is unavailable: {error}") from error
    finally:
        if root is not None:
            root.destroy()
        _PICKER_LOCK.release()
    return str(Path(selected)) if selected else None
