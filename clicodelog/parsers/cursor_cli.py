"""Parser for cursor-agent CLI chats, as exported by sync_cursor_cli.py.

The JSON rows are AI-SDK-shaped messages:

    {"role": "system", "content": "..."}                       not shown
    {"role": "user", "content": [{"type": "text", "text": "<timestamp>...</timestamp>
                                   <user_query>the prompt</user_query>"}]}
    {"role": "assistant", "content": [{"type": "reasoning", "text": ...},
                                      {"type": "text", "text": ...},
                                      {"type": "tool-call", "toolCallId", "toolName",
                                       "args" | "input"}]}
    {"role": "tool", "content": [{"type": "tool-result", "toolCallId",
                                  "result" | "output"}]}

A user message is a typed prompt only if it carries `<user_query>`; the rest
(the environment preamble, attached skills, workspace context) is injected by
Cursor and is not a turn. Only prompts carry a time, so the messages after one
take its time.
"""

import json

from .cursor import (_QUERY, _STAMP, _append, _fill_times, _json, _ts, edited_path,
                     parse_prompt_time)
from .vscode_log import SHAPE_ERRORS


def _parts(content) -> list:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return [p for p in content if isinstance(p, dict)] if isinstance(content, list) else []


def _result_text(part: dict):
    value = part.get("result", part.get("output"))
    # AI SDK v5 wraps outputs: {"type": "text" | "json" | "error-text", "value": ...}
    if isinstance(value, dict) and "value" in value and "type" in value:
        value = value["value"]
    if value is None or isinstance(value, str):
        return value
    return json.dumps(value, indent=2, ensure_ascii=False)


def _user(parts: list, state: dict) -> dict | None:
    text = "\n".join(p.get("text") or "" for p in parts if p.get("type") == "text")
    stamp = _STAMP.search(text)
    if stamp:
        state["ts"] = parse_prompt_time(stamp.group(1)) or state["ts"]
    query = _QUERY.search(text)
    if not query:
        return None
    return {"role": "user", "content": query.group(1).strip(), "timestamp": state["ts"],
            "uuid": None}


def _assistant(msg: dict, parts: list, state: dict, model) -> dict | None:
    texts, thinking, tools, edited = [], [], [], []
    for p in parts:
        ptype = p.get("type")
        if ptype == "text" and isinstance(p.get("text"), str):
            texts.append(p["text"])
        elif ptype == "reasoning" and isinstance(p.get("text"), str) and p["text"].strip():
            thinking.append(p["text"])
        elif ptype == "tool-call":
            args = _json(p.get("args", p.get("input")))
            tool = {"name": p.get("toolName") or "tool", "input": args, "result": None}
            state["calls"][p.get("toolCallId")] = tool
            tools.append(tool)
            path = edited_path(tool["name"], args)
            if path:
                edited.append(path)
    text = "\n".join(texts).strip()
    if not (text or thinking or tools):
        return None
    return {"role": "assistant", "content": text, "thinking": "\n\n".join(thinking) or None,
            "tool_uses": tools or None, "timestamp": state["ts"], "model": model,
            "usage": None, "uuid": msg.get("id"), "edited_files": edited or None}


def _message(msg: dict, state: dict, model) -> dict | None:
    role, parts = msg.get("role"), _parts(msg.get("content"))
    if role == "user":
        return _user(parts, state)
    if role == "assistant":
        return _assistant(msg, parts, state, model)
    if role == "tool":
        for p in parts:
            tool = state["calls"].get(p.get("toolCallId"))
            if p.get("type") == "tool-result" and tool is not None:
                tool["result"] = _result_text(p)
    return None                                       # system prompt, tool results


def parse_cli(records: list) -> dict:
    header = records[0]
    meta = header.get("meta") if isinstance(header.get("meta"), dict) else {}
    store_meta = header.get("storeMeta") if isinstance(header.get("storeMeta"), dict) else {}
    info = next((v for v in store_meta.values() if isinstance(v, dict)), {})
    model = info.get("lastUsedModel") or info.get("model")
    state = {"ts": _ts(meta.get("createdAtMs")) or _ts(info.get("createdAt")), "calls": {}}
    start = state["ts"]

    messages: list = []
    for rec in records[1:]:
        msg = rec.get("json") if rec.get("type") == "cli-blob" else None
        if not isinstance(msg, dict):
            continue                                  # a binary tree node
        try:
            m = _message(msg, state, model)
        except SHAPE_ERRORS:
            continue
        if m:
            _append(messages, m)
    _fill_times(messages, start)
    title = meta.get("title") or info.get("name")
    return {"summaries": [title] if isinstance(title, str) and title else [],
            "messages": messages,
            "meta": {"cwd": header.get("workspace") or meta.get("cwd") or "",
                     "startTime": start, "lastUpdated": _ts(meta.get("updatedAtMs")),
                     "mode": info.get("mode")}}
