"""Full-text rows for sources read through their parser (VS Code, Cursor).

fts.py extracts line by line, which only works for logs where each line is a
self-contained record. These files have to be replayed or assembled first, so
they are parsed whole and re-indexed whole when they change. They are small:
a chat, not a multi-hundred-megabyte agent run.
"""

import json
from pathlib import Path

from .parsers import WHOLE_FILE_PARSERS

TEXT_CAP = 200_000


def extract_parsed(path: Path, source_id: str):
    """Yield (uuid, role, kind, ts, dialog, tool, edited_path) rows."""
    conv = WHOLE_FILE_PARSERS[source_id](path, path.stem)
    for s in conv.get("summaries") or []:
        if isinstance(s, str) and s.strip():
            yield (None, "summary", "summary", None, s[:TEXT_CAP], "", None)
    for m in conv.get("messages") or []:
        uid, role, ts = m.get("uuid"), m["role"], m.get("timestamp")
        if m.get("content"):
            yield (uid, role, "text", ts, m["content"][:TEXT_CAP], "", None)
        if m.get("thinking"):
            yield (uid, role, "thinking", ts, m["thinking"][:TEXT_CAP], "", None)
        for t in m.get("tool_uses") or []:
            inp = t.get("input")
            try:
                inp_s = inp if isinstance(inp, str) else json.dumps(inp, ensure_ascii=False)
            except (TypeError, ValueError):
                inp_s = str(inp)
            yield (uid, role, "tool_use", ts, "", f"{t.get('name') or ''} {inp_s}", None)
            if isinstance(t.get("result"), str) and t["result"].strip():
                yield (uid, role, "tool_result", ts, "", t["result"], None)
        for f in m.get("edited_files") or []:
            yield (uid, role, "edit", ts, "", "", f)
