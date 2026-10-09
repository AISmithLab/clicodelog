"""Cursor parser, for both shapes the backup holds (see sync_cursor.py).

* `composers/<ws>/<id>.jsonl` — exported from Cursor's SQLite store: a header,
  the composer record, then one bubble per message. Bubble `type` 1 is the user,
  2 the assistant; an assistant bubble carries text, `thinking`, or one tool
  call in `toolFormerData` (whose params/result are JSON strings). Older builds
  kept the bubbles inline as composer.conversation; the oldest kept "aichat"
  tabs whose bubbles say "user"/"ai".
* `cli/<ws>/<id>.jsonl` — the cursor-agent CLI's chats; see cursor_cli.py.
* `transcripts/<slug>/.../<id>.jsonl` — Cursor's own agent transcripts:
  {role, message: {content: [blocks]}} per line, plus {type: "turn_ended"}.
  They record no timestamps except a `<timestamp>` tag Cursor writes into each
  user prompt, which wraps the actual text in `<user_query>`.

Cursor agents emit many small bubbles per reply (text, a tool call, more text).
Tool-only and thinking-only bubbles are folded into the assistant message before
them, as the Codex parser does, so a reply reads as one turn.
"""

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .vscode_log import SHAPE_ERRORS, iso_ms

# Tool names whose params name a file the agent changed, across Cursor versions.
EDIT_TOOLS = {"edit_file", "edit_file_v2", "search_replace", "write", "Write", "MultiEdit",
              "apply_patch", "ApplyPatch", "delete_file", "Delete", "StrReplace", "Edit"}
_PATH_KEYS = ("target_file", "file_path", "filePath", "relativeWorkspacePath", "path")


def _json(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def _ts(value) -> str | None:
    if isinstance(value, (int, float)):
        return iso_ms(value)
    if isinstance(value, str) and value:
        return value
    return None


def edited_path(name, args) -> str | None:
    if name not in EDIT_TOOLS or not isinstance(args, dict):
        return None
    for key in _PATH_KEYS:
        if isinstance(args.get(key), str) and args[key]:
            return args[key]
    return None


def _append(messages: list, msg: dict) -> None:
    """Fold a content-less assistant bubble into the assistant turn before it."""
    prev = messages[-1] if messages else None
    if (prev and prev["role"] == "assistant" and msg["role"] == "assistant"
            and not msg.get("content")):
        if msg.get("thinking"):
            prev["thinking"] = "\n\n".join(x for x in (prev.get("thinking"), msg["thinking"]) if x)
        if msg.get("tool_uses"):
            prev["tool_uses"] = (prev.get("tool_uses") or []) + msg["tool_uses"]
        if msg.get("edited_files"):
            prev["edited_files"] = (prev.get("edited_files") or []) + msg["edited_files"]
        if msg.get("usage"):
            # Every counter either side has — iterating only the new bubble's
            # keys dropped the earlier turn's input and cache tokens.
            u, v = prev.get("usage") or {}, msg["usage"]
            prev["usage"] = {k: (u.get(k) or 0) + (v.get(k) or 0) for k in {*u, *v}}
        return
    messages.append(msg)


# ------------------------------------------------------------------ composer
def _bubble(b: dict, default_model) -> dict | None:
    btype = b.get("type")
    role = "user" if btype in (1, "user") else "assistant" if btype in (2, "ai") else None
    if role is None:
        return None
    text = next((t for t in (b.get("text"), b.get("rawText")) if isinstance(t, str) and t), "")
    if not text and role == "assistant":
        blocks = [c.get("content") for c in b.get("codeBlocks") or []
                  if isinstance(c, dict) and isinstance(c.get("content"), str)]
        text = "\n\n".join(f"```\n{c}\n```" for c in blocks)
    timing = b.get("timingInfo") if isinstance(b.get("timingInfo"), dict) else {}
    ts = _ts(b.get("createdAt")) or _ts(timing.get("clientStartTime"))
    msg = {"role": role, "content": (text or "").strip(), "timestamp": ts,
           "uuid": b.get("bubbleId") or b.get("id")}
    if role == "user":
        return msg if msg["content"] else None

    thinking = b.get("thinking")
    parts = [thinking.get("text")] if isinstance(thinking, dict) else []
    parts += [t.get("thinking") or t.get("text") for t in b.get("allThinkingBlocks") or []
              if isinstance(t, dict)]
    thinking_text = "\n\n".join(p for p in parts if isinstance(p, str) and p.strip())

    tools, edited = [], []
    tf = b.get("toolFormerData")
    if isinstance(tf, dict) and (tf.get("name") or tf.get("tool") is not None):
        name = tf.get("name") or f"tool-{tf.get('tool')}"
        args = _json(tf.get("params") or tf.get("rawArgs"))
        result = _json(tf.get("result"))
        if result is not None and not isinstance(result, str):
            result = json.dumps(result, indent=2, ensure_ascii=False)
        tools.append({"name": name, "input": args, "result": result})
        p = edited_path(name, args)
        if p:
            edited.append(p)

    tc = b.get("tokenCount") if isinstance(b.get("tokenCount"), dict) else {}
    usage = None
    if tc.get("inputTokens") or tc.get("outputTokens"):
        usage = {"input_tokens": tc.get("inputTokens") or 0,
                 "output_tokens": tc.get("outputTokens") or 0,
                 "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
    model = (b.get("modelInfo") or {}).get("modelName") if isinstance(b.get("modelInfo"), dict) else None
    if not (msg["content"] or thinking_text or tools):
        return None
    msg.update({"thinking": thinking_text or None, "tool_uses": tools or None,
                "model": model or b.get("modelType") or default_model, "usage": usage,
                "edited_files": edited or None})
    return msg


def _parse_export(records: list) -> dict:
    header = records[0]
    composer, bubbles, tab = {}, [], None
    for r in records[1:]:
        if r.get("type") == "composer" and isinstance(r.get("data"), dict):
            composer = r["data"]
        elif r.get("type") == "bubble" and isinstance(r.get("data"), dict) and not r.get("orphan"):
            bubbles.append(r["data"])
        elif r.get("type") == "aichat-tab" and isinstance(r.get("data"), dict):
            tab = r["data"]
    if tab is not None:
        bubbles, title = tab.get("bubbles") or [], tab.get("chatTitle")
        created, updated = None, tab.get("lastSendTime")
    else:
        if not bubbles:
            bubbles = [b for b in composer.get("conversation") or [] if isinstance(b, dict)]
        title = composer.get("name")
        created, updated = composer.get("createdAt"), composer.get("lastUpdatedAt")
    model = (composer.get("modelConfig") or {}).get("modelName") \
        if isinstance(composer.get("modelConfig"), dict) else None

    messages: list = []
    for b in bubbles:
        try:
            m = _bubble(b, model) if isinstance(b, dict) else None
        except SHAPE_ERRORS:
            continue
        if m:
            _append(messages, m)
    _fill_times(messages, _ts(created))
    return {"summaries": [title] if isinstance(title, str) and title else [],
            "messages": messages,
            "meta": {"cwd": header.get("workspace") or "", "startTime": _ts(created),
                     "lastUpdated": _ts(updated), "mode": composer.get("unifiedMode")}}


def _fill_times(messages: list, start) -> None:
    """Give untimed messages the time of the last timed one before them."""
    last = start
    for m in messages:
        if m.get("timestamp"):
            last = m["timestamp"]
        elif last:
            m["timestamp"] = last


# ------------------------------------------------------------------ transcripts
_QUERY = re.compile(r"<user_query>\s*(.*?)\s*</user_query>", re.S)
_STAMP = re.compile(r"<timestamp>\s*(.*?)\s*</timestamp>", re.S)
_STAMP_FMT = re.compile(r"(\w{3})\w*\.? (\d{1,2}), (\d{4}),? (\d{1,2}):(\d{2})\s*([AP]M)?"
                        r"(?:\s*\(UTC([+-]\d{1,2})(?::?(\d{2}))?\))?", re.I)
_MONTHS = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}


def parse_prompt_time(text: str) -> str | None:
    """'Tuesday, Jun 2, 2026, 11:20 AM (UTC+8)' -> '2026-06-02T03:20:00Z'."""
    m = _STAMP_FMT.search(text or "")
    if not m or m.group(1).lower() not in _MONTHS:
        return None
    hour, minute = int(m.group(4)), int(m.group(5))
    if m.group(6):
        hour = hour % 12 + (12 if m.group(6).upper() == "PM" else 0)
    sign = -1 if (m.group(7) or "+0").startswith("-") else 1
    offset = timedelta(hours=abs(int(m.group(7) or 0)), minutes=int(m.group(8) or 0)) * sign
    try:
        local = datetime(int(m.group(3)), _MONTHS[m.group(1).lower()], int(m.group(2)),
                         hour, minute, tzinfo=timezone(offset))
    except ValueError:
        return None
    return local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _blocks(content) -> list:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return [b for b in content or [] if isinstance(b, dict)] if isinstance(content, list) else []


def _transcript_line(entry: dict, state: dict) -> dict | None:
    msg = entry.get("message") if isinstance(entry.get("message"), dict) else {}
    role = entry.get("role") or msg.get("role") or entry.get("type")
    if role not in ("user", "assistant"):
        return None
    texts, thinking, tools, edited, results = [], [], [], [], {}
    for b in _blocks(msg.get("content", entry.get("content"))):
        btype = b.get("type")
        if btype == "text" and isinstance(b.get("text"), str):
            texts.append(b["text"])
        elif btype == "thinking" and isinstance(b.get("thinking"), str):
            thinking.append(b["thinking"])
        elif btype == "tool_use":
            args = _json(b.get("input"))
            tools.append({"name": b.get("name") or "tool", "input": args, "result": None,
                          "id": b.get("id")})
            p = edited_path(b.get("name"), args)
            if p:
                edited.append(p)
        elif btype == "tool_result":
            c = b.get("content")
            if isinstance(c, list):
                c = "\n".join(x.get("text", "") for x in c if isinstance(x, dict))
            results[b.get("tool_use_id")] = c if isinstance(c, str) else json.dumps(c)
    text = "\n".join(texts).strip()
    ts = _ts(entry.get("timestamp"))
    if role == "user":
        stamp = _STAMP.search(text)
        if stamp:
            ts = ts or parse_prompt_time(stamp.group(1))
        query = _QUERY.search(text)
        if query:
            text = query.group(1).strip()
        elif stamp:
            text = _STAMP.sub("", text).strip()
        if results:                      # Anthropic-shaped tool results ride on user turns
            state["results"].update(results)
            if not text:
                return None
    if ts:
        state["ts"] = ts
    usage = msg.get("usage") if isinstance(msg.get("usage"), dict) else None
    out = {"role": role, "content": text, "timestamp": ts or state["ts"],
           "uuid": entry.get("uuid") or msg.get("id")}
    if role == "assistant":
        if not (text or thinking or tools):
            return None
        out.update({"thinking": "\n\n".join(thinking) or None, "tool_uses": tools or None,
                    "model": msg.get("model"), "usage": usage, "edited_files": edited or None})
    return out if (out["content"] or role == "assistant") else None


def _parse_transcript(records: list) -> dict:
    state = {"ts": None, "results": {}}
    messages: list = []
    for entry in records:
        try:
            m = _transcript_line(entry, state)
        except SHAPE_ERRORS:
            continue
        if m:
            _append(messages, m)
    for m in messages:
        for t in m.get("tool_uses") or []:
            tid = t.pop("id", None)
            if tid in state["results"]:
                t["result"] = state["results"][tid]
    return {"summaries": [], "messages": messages, "meta": {"cwd": ""}}


# ------------------------------------------------------------------ entry
def read_records(path: Path) -> list:
    out = []
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue                       # torn line from a live session
            if isinstance(rec, dict):
                out.append(rec)
    return out


def parse_cursor_conversation(session_file: Path, session_id: str) -> dict:
    records = read_records(session_file)
    if records and records[0].get("type") == "cursor-backup" and records[0].get("kind") == "cli":
        from .cursor_cli import parse_cli
        conv = parse_cli(records)
    elif records and records[0].get("type") == "cursor-backup":
        conv = _parse_export(records)
    else:
        conv = _parse_transcript(records)
    # Transcripts carry no message ids; search hits jump to a message by id.
    for i, m in enumerate(conv["messages"]):
        m["uuid"] = m.get("uuid") or f"msg-{i}"
    conv["session_id"] = session_id
    return conv
