import json
from pathlib import Path


def _stringify_result(content) -> str:
    """Flatten a tool_result 'content' (str, or list of blocks) to text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict):
                if b.get("type") == "text":
                    parts.append(b.get("text", ""))
                else:
                    parts.append(json.dumps(b, ensure_ascii=False))
            elif isinstance(b, str):
                parts.append(b)
        return "\n".join(parts)
    if content is None:
        return ""
    return json.dumps(content, ensure_ascii=False)


MESSAGE_TYPES = ("user", "assistant")


def build_message(entry: dict, tool_results: dict | None = None) -> dict | None:
    """Turn one JSONL entry into a message, or None if it isn't one.

    Exposed separately so a windowed read can build just the messages on the
    requested page instead of materialising the whole session.
    """
    entry_type = entry.get("type")

    if entry_type == "user":
        msg = entry.get("message") or {}
        content = msg.get("content", "")
        if isinstance(content, list):
            text_parts = []
            for block in content:
                if isinstance(block, dict):
                    bt = block.get("type")
                    if bt == "text":
                        text_parts.append(block.get("text", ""))
                    elif bt == "tool_result" and tool_results is not None:
                        # Capture the tool's output, keyed by id.
                        tid = block.get("tool_use_id")
                        res = _stringify_result(block.get("content"))
                        # Prefer the richer top-level toolUseResult when present.
                        tur = entry.get("toolUseResult")
                        if tur is not None:
                            res_full = (tur if isinstance(tur, str)
                                        else json.dumps(tur, indent=2, ensure_ascii=False))
                            if res_full and len(res_full) >= len(res):
                                res = res_full
                        if tid:
                            tool_results[tid] = res
                elif isinstance(block, str):
                    text_parts.append(block)
            content = "\n".join(text_parts)

        return {
            "role": "user",
            "content": content,
            "timestamp": entry.get("timestamp"),
            "uuid": entry.get("uuid"),
            "cwd": entry.get("cwd"),
            "gitBranch": entry.get("gitBranch"),
        }

    if entry_type == "assistant":
        msg = entry.get("message") or {}
        content_blocks = msg.get("content") or []

        text_content, thinking_content, tool_uses = [], [], []
        for block in content_blocks:
            if isinstance(block, dict):
                block_type = block.get("type")
                if block_type == "text":
                    text_content.append(block.get("text", ""))
                elif block_type == "thinking":
                    thinking_content.append(block.get("thinking", ""))
                elif block_type == "tool_use":
                    tool_uses.append({
                        "name": block.get("name", ""),
                        "input": block.get("input", {}),
                        "id": block.get("id"),
                    })

        return {
            "role": "assistant",
            "content": "\n".join(text_content),
            "thinking": "\n".join(thinking_content) if thinking_content else None,
            "tool_uses": tool_uses if tool_uses else None,
            "timestamp": entry.get("timestamp"),
            "uuid": entry.get("uuid"),
            "model": msg.get("model"),
            "usage": msg.get("usage"),
        }

    return None


def parse_claude_conversation(session_file: Path, session_id: str) -> dict:
    """Parse a whole Claude Code session, tool results included.

    This materialises everything and is used for exports. The interactive view
    goes through the windowed reader in conversation.py instead.
    """
    messages = []
    summaries = []
    # tool_use_id -> result text (results arrive in later user messages).
    tool_results: dict = {}

    with open(session_file, "r", errors="ignore") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue                     # torn line from a live session
            if not isinstance(entry, dict):
                continue

            if entry.get("type") == "summary":
                summaries.append(entry.get("summary", ""))
                continue
            try:
                m = build_message(entry, tool_results)
            except (AttributeError, TypeError):
                continue
            if m:
                messages.append(m)

    # Second pass: attach each tool's captured result to its tool_use.
    for m in messages:
        for tu in (m.get("tool_uses") or []):
            tid = tu.get("id")
            if tid and tid in tool_results:
                tu["result"] = tool_results[tid]

    return {"summaries": summaries, "messages": messages, "session_id": session_id}
