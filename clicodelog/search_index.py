"""Auto-maintained metadata index — the read model behind listings and search.

One small record per session (id, project, cwd, summary, timestamps, counts,
token usage, models, tools). Because it already holds every field a session
listing needs, listings are served from here instead of re-reading gigabytes of
JSONL on every click.

Freshness: DATA_DIR is written only by sync_data, and sync_data refreshes the
index straight after copying. There is therefore nothing to detect between
syncs, and the old 5-second TTL — which re-stat'd 15k files and rewrote a 9.8 MB
JSON blob before every search, costing 25x more than the query it guarded — has
been removed.

Durability: writes are atomic (temp + os.replace) and skipped entirely when
nothing changed. A single lock serialises refreshes so the sync thread and a
request thread cannot interleave.
"""

import threading
import time
from pathlib import Path

from .config import APP_DATA_DIR, DATA_DIR, SOURCES
from .logging_setup import get_logger
from .scan import scan_session
from .storage import load_json, write_json
from .utils import encode_path_id

log = get_logger(__name__)

INDEX_FILE = APP_DATA_DIR / "search_index.json"

# Bumped when the entry shape changes, so stale entries are rebuilt rather than
# served with missing fields.
SCHEMA_VERSION = 2

_index: dict = {}
_meta: dict = {}
_loaded = False
_lock = threading.RLock()          # guards _index/_meta and serialises refreshes
_building: set = set()             # sources with a refresh in flight


def _load() -> None:
    global _index, _meta, _loaded
    with _lock:
        if _loaded:
            return
        raw = load_json(INDEX_FILE, {})
        if isinstance(raw, dict) and raw.get("_schema") == SCHEMA_VERSION:
            _meta = raw.get("_meta", {})
            _index = {k: v for k, v in raw.items() if not k.startswith("_")}
        else:
            if raw:
                log.info("Index schema changed (v%s -> v%s); rebuilding on next refresh",
                         raw.get("_schema") if isinstance(raw, dict) else "?", SCHEMA_VERSION)
            _index, _meta = {}, {}
        _loaded = True


def _save() -> None:
    payload = dict(_index)
    payload["_schema"] = SCHEMA_VERSION
    payload["_meta"] = _meta
    write_json(INDEX_FILE, payload)


def session_files(source_id: str, data_dir: Path):
    """Yield (file, project_dir) for every session file of a source."""
    try:
        if source_id == "claude-code":
            for project_dir in data_dir.iterdir():
                if project_dir.is_dir():
                    for f in project_dir.rglob("*.jsonl"):
                        yield f, project_dir
        elif source_id == "codex":
            for f in data_dir.rglob("*.jsonl"):
                yield f, None
        else:
            # Gemini CLI writes JSON Lines with a .jsonl extension. The previous
            # glob asked for "chats/session-*.json", which matched nothing, so
            # this source silently reported zero sessions.
            for f in data_dir.rglob("chats/session-*.jsonl"):
                yield f, None
    except OSError as e:
        log.warning("Could not walk %s: %s", data_dir, e)


def _entry_for(source_id: str, f: Path, project_dir) -> dict | None:
    info = scan_session(f, source_id)
    if info is None:
        return None

    if source_id == "claude-code":
        project_id = project_dir.name
        # The directory name is a lossy hash: Claude Code collapses "/", "_" and
        # "-" all into "-", so it cannot be decoded back to a path. The session's
        # own cwd is authoritative; fall back to the old rendering only when a
        # session recorded no cwd at all.
        project_name = info["cwd"] or project_dir.name.replace("-", "/").lstrip("/")
    elif source_id == "codex":
        cwd = info["cwd"]
        if not cwd:
            return None
        project_id = encode_path_id(cwd)
        project_name = cwd
    else:
        # Group Gemini by its directory, which carries a readable project name,
        # rather than by the 64-character projectHash buried in the file.
        try:
            rel = f.relative_to(DATA_DIR / SOURCES["gemini"]["data_subdir"])
            project_id = rel.parts[0] if rel.parts else (info["gemini_hash"] or "unknown")
        except ValueError:
            project_id = info["gemini_hash"] or "unknown"
        project_name = project_id

    return {
        "session_id": info["id"],
        "filename": info["filename"],
        "project_id": project_id,
        "project_name": project_name,
        "cwd": info["cwd"],
        "summary": info["summary"],
        "first_ts": info["first_timestamp"],
        "last_ts": info["last_timestamp"],
        "msg_count": info["message_count"],
        "subagent_count": info["subagent_count"],
        "size": info["size"],
        "mtime": info["mtime"],
        "modified": info["modified"],
        "full_path": info["full_path"],
        "usage": info["usage"],
        "models": info["models"],
        "tools": info["tools"],
    }


def refresh_index(source_id: str | None = None, *, force: bool = False) -> dict:
    """Incrementally refresh one source (or all). Only files whose (size, mtime)
    changed are re-read; entries for files that vanished from the backup are
    dropped. Returns per-source counts."""
    _load()
    sources = [source_id] if source_id else list(SOURCES.keys())
    result: dict = {}

    for sid in sources:
        if sid not in SOURCES:
            continue
        data_dir = DATA_DIR / SOURCES[sid]["data_subdir"]
        if not data_dir.exists():
            result[sid] = {"sessions": 0, "projects": 0, "changed": 0}
            continue

        with _lock:
            if sid in _building:
                log.debug("Refresh for %s already running; skipping", sid)
                continue
            _building.add(sid)
        try:
            old = _index.get(sid, {})
            had_saved_index = bool(_meta.get(sid))
            new: dict = {}
            changed = 0
            started = time.monotonic()

            for f, project_dir in session_files(sid, data_dir):
                key = str(f)
                try:
                    st = f.stat()
                except OSError:
                    continue
                prev = old.get(key)
                if (not force and prev
                        and prev.get("size") == st.st_size
                        and prev.get("mtime") == st.st_mtime):
                    new[key] = prev
                    continue
                entry = _entry_for(sid, f, project_dir)
                if entry:
                    new[key] = entry
                    changed += 1

            dropped = len(old) - sum(1 for k in old if k in new)
            with _lock:
                _index[sid] = new
                _meta[sid] = {"count": len(new), "updated": time.time()}
            if changed or dropped:
                log.info("Index %s: %d sessions (%d rescanned, %d gone) in %.2fs",
                         sid, len(new), changed, max(dropped, 0), time.monotonic() - started)
            result[sid] = {
                "sessions": len(new),
                "projects": len({e["project_id"] for e in new.values()}),
                "changed": changed,
            }
            # Only pay the 10 MB write when something actually moved.
            if changed or dropped > 0 or not had_saved_index:
                _save()
        finally:
            with _lock:
                _building.discard(sid)

    return result


def prev_saved(source_id: str) -> bool:
    return bool(_meta.get(source_id))


def entries(source_id: str) -> list:
    _load()
    with _lock:
        return list(_index.get(source_id, {}).values())


def is_ready(source_id: str) -> bool:
    _load()
    return bool(_index.get(source_id))


def entry_for_session(source_id: str, project_id: str, session_id: str) -> dict | None:
    for e in entries(source_id):
        if e["session_id"] == session_id and e["project_id"] == project_id:
            return e
    return None


def sessions_for_project(source_id: str, project_id: str, *, top_level_only: bool = True) -> list:
    """Session listing straight from the index — no file reads.

    This replaced a full re-parse of every file in the project, which measured
    15.5 seconds on the largest project here and ran again on every click.
    """
    out = []
    for e in entries(source_id):
        if e["project_id"] != project_id:
            continue
        if top_level_only and source_id == "claude-code":
            # Subagent transcripts live in <project>/<session-id>/ and are listed
            # separately by the expander, not inline with top-level sessions.
            p = Path(e["full_path"])
            if p.parent.name != project_id:
                continue
        out.append(e)
    out.sort(key=lambda e: e.get("mtime") or 0, reverse=True)
    return out


def subagent_sessions(source_id: str, project_id: str, session_id: str) -> list:
    if source_id != "claude-code":
        return []
    out = []
    for e in entries(source_id):
        if e["project_id"] != project_id:
            continue
        p = Path(e["full_path"])
        if session_id in p.parts[:-1]:
            out.append(e)
    out.sort(key=lambda e: e.get("mtime") or 0, reverse=True)
    return out


def projects_for_source(source_id: str) -> list:
    """Aggregate projects from the index instead of stat-walking every file."""
    groups: dict = {}
    for e in entries(source_id):
        pid = e["project_id"]
        g = groups.get(pid)
        if g is None:
            g = groups[pid] = {
                "id": pid,
                "name": e["project_name"],
                "cwd": e.get("cwd", ""),
                "session_count": 0,
                "_mtime": 0.0,
            }
        g["session_count"] += 1
        m = e.get("mtime") or 0
        if m > g["_mtime"]:
            g["_mtime"] = m
            if e.get("cwd"):
                g["name"] = e["cwd"]        # prefer the most recent recorded cwd
    return list(groups.values())


def search_index(query: str, source_id: str) -> list:
    """Match against session id, cwd, summary and project name. In-memory."""
    q = (query or "").strip().lower()
    if not q:
        return []
    exact, partial = [], []
    for e in entries(source_id):
        sid = e.get("session_id", "")
        if sid.lower() == q:
            exact.append(e)
            continue
        hay = " ".join([sid, e.get("cwd", ""), e.get("summary", ""),
                        e.get("project_name", "")]).lower()
        if q in hay:
            partial.append(e)

    out, seen = [], set()
    for e in exact + partial:
        k = (e["project_id"], e["session_id"])
        if k in seen:
            continue
        seen.add(k)
        out.append({
            "project_id": e["project_id"],
            "session_id": e["session_id"],
            "project_name": e["project_name"],
            "cwd": e.get("cwd", ""),
            "summary": e.get("summary", ""),
            "last_ts": e.get("last_ts"),
            "msg_count": e.get("msg_count", 0),
        })
    return out
