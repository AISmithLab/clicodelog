"""Session metadata for sources read through their parser.

scan.py reads the line-per-record logs in one streaming pass. VS Code's mutation
log and Cursor's export only mean something once replayed or assembled, so for
those the parsed conversation is summarised instead — still one read per file,
producing exactly the fields scan_session() returns for every other source.
"""

from datetime import datetime
from pathlib import Path

from .logging_setup import get_logger
from .parsers import WHOLE_FILE_PARSERS
from .scan import MAX_FILES_TOUCHED, SUMMARY_CHARS

log = get_logger(__name__)


def scan_parsed(path: Path, source_id: str, st) -> dict | None:
    try:
        conv = WHOLE_FILE_PARSERS[source_id](path, path.stem)
    except OSError as e:
        log.warning("Could not read %s: %s", path, e)
        return None
    except Exception:
        log.exception("Could not parse %s", path)
        return None

    messages = conv.get("messages") or []
    if not messages:
        return None                     # an empty chat, opened and never used

    usage = {"input": 0, "output": 0, "cache_read": 0, "cache_creation": 0}
    models, tools, files = {}, {}, []
    first_user = None
    stamps = [m["timestamp"] for m in messages if m.get("timestamp")]
    for m in messages:
        if m["role"] == "user" and not first_user and m.get("content"):
            first_user = " ".join(m["content"].split())[:SUMMARY_CHARS]
        if m.get("model"):
            models[m["model"]] = models.get(m["model"], 0) + 1
        u = m.get("usage") or {}
        usage["input"] += u.get("input_tokens") or 0
        usage["output"] += u.get("output_tokens") or 0
        usage["cache_read"] += u.get("cache_read_input_tokens") or 0
        usage["cache_creation"] += u.get("cache_creation_input_tokens") or 0
        for t in m.get("tool_uses") or []:
            name = t.get("name") or "tool"
            tools[name] = tools.get(name, 0) + 1
        for f in m.get("edited_files") or []:
            if f not in files and len(files) < MAX_FILES_TOUCHED:
                files.append(f)

    meta = conv.get("meta") or {}
    summaries = conv.get("summaries") or []
    return {
        "id": path.stem,
        "filename": path.name,
        "summary": (summaries[0] if summaries else None) or first_user or "No summary",
        "message_count": len(messages),
        "first_timestamp": meta.get("startTime") or (min(stamps) if stamps else None),
        "last_timestamp": max(stamps) if stamps else meta.get("lastUpdated"),
        "size": st.st_size,
        "mtime": st.st_mtime,
        "modified": datetime.fromtimestamp(st.st_mtime).isoformat(),
        "full_path": str(path),
        # Cursor sub-agents live in other files; the index fills this in from
        # one shared walk (editor_rows.subagent_counts). VS Code has none.
        "subagent_count": 0,
        "cwd": meta.get("cwd") or "",
        "usage": usage,
        "models": models,
        "tools": tools,
        "files_touched": files,
        "gemini_hash": "",
    }

