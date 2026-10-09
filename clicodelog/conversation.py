"""Conversation loading, with pagination and a small parsed-result cache.

Two changes matter here. Session files are located by their deterministic path
instead of an rglob over the whole project directory, and the response can be
windowed: the largest session on this machine produced a 138 MB JSON body that
the browser had to parse in one shot before rendering anything, most of it tool
output the viewer never displays.
"""

import glob
import threading
from collections import OrderedDict
from pathlib import Path

from .config import DATA_DIR, SOURCES
from .logging_setup import get_logger
from .parsers import PARSERS, WHOLE_FILE_PARSERS
from .utils import decode_path_id, is_safe_id, safe_child

log = get_logger(__name__)

# Parsed conversations are cached so paging through one does not re-parse it.
# Only modest files are cached — holding a 966 MB session resident costs ~600 MB
# of RSS, which is not a trade worth making for a scroll.
_CACHE_MAX_ENTRIES = 3
_CACHE_MAX_FILE_BYTES = 50 * 1024 * 1024
_cache: "OrderedDict[tuple, dict]" = OrderedDict()
_cache_lock = threading.Lock()


def _cache_get(key):
    with _cache_lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]
    return None


def _cache_put(key, value):
    with _cache_lock:
        _cache[key] = value
        _cache.move_to_end(key)
        while len(_cache) > _CACHE_MAX_ENTRIES:
            _cache.popitem(last=False)


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def find_session_path(project_id: str, session_id: str, source_id: str):
    """Resolve a session's raw source file path (or None)."""
    if source_id not in SOURCES:
        return None
    if not (is_safe_id(project_id) and is_safe_id(session_id)):
        return None
    data_dir = DATA_DIR / SOURCES[source_id]["data_subdir"]
    return _find_session_file(data_dir, project_id, session_id, source_id)


def _find_session_file(data_dir: Path, project_id: str, session_id: str, source_id: str):
    if source_id == "claude-code":
        project_dir = safe_child(data_dir, project_id)
        if project_dir is None or not project_dir.is_dir():
            return None
        # Top-level sessions sit at a known path; try it before walking anything.
        direct = project_dir / f"{session_id}.jsonl"
        if direct.is_file():
            return direct
        # Subagent transcripts live one level down, under their parent session.
        for f in project_dir.glob(f"*/{session_id}.jsonl"):
            return f
        for f in project_dir.rglob(f"{session_id}.jsonl"):
            return f
        return None

    if source_id == "codex":
        from . import search_index as _idx
        entry = _idx.entry_for_session(source_id, project_id, session_id)
        if entry:
            p = Path(entry["full_path"])
            if p.is_file():
                return p
        try:
            target_cwd = decode_path_id(project_id)
        except Exception:
            return None
        from .utils import get_codex_cwd
        for f in data_dir.rglob(f"{session_id}.jsonl"):
            if get_codex_cwd(f) == target_cwd:
                return f
        return None

    if source_id in WHOLE_FILE_PARSERS:
        return _find_editor_file(data_dir, project_id, session_id, source_id)

    # gemini — files are .jsonl and grouped by their directory
    project_dir = safe_child(data_dir, project_id)
    if project_dir is not None:
        direct = project_dir / "chats" / f"{session_id}.jsonl"
        if direct.is_file():
            return direct
    for f in data_dir.rglob(f"chats/{session_id}.jsonl"):
        return f
    return None


def _find_editor_file(data_dir: Path, project_id: str, session_id: str, source_id: str):
    """The index knows the path; a Cursor sub-agent may sit under a different
    project than the chat it is opened from, so fall back to the id alone."""
    from . import search_index as _idx
    entry = _idx.entry_for_session(source_id, project_id, session_id)
    candidate = entry["full_path"] if entry else _idx.path_for_session_id(source_id, session_id)
    if candidate:
        p = Path(candidate)
        try:
            p.resolve().relative_to(data_dir.resolve())
        except (ValueError, OSError):
            return None
        if p.is_file():
            return p
    if source_id == "vscode":
        ws = safe_child(data_dir, project_id)
        for ext in (".jsonl", ".json"):
            if ws is not None and (ws / "chatSessions" / f"{session_id}{ext}").is_file():
                return ws / "chatSessions" / f"{session_id}{ext}"
        return None
    # The id is matched literally: "*" or "[" in it must not act as a wildcard.
    name = glob.escape(session_id) + ".jsonl"
    for kind in ("composers", "cli"):
        for f in (data_dir / kind).glob(f"*/{name}"):
            return f
    for f in (data_dir / "transcripts").rglob(name):
        return f
    return None


def _strip_tool_results(messages: list) -> None:
    """Drop tool output the viewer never renders.

    buildMessageEl shows a tool's name and input only; the result is used solely
    by the client-side export, which now fetches it from the export endpoint.
    On large sessions this is the bulk of the payload.
    """
    for m in messages:
        for tool in m.get("tool_uses") or []:
            if "result" in tool:
                tool["result"] = None


def get_conversation(project_id: str, session_id: str, source_id: str,
                     *, offset: int = 0, limit: int | None = None,
                     include_results: bool = False, tail: bool = False) -> dict:
    if source_id not in SOURCES:
        return {"error": "Unknown source"}
    if not (is_safe_id(project_id) and is_safe_id(session_id)):
        return {"error": "Invalid session or project id"}

    session_file = find_session_path(project_id, session_id, source_id)
    if not session_file or not session_file.exists():
        return {"error": "Session not found"}

    try:
        st = session_file.stat()
    except OSError:
        return {"error": "Session file unavailable"}

    # Fast path: a windowed read of a Claude session parses only the requested
    # page. Whole-file parsing is reserved for exports, which genuinely need
    # tool outputs and the tool_use_id -> result mapping that requires them.
    if (source_id == "claude-code" and not include_results and limit is not None):
        try:
            from .window import read_window
            messages, total, summaries, start = read_window(
                session_file, st.st_size, st.st_mtime, max(0, offset), limit, tail)
            _strip_tool_results(messages)
            return {
                "summaries": summaries,
                "session_id": session_id,
                "meta": {"cwd": messages[0].get("cwd") if messages else None},
                "messages": messages,
                "total_messages": total,
                "offset": start,
                "truncated": (start + len(messages)) < total,
            }
        except OSError:
            log.exception("Windowed read failed for %s; falling back", session_file)

    key = (str(session_file), st.st_size, st.st_mtime, include_results)
    conv = _cache_get(key)

    if conv is None:
        try:
            conv = PARSERS[source_id](session_file, session_id)
        except Exception:
            # A file copied mid-write can be truncated. Say so instead of 500ing.
            log.exception("Failed to parse %s", session_file)
            return {"error": "Session file could not be read (it may be mid-sync). Try again."}

        if not include_results:
            _strip_tool_results(conv.get("messages") or [])
        if st.st_size <= _CACHE_MAX_FILE_BYTES:
            _cache_put(key, conv)

    messages = conv.get("messages") or []
    total = len(messages)
    if limit is not None:
        start = max(0, total - limit) if tail else max(0, offset)
        window = messages[start:start + max(0, limit)]
    else:
        start = 0
        window = messages

    return {
        "summaries": conv.get("summaries", []),
        "session_id": conv.get("session_id", session_id),
        "meta": conv.get("meta", {}),
        "messages": window,
        "total_messages": total,
        "offset": start,
        "truncated": limit is not None and (start + len(window)) < total,
    }
