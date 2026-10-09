"""Text extraction for the full-text index: what of each record is searchable.

Each extractor yields (uuid, role, kind, ts, dialog, tool, edited_path) rows.
The line-per-record sources (Claude Code, Codex, Gemini) are read one JSON line
at a time, which lets fts.py index an appended log incrementally. VS Code's
mutation log and Cursor's exports only mean something once replayed or
assembled, so they are parsed whole by their parser and re-indexed whole.
"""

import json
from pathlib import Path

from .parsers import WHOLE_FILE_PARSERS

TEXT_CAP = 200_000


def _extract_claude(entry: dict):
    etype = entry.get("type")
    ts = entry.get("timestamp")
    uid = entry.get("uuid")
    if etype == "summary":
        s = entry.get("summary")
        if isinstance(s, str) and s.strip():
            yield (uid, "summary", "summary", ts, s[:TEXT_CAP], "", None)
        return
    if etype not in ("user", "assistant"):
        return
    msg = entry.get("message")
    if not isinstance(msg, dict):
        return
    content = msg.get("content")
    if isinstance(content, str):
        if content.strip():
            yield (uid, etype, "text", ts, content[:TEXT_CAP], "", None)
        return
    if not isinstance(content, list):
        return
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            t = block.get("text") or ""
            if t.strip():
                yield (uid, etype, "text", ts, t[:TEXT_CAP], "", None)
        elif btype == "thinking":
            t = block.get("thinking") or ""
            if t.strip():
                yield (uid, etype, "thinking", ts, t[:TEXT_CAP], "", None)
        elif btype == "tool_use":
            name = block.get("name") or ""
            inp = block.get("input")
            try:
                inp_s = json.dumps(inp, ensure_ascii=False)
            except (TypeError, ValueError):
                inp_s = str(inp)
            edited = None
            if isinstance(inp, dict) and name in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
                edited = inp.get("file_path") or inp.get("path")
            yield (uid, etype, "tool_use", ts, "", f"{name} {inp_s}", edited)
        elif btype == "tool_result":
            c = block.get("content")
            if isinstance(c, list):
                c = " ".join(b.get("text", "") for b in c if isinstance(b, dict))
            if isinstance(c, str) and c.strip():
                yield (uid, etype, "tool_result", ts, "", c, None)


def _extract_codex(entry: dict):
    ts = entry.get("timestamp")
    payload = entry.get("payload")
    if not isinstance(payload, dict):
        return
    if entry.get("type") == "response_item":
        role = payload.get("role")
        if role in ("user", "assistant"):
            parts = []
            for block in payload.get("content") or []:
                if isinstance(block, dict):
                    t = block.get("text")
                    if isinstance(t, str):
                        parts.append(t)
            text = "\n".join(parts)
            if text.strip():
                yield (payload.get("id"), role, "text", ts, text[:TEXT_CAP], "", None)
        if payload.get("type") == "function_call":
            yield (payload.get("id"), "assistant", "tool_use", ts, "",
                   f"{payload.get('name') or ''} {payload.get('arguments') or ''}", None)
        elif payload.get("type") == "function_call_output":
            out = payload.get("output")
            if isinstance(out, str) and out.strip():
                yield (payload.get("id"), "assistant", "tool_result", ts, "", out, None)
    elif entry.get("type") == "event_msg":
        p = payload.get("type")
        if p in ("user_message", "agent_message"):
            m = payload.get("message")
            if isinstance(m, str) and m.strip():
                role = "user" if p == "user_message" else "assistant"
                yield (None, role, "text", ts, m[:TEXT_CAP], "", None)


def _extract_gemini(entry: dict):
    def one(msg):
        if not isinstance(msg, dict):
            return
        mtype = msg.get("type")
        ts = msg.get("timestamp")
        uid = msg.get("id")
        content = msg.get("content")
        if isinstance(content, list):
            content = " ".join(b.get("text", "") for b in content if isinstance(b, dict))
        if not isinstance(content, str) or not content.strip():
            return
        role = "user" if mtype == "user" else ("assistant" if mtype in
                                               ("gemini", "model", "assistant") else None)
        if role is None:
            return
        yield (uid, role, "text", ts, content[:TEXT_CAP], "", None)

    mutation = entry.get("$set")
    if isinstance(mutation, dict) and isinstance(mutation.get("messages"), list):
        for m in mutation["messages"]:
            yield from one(m)
    elif entry.get("type"):
        yield from one(entry)


EXTRACT = {"claude-code": _extract_claude, "codex": _extract_codex, "gemini": _extract_gemini}


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
