"""Project listings, aggregated from the metadata index.

The previous implementation rglob'd and stat'd all 15k session files on every
/api/projects request (and, for Codex, opened every file to read its cwd). The
index already stores project id, name, cwd, size and mtime per session, so this
is now an in-memory group-by.
"""

from datetime import datetime

from . import search_index as _idx
from .config import DATA_DIR, SOURCES
from .metadata import get_project_meta_key, load_project_meta


def get_projects(source_id: str) -> list:
    if source_id not in SOURCES:
        return []

    data_dir = DATA_DIR / SOURCES[source_id]["data_subdir"]
    if not data_dir.exists():
        return []

    meta = load_project_meta()

    def _pm(pid):
        return meta.get(get_project_meta_key(pid, source_id), {})

    out = []
    for g in _idx.projects_for_source(source_id):
        pid = g["id"]
        last_mod = (datetime.fromtimestamp(g["_mtime"]).isoformat()
                    if g.get("_mtime") else None)
        out.append({
            "id": pid,
            "name": g["name"],
            "cwd": g.get("cwd", ""),
            "custom_name": _pm(pid).get("custom_name", ""),
            "tags": _pm(pid).get("tags", []),
            "session_count": g["session_count"],
            "last_modified": last_mod,
            "path": g.get("cwd") or str(data_dir / pid),
        })

    out.sort(key=lambda p: (p.get("last_modified") or ""), reverse=True)
    return out
