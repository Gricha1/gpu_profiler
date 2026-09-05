"""Local filesystem browse for New Cursor Project on this PC."""

from __future__ import annotations

import os
import string
from pathlib import Path
from typing import Any

from cursor_projects import is_safe_local_path


def _slash(path: Path | str) -> str:
    return str(path).replace("\\", "/")


def _entry(path: Path) -> dict[str, Any] | None:
    try:
        if not path.is_dir():
            return None
    except OSError:
        return None
    has_git = (path / ".git").is_dir()
    has_children = False
    try:
        for child in path.iterdir():
            if child.name.startswith("."):
                continue
            try:
                if child.is_dir():
                    has_children = True
                    break
            except OSError:
                continue
    except OSError:
        pass
    return {
        "name": path.name or _slash(path),
        "path": _slash(path),
        "is_git": has_git,
        "has_children": has_children,
    }


def fs_list(path: str | None = None) -> dict[str, Any]:
    home = Path.home()
    if path is None:
        entries: list[dict[str, Any]] = []
        home_e = _entry(home)
        if home_e:
            home_e["name"] = f"home ({home.name})"
            entries.append(home_e)
        if os.name == "nt":
            for letter in string.ascii_uppercase:
                drive = Path(f"{letter}:/")
                try:
                    if drive.exists():
                        e = _entry(drive)
                        if e:
                            e["name"] = f"{letter}:"
                            entries.append(e)
                except OSError:
                    continue
        return {
            "ok": True,
            "path": _slash(home),
            "parent": None,
            "home": _slash(home),
            "entries": entries,
        }

    if not is_safe_local_path(path):
        return {"ok": False, "error": "unsafe path", "path": path, "entries": []}

    root = Path(path).resolve()
    entries = []
    try:
        names = sorted(os.listdir(root), key=str.lower)
    except OSError as exc:
        return {"ok": False, "error": str(exc), "path": _slash(root), "entries": []}

    for name in names:
        if name.startswith("."):
            continue
        e = _entry(root / name)
        if e:
            entries.append(e)

    parent = root.parent
    return {
        "ok": True,
        "path": _slash(root),
        "parent": _slash(parent) if parent != root else None,
        "home": _slash(home),
        "entries": entries,
    }
