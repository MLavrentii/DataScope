"""Remember what you were doing.

The window state that matters is not just geometry: it is the files you had
loaded, the dictionary, the output folder, the profile you were editing
(including edits you never saved), and the last folder each dialog was
pointed at.  All of it lives in one JSON file next to the settings, written
atomically so a crash mid-write cannot leave an unreadable session.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Optional

from .models import Profile, user_data_dir

SESSION_VERSION = 1
MAX_RECENT = 12


def session_path() -> Path:
    return user_data_dir() / "session.json"


def _empty() -> dict[str, Any]:
    return {
        "version": SESSION_VERSION,
        "files": [],
        "dictionary": "",
        "out_dir": "",
        "profile_path": "",
        "profile": None,          # the working copy, including unsaved edits
        "dirs": {},               # last folder per dialog: files / folder / dict / out / profile
        "recent_folders": [],
        "recent_profiles": [],
        "tab": 0,
        "autoload": True,
        "autoscan": False,
        "autosort": False,        # keep the file list in name order
    }


def load_session() -> dict[str, Any]:
    """Never raises: a damaged session file just means a fresh start."""
    path = session_path()
    data = _empty()
    if not path.exists():
        return data
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return data
    if not isinstance(raw, dict):
        return data
    for key, default in data.items():
        value = raw.get(key, default)
        if type(default) is type(value) or default is None or value is None:
            data[key] = value
    # drop files that have since been moved or deleted
    data["files"] = [f for f in data.get("files", []) if isinstance(f, str) and Path(f).exists()]
    data["recent_folders"] = [d for d in data.get("recent_folders", [])
                              if isinstance(d, str) and Path(d).is_dir()]
    return data


def save_session(data: dict[str, Any]) -> Optional[Path]:
    """Atomic write - the old session survives if this fails half way."""
    path = session_path()
    payload = dict(_empty())
    payload.update({k: v for k, v in data.items() if k in payload})
    payload["version"] = SESSION_VERSION
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return path
    except OSError:
        return None


def remember_dir(data: dict[str, Any], key: str, path: Any) -> None:
    """Store the folder a dialog was last pointed at, per dialog."""
    if not path:
        return
    p = Path(path)
    folder = p if p.is_dir() else p.parent
    if not folder.exists():
        return
    data.setdefault("dirs", {})[key] = str(folder)
    recent = [d for d in data.get("recent_folders", []) if d != str(folder)]
    recent.insert(0, str(folder))
    data["recent_folders"] = recent[:MAX_RECENT]


def last_dir(data: dict[str, Any], key: str, fallback: str = "") -> str:
    value = (data.get("dirs") or {}).get(key, "")
    if value and Path(value).is_dir():
        return value
    for other in (data.get("recent_folders") or []):
        if Path(other).is_dir():
            return other
    return fallback


def profile_from_session(data: dict[str, Any]) -> Optional[Profile]:
    raw = data.get("profile")
    if not isinstance(raw, dict):
        return None
    try:
        return Profile.from_dict(raw)
    except Exception:
        return None
