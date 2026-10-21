"""GitHub Copilot CLI's persisted session event stream.

Schema: github/copilot-sdk, nodejs/src/generated/session-events.ts.
Streaming deltas are ignored in favour of the persisted complete messages.
Tool requests and execution events name the same call and must be shown once.
"""

import json
from pathlib import Path

from .agent_common import arguments, message, number, obj, output, records, text


def _tool(data, state, owner):
    call_id = text(data.get("toolCallId"))
    if not call_id:
        return
    if call_id in state["calls"]:
        return state["calls"][call_id][0]
    tool = {"name": text(data.get("name") or data.get("toolName")) or "tool",
            "input": arguments(data.get("arguments")), "result": None}
    owner["tool_uses"] = (owner.get("tool_uses") or []) + [tool]
    state["calls"][call_id] = (tool, owner)
    if tool["name"] in ("edit", "create", "str_replace_editor", "apply_patch"):
        args = obj(tool["input"])
        path = text(args.get("path") or args.get("file_path"))
        if path:
            owner["edited_files"] = list(dict.fromkeys((owner["edited_files"] or []) + [path]))
    return tool


def _owner(event, state, messages):
    agent = text(event.get("agentId"))
    owner = state["owners"].get(agent)
    if owner is None:
        owner = message("assistant", "", event.get("timestamp"), event.get("id"), state["model"])
        messages.append(owner)
        state["owners"][agent] = owner
    return owner


def _event(event, state, messages, meta, summaries):
    kind, data = event.get("type"), obj(event.get("data"))
    stamp, agent = event.get("timestamp"), text(event.get("agentId"))
    if isinstance(stamp, str):
        meta["lastUpdated"] = stamp
    if kind in ("session.start", "session.resume", "session.context_changed"):
        context = obj(data.get("context"))
        meta["cwd"] = text(context.get("cwd")) or meta["cwd"]
        if kind == "session.start":
            meta["startTime"] = text(data.get("startTime")) or text(stamp)
            state["model"] = text(data.get("selectedModel") or data.get("model"))
    elif kind == "session.title_changed":
        title = text(data.get("title"))
        if title:
            summaries[:] = [title]
    elif kind == "session.model_change":
        state["model"] = text(data.get("newModel") or data.get("model"))
    elif kind in ("assistant.turn_start", "assistant.turn_end"):
        state["owners"].pop(agent, None)
    elif kind == "user.message":
        content = text(data.get("content"))
        if content:
            messages.append(message("user", content, stamp, event.get("id")))
        state["owners"].pop(agent, None)
    elif kind == "assistant.reasoning":
        previous = state["owners"].get(agent)
        if previous and (previous["content"] or previous["tool_uses"]):
            state["owners"].pop(agent)
        owner = _owner(event, state, messages)
        owner["thinking"] = text(data.get("content")) or None
    elif kind == "assistant.message":
        # Reasoning immediately preceding a message belongs to that response.
        owner = state["owners"].get(agent)
        if owner is None or owner["content"] or owner["tool_uses"]:
            owner = message("assistant", "", stamp, event.get("id"))
            messages.append(owner)
        state["owners"][agent] = owner
        owner.update(content=text(data.get("content")),
                     uuid=text(data.get("messageId") or event.get("id")) or None,
                     model=text(data.get("model")) or state["model"] or None)
        owner["thinking"] = text(data.get("reasoningText")) or owner["thinking"]
        if data.get("outputTokens") is not None:
            owner["usage"] = {"output_tokens": number(data["outputTokens"])}
        for request in records(data.get("toolRequests")):
            _tool(request, state, owner)
    elif kind == "tool.execution_start":
        _tool(data, state, _owner(event, state, messages))
    elif kind == "tool.execution_complete":
        call = state["calls"].get(text(data.get("toolCallId")))
        if call:
            tool, owner = call
            result = data.get("result")
            tool["result"] = output(obj(result).get("detailedContent", obj(result).get("content", result)))
            if data.get("error"):
                tool["result"] = output(data["error"])
            for edit in records(data.get("fileEdits")):
                path = text(edit.get("path"))
                if path:
                    owner["edited_files"] = list(dict.fromkeys((owner["edited_files"] or []) + [path]))
    elif kind == "assistant.usage":
        owner = _owner(event, state, messages)
        owner["model"] = text(data.get("model")) or owner["model"]
        owner["usage"] = {"input_tokens": number(data.get("inputTokens")),
                          "output_tokens": number(data.get("outputTokens")),
                          "cache_read_input_tokens": number(data.get("cacheReadTokens")),
                          "cache_creation_input_tokens": number(data.get("cacheWriteTokens"))}


def parse_copilot_conversation(path: Path, session_id: str) -> dict:
    messages, summaries = [], []
    meta = {"cwd": "", "startTime": None, "lastUpdated": None}
    state = {"model": "", "calls": {}, "owners": {}}
    seen = set()
    with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
        for line in fh:
            try:
                event = json.loads(line)
            except ValueError:
                continue                         # a torn live-write tail
            if not isinstance(event, dict):
                continue
            uid = text(event.get("id"))
            if uid and uid in seen:
                continue
            if uid:
                seen.add(uid)
            _event(event, state, messages, meta, summaries)
    messages = [m for m in messages if m["content"] or m["thinking"] or m["tool_uses"] or m["usage"]]
    return {"session_id": session_id, "summaries": summaries, "messages": messages, "meta": meta}
