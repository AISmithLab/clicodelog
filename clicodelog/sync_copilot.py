"""Copy only Copilot CLI event logs; keep rewound/replaced histories."""

import json
from pathlib import Path

from .agent_paths import copilot_sessions_dir
from .editors import safe_name
from .storage import keep_superseded


def source_roots() -> list[Path]:
    return [copilot_sessions_dir()]


def _ids(path):
    ids = set()
    with open(path, encoding="utf-8-sig", errors="replace") as fh:
        for line in fh:
            try:
                row = json.loads(line)
                if isinstance(row, dict) and isinstance(row.get("id"), str):
                    ids.add(row["id"])
            except ValueError:
                continue
    return ids


def sync(dest_dir: Path, stats: dict, copy) -> bool:
    root = copilot_sessions_dir()
    if not root.is_dir():
        return False
    dest = dest_dir / "sessions"
    dest.mkdir(parents=True, exist_ok=True)
    # Before per-session directories, the log itself was <id>.jsonl.
    files = {f.stem: f for f in sorted(root.glob("*.jsonl")) if f.is_file()}
    files.update({f.parent.name: f for f in sorted(root.glob("*/events.jsonl")) if f.is_file()})
    for sid, src in files.items():
        target = dest / (safe_name(sid) + ".jsonl")
        try:
            if target.exists() and (src.stat().st_size, src.stat().st_mtime) != \
                    (target.stat().st_size, target.stat().st_mtime):
                if _ids(target) - _ids(src) and keep_superseded(target) is None:
                    stats["failed"] += 1
                    continue
            copy(src, target, stats)
        except OSError:
            stats["failed"] += 1
    return True
