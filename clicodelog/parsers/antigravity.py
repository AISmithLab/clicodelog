"""Antigravity JSON trajectory snapshots, exported from its local history API."""

import json
from pathlib import Path

from ..editors import uri_to_path
from .agent_common import arguments, message, number, obj, output, records, text

# Internal checkpoints and state updates are not assistant utterances.
TOOLS = {"runCommand", "shellExec", "codeAction", "writeToFile", "viewFile", "viewCodeItem",
         "listDirectory", "grepSearch", "find", "searchWeb", "readUrlContent",
         "mcpTool", "commandStatus", "sendCommandInput", "browserSubagent",
         "openBrowserUrl", "executeBrowserJavaScript", "readBrowserPage",
         "captureBrowserScreenshot", "generateImage", "editNotebook", "readNotebook",
         "executeNotebook", "invokeSubagent", "toolSearch", "codeSearch", "generic"}


def _call(raw):
    return {"name": text(raw.get("name")) or "tool",
            "input": arguments(raw.get("argumentsJson", raw.get("arguments"))), "result": None}


def _edited(kind, payload):
    if kind not in ("codeAction", "writeToFile", "editNotebook"):
        return None
    spec = obj(payload.get("actionSpec"))
    path = payload.get("targetFileUri") or payload.get("absolutePath") or spec.get("fileUri")
    if not path:
        for action in ("createFile", "deleteFile", "sed"):
            part = obj(spec.get(action))
            path = part.get("fileUri") or part.get("path")
            if isinstance(path, dict):
                path = path.get("absoluteUri") or path.get("uri")
            if path:
                break
    path = uri_to_path(path)
    return [path] if path else None


def _step(step, index, calls, usage_seen):
    meta = obj(step.get("metadata"))
    usage = obj(meta.get("modelUsage"))
    model = text(obj(meta.get("modelInfo")).get("modelName")) or text(usage.get("model")) \
        or text(meta.get("generatorModel"))
    msg = message("assistant", "", meta.get("createdAt"),
                  step.get("id") or f"step-{index}", model)
    user = obj(step.get("userInput"))
    planner = obj(step.get("plannerResponse"))
    if user:
        msg["role"] = "user"
        msg["content"] = "\n".join(text(item.get("text")) for item in records(user.get("items"))).strip() \
            or text(user.get("userResponse")) or text(user.get("query"))
    elif planner:
        msg["content"] = text(planner.get("modifiedResponse")) or text(planner.get("response"))
        msg["thinking"] = text(planner.get("thinking")) or text(planner.get("rawThinking")) or None
        for raw in records(planner.get("toolCalls")):
            tool = _call(raw)
            msg["tool_uses"] = (msg["tool_uses"] or []) + [tool]
            if text(raw.get("id")):
                calls[raw["id"]] = (tool, msg)
    elif obj(step.get("notifyUser")):
        msg["content"] = text(step["notifyUser"].get("notificationContent"))
    else:
        kind = next((k for k in step if k in TOOLS and isinstance(step[k], dict)), None)
        if kind:
            payload = step[kind]
            raw = obj(meta.get("toolCall"))
            previous = calls.get(text(raw.get("id")))
            tool = previous[0] if previous else (_call(raw) if raw else
                    {"name": kind, "input": payload, "result": None})
            result = payload.get("combinedOutput", payload.get("output", payload.get("result")))
            if isinstance(result, dict):
                result = result.get("full", result.get("truncated", result))
            tool["result"] = output(result if result is not None else payload)
            owner = previous[1] if previous else msg
            if not previous:
                msg["tool_uses"] = [tool]
            edited = _edited(kind, payload)
            if edited:
                owner["edited_files"] = list(dict.fromkeys((owner["edited_files"] or []) + edited))
        elif obj(step.get("finish")):
            msg["content"] = text(step["finish"].get("message"))
        elif obj(step.get("errorMessage")):
            err = obj(step["errorMessage"].get("error"))
            msg["content"] = text(err.get("shortError")) or text(err.get("fullError"))
    # A usage record can be repeated on multiple steps from one model call.
    identity = text(usage.get("messageId") or usage.get("responseId")) or f"step-{index}"
    if usage and identity not in usage_seen:
        usage_seen.add(identity)
        msg["usage"] = {"input_tokens": number(usage.get("inputTokens")),
                        "output_tokens": number(usage.get("outputTokens")),
                        "cache_read_input_tokens": number(usage.get("cacheReadTokens")),
                        "cache_creation_input_tokens": number(usage.get("cacheWriteTokens"))}
    return msg


def _workspace(metadata):
    for workspace in records(metadata.get("workspaces")):
        uri = text(workspace.get("workspaceFolderAbsoluteUri"))
        if uri:
            return uri
    uris = metadata.get("workspaceUris")
    return next((u for u in uris if isinstance(u, str) and u), "") if isinstance(uris, list) else ""


def parse_antigravity_conversation(path: Path, session_id: str) -> dict:
    with open(path, encoding="utf-8-sig") as fh:
        root = obj(json.load(fh))
    trajectory = obj(root.get("trajectory")) or root
    summary = obj(root.get("summary"))
    meta = obj(trajectory.get("metadata"))
    cwd = text(root.get("cwd")) or _workspace(summary) or _workspace(meta)
    messages, calls, usage_seen = [], {}, set()
    for i, step in enumerate(records(trajectory.get("steps"))):
        if not cwd:
            cwd = text(obj(step.get("runCommand")).get("cwd"))
        msg = _step(step, i, calls, usage_seen)
        if msg["content"] or msg["thinking"] or msg["tool_uses"] or msg["usage"]:
            messages.append(msg)
    title = text(summary.get("summary")) or text(root.get("title"))
    return {"session_id": session_id, "summaries": [title] if title else [], "messages": messages,
            "meta": {"cwd": uri_to_path(cwd),
                     "startTime": text(summary.get("createdTime")) or text(meta.get("createdAt")) or None,
                     "lastUpdated": text(summary.get("lastModifiedTime")) or None}}
