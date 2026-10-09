"""Session listings.

These are served from the metadata index rather than by re-reading files. The
previous implementation called a full line-by-line parse on every session in the
project for every request: 15.5 seconds and 2.5 GB of reads on the largest
project here, repeated identically on every click, for data the index already
held under the same (size, mtime) key.
"""

from pathlib import Path

from . import search_index as _idx
from .config import DATA_DIR, SOURCES
from .logging_setup import get_logger
from .parsers import WHOLE_FILE_PARSERS
from .scan import scan_session
from .utils import decode_path_id, is_safe_id

log = get_logger(__name__)


def _as_session(entry: dict) -> dict:
    """Index entry -> the shape the frontend consumes."""
    return {
        "id": entry["session_id"],
        "filename": entry.get("filename", entry["session_id"] + ".jsonl"),
        "summary": entry.get("summary") or "No summary",
        "message_count": entry.get("msg_count", 0),
        "first_timestamp": entry.get("first_ts"),
        "last_timestamp": entry.get("last_ts"),
        "size": entry.get("size", 0),
        "modified": entry.get("modified"),
        "full_path": entry.get("full_path", ""),
        "subagent_count": entry.get("subagent_count", 0),
        "cwd": entry.get("cwd", ""),
        "usage": entry.get("usage", {}),
        "models": entry.get("models", {}),
    }


# A project here holds up to 10,581 sessions. Returning them all costs ~21 MB of
# server RAM and a 7 MB response for a list nobody scrolls to the end of, so the
# listing is paged and reports its true total.
DEFAULT_PAGE = 500


def get_sessions(project_id: str, source_id: str, *,
                 limit: int | None = DEFAULT_PAGE, offset: int = 0) -> dict:
    if source_id not in SOURCES or not is_safe_id(project_id):
        return {"sessions": [], "total": 0, "offset": 0}

    total = _idx.project_session_count(source_id, project_id)
    if total:
        entries = _idx.sessions_for_project(source_id, project_id,
                                            limit=limit, offset=offset)
        return {
            "sessions": [_as_session(e) for e in entries],
            "total": total,
            "offset": offset,
        }

    # Index not built yet (first run, or a source never synced). Fall back to
    # scanning so the UI is never empty just because of timing.
    if _idx.is_ready(source_id):
        return {"sessions": [], "total": 0, "offset": 0}
    rows = _scan_project(project_id, source_id)
    return {"sessions": rows, "total": len(rows), "offset": 0}


def get_subagent_sessions(project_id: str, session_id: str, source_id: str) -> list:
    if source_id not in ("claude-code", "cursor") or not (
            is_safe_id(project_id) and is_safe_id(session_id)):
        return []
    entries = _idx.subagent_sessions(
        source_id, project_id if source_id == "claude-code" else None, session_id)
    if entries or source_id != "claude-code":
        return [_as_session(e) for e in entries]

    sub_dir = DATA_DIR / SOURCES[source_id]["data_subdir"] / project_id / session_id
    if not sub_dir.is_dir():
        return []
    out = []
    for f in sorted(sub_dir.rglob("*.jsonl"), key=_safe_mtime, reverse=True):
        info = scan_session(f, source_id)
        if info:
            out.append(_scan_to_session(info))
    return out


def _safe_mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0          # a file removed mid-sync must not 500 the listing


def _scan_to_session(info: dict) -> dict:
    return {
        "id": info["id"],
        "filename": info["filename"],
        "summary": info["summary"],
        "message_count": info["message_count"],
        "first_timestamp": info["first_timestamp"],
        "last_timestamp": info["last_timestamp"],
        "size": info["size"],
        "modified": info["modified"],
        "full_path": info["full_path"],
        "subagent_count": info["subagent_count"],
        "cwd": info["cwd"],
        "usage": info["usage"],
        "models": info["models"],
    }


def _scan_project(project_id: str, source_id: str) -> list:
    """Direct filesystem fallback used only before the index exists."""
    data_dir = DATA_DIR / SOURCES[source_id]["data_subdir"]
    files: list[Path] = []

    if source_id == "claude-code":
        project_dir = data_dir / project_id
        if not project_dir.is_dir():
            return []
        files = list(project_dir.glob("*.jsonl"))
    elif source_id == "codex":
        try:
            target = decode_path_id(project_id)
        except Exception:
            return []
        for f in data_dir.rglob("*.jsonl"):
            info = scan_session(f, source_id)
            if info and info["cwd"] == target:
                files.append(f)
    elif source_id in WHOLE_FILE_PARSERS:
        if not data_dir.is_dir():
            return []
        # Before the index exists, list only what sits directly under the
        # project's own directory; Cursor's slug grouping needs the index.
        files = [f for f, pdir in _idx.session_files(source_id, data_dir)
                 if pdir.name == project_id
                 and "subagents" not in f.relative_to(data_dir).parts]
    else:
        for f in data_dir.rglob("chats/session-*.jsonl"):
            if f.parent.parent.name == project_id:
                files.append(f)

    out = []
    for f in sorted(files, key=_safe_mtime, reverse=True):
        info = scan_session(f, source_id)
        if info:
            out.append(_scan_to_session(info))
    return out
