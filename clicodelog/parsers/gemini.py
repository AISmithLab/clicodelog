"""Google Gemini CLI session parser.

Gemini CLI writes JSON Lines, not a single JSON object: line one is a session
header, and the rest are either `{"$set": {...}}` mutations (where `messages`
replaces the list wholesale) or bare appended message records. The previous
parser called json.load() on the whole file, which raised on every current file
— one reason this source returned nothing at all.
"""

import json
from pathlib import Path


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if isinstance(block, dict):
            t = block.get("text")
            if isinstance(t, str):
                parts.append(t)
        elif isinstance(block, str):
            parts.append(block)
    return "\n".join(parts)


def _normalise(msg: dict) -> dict | None:
    if not isinstance(msg, dict):
        return None
    mtype = msg.get("type")
    timestamp = msg.get("timestamp")
    content = _text_of(msg.get("content"))

    if mtype == "user":
        return {"role": "user", "content": content, "timestamp": timestamp,
                "uuid": msg.get("id")}

    if mtype in ("gemini", "model", "assistant"):
        thinking_parts = []
        for thought in msg.get("thoughts") or []:
            if isinstance(thought, dict):
                subject = thought.get("subject", "")
                desc = thought.get("description", "")
                if subject or desc:
                    thinking_parts.append(f"**{subject}**: {desc}" if subject else desc)

        tool_uses = [
            {"name": tc.get("name", ""), "input": tc.get("args", {}), "result": None}
            for tc in msg.get("toolCalls") or []
            if isinstance(tc, dict)
        ]

        tokens = msg.get("tokens")
        # Normalise to the same shape the other sources use, so the frontend has
        # one code path for token display.
        usage = None
        if isinstance(tokens, dict):
            usage = {
                "input_tokens": tokens.get("input") or tokens.get("input_tokens") or 0,
                "output_tokens": tokens.get("output") or tokens.get("output_tokens") or 0,
                "cache_read_input_tokens": tokens.get("cached") or 0,
                "cache_creation_input_tokens": 0,
            }
        elif isinstance(tokens, int):
            usage = {"input_tokens": 0, "output_tokens": tokens,
                     "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}

        return {
            "role": "assistant",
            "content": content,
            "thinking": "\n".join(thinking_parts) if thinking_parts else None,
            "tool_uses": tool_uses or None,
            "timestamp": timestamp,
            "model": msg.get("model", "gemini"),
            "usage": usage,
            "uuid": msg.get("id"),
        }

    return None       # 'info' and friends are not conversation turns


def parse_gemini_conversation(session_file: Path, session_id: str) -> dict:
    header: dict = {}
    messages: list = []

    with open(session_file, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if not isinstance(entry, dict):
                continue

            if "sessionId" in entry and "projectHash" in entry:
                header.update(entry)
                continue

            mutation = entry.get("$set")
            if isinstance(mutation, dict):
                if isinstance(mutation.get("messages"), list):
                    messages = [m for m in (_normalise(x) for x in mutation["messages"]) if m]
                if mutation.get("lastUpdated"):
                    header["lastUpdated"] = mutation["lastUpdated"]
                continue

            if entry.get("type"):
                norm = _normalise(entry)
                if norm:
                    messages.append(norm)

    return {
        "summaries": [],
        "messages": messages,
        "session_id": session_id,
        "meta": {
            "sessionId": header.get("sessionId"),
            "projectHash": header.get("projectHash"),
            "startTime": header.get("startTime"),
            "lastUpdated": header.get("lastUpdated"),
        },
    }
