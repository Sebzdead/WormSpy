"""Session folders (ADR 0007): `<date>_<animal-id>/` holding `session.json`.

`session.json` snapshots the operator's metadata form, the full config (preset, calibration,
…) and provenance (software version, git commit if available), so a session describes
itself.
"""

import json
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

import wormspy

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def create_session(root: Path, metadata: dict[str, Any], config: dict[str, Any],
                   now: datetime | None = None) -> Path:
    now = now or datetime.now().astimezone()  # local time, with its UTC offset
    animal = _UNSAFE.sub("_", str(metadata.get("animal_id") or "")).strip("_") or "unnamed"
    base = f"{now:%Y-%m-%d}_{animal}"
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    path, n = root / base, 1
    while True:
        try:
            path.mkdir()
            break
        except FileExistsError:
            n += 1
            path = root / f"{base}_{n}"
    session = {
        "created": now.isoformat(timespec="seconds"),
        "metadata": metadata,
        "config": config,
        "software": {"name": "wormspy", "version": wormspy.__version__, "git": _git_commit()},
    }
    (path / "session.json").write_text(json.dumps(session, indent=2, default=str))
    return path


def _git_commit() -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=Path(__file__).parent,
                             capture_output=True, text=True, timeout=2, check=True)
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None
