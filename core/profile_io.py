"""Load / save analysis profiles (JSON) - one file per project pattern."""

from __future__ import annotations

import json
from pathlib import Path

from .models import Profile, default_rules, project_root, user_data_dir


def profiles_dirs() -> list[Path]:
    """Bundled profiles first, then the user's own (which may override)."""
    dirs = [project_root() / "profiles", user_data_dir() / "profiles"]
    for d in dirs:
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
    return dirs


def list_profiles() -> list[Path]:
    seen: dict[str, Path] = {}
    for d in profiles_dirs():
        for p in sorted(d.glob("*.json")):
            seen[p.name] = p        # later dirs win
    return list(seen.values())


def load_profile(path: Path) -> Profile:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    prof = Profile.from_dict(data)
    if not prof.rules:
        prof.rules = default_rules()
    return prof


def save_profile(profile: Profile, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(profile.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def new_profile(name: str = "new project") -> Profile:
    p = Profile(name=name)
    p.rules = default_rules()
    return p
