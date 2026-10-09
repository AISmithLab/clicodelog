"""VS Code / GitHub Copilot Chat session parser.

Copilot Chat has written two formats for the same session object:

`<id>.json` (one JSON document: {version: 3, sessionId, customTitle,
creationDate, requests: [...]}) and `<id>.jsonl` (a mutation log of edits to
that document). vscode_log.py turns either into the session object.

Each request is one user turn plus the assistant's streamed response parts:
markdown, thinking, tool invocations, file edits. Tool calls are taken from
result.metadata.toolCallRounds when present, which records exact arguments,
with outputs from result.metadata.toolCallResults.
"""

import json
import re
from pathlib import Path

from ..editors import uri_to_path, workspace_folder
from .vscode_log import SHAPE_ERRORS, iso_ms, load_session


# ------------------------------------------------------------------ helpers
def _md(value) -> str:
    """A markdown part's text: a bare string or a {value: str} object."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        v = value.get("value")
        return v if isinstance(v, str) else ""
    return ""


def _node_text(node) -> str:
    """Flatten a prompt-tsx result tree (or any nested value) to its text."""
    out: list = []

    def walk(n):
        if isinstance(n, str):
            out.append(n)
        elif isinstance(n, list):
            for x in n:
                walk(x)
        elif isinstance(n, dict):
            if isinstance(n.get("text"), str):
                out.append(n["text"])
            elif isinstance(n.get("value"), str):
                out.append(n["value"])
            for key in ("value", "node", "children", "content"):
                if isinstance(n.get(key), (dict, list)):
                    walk(n[key])
    walk(node)
    return "".join(out).strip()


def _args(raw):
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except ValueError:
            return raw
    return raw


def _tools_from_rounds(meta: dict) -> list:
    results = meta.get("toolCallResults") if isinstance(meta.get("toolCallResults"), dict) else {}
    tools = []
    for rnd in meta.get("toolCallRounds") or []:
        for call in (rnd.get("toolCalls") if isinstance(rnd, dict) else None) or []:
            if not isinstance(call, dict):
                continue
            res = results.get(call.get("id"))
            tools.append({"name": call.get("name") or "tool",
                          "input": _args(call.get("arguments")),
                          "result": _node_text(res) if res is not None else None})
    return tools


def _tool_from_part(part: dict) -> dict:
    data = part.get("toolSpecificData") if isinstance(part.get("toolSpecificData"), dict) else {}
    details = part.get("resultDetails")
    inp, result = None, None
    if data.get("kind") == "terminal":
        cmd = data.get("commandLine")
        inp = {"command": cmd.get("original") if isinstance(cmd, dict) else cmd}
    if isinstance(details, dict):
        if inp is None and details.get("input") is not None:
            inp = _args(details.get("input"))
        if details.get("output") is not None:
            result = _node_text(details.get("output"))
    elif isinstance(details, list):
        result = "\n".join(uri_to_path(d.get("uri", d)) for d in details if isinstance(d, dict))
    if inp is None:
        inp = _md(part.get("invocationMessage"))
    if not result:
        result = _md(part.get("pastTenseMessage")) or None
    return {"name": part.get("toolId") or data.get("kind") or "tool", "input": inp,
            "result": result}


def _usage(result: dict) -> dict | None:
    u = result.get("usage") if isinstance(result.get("usage"), dict) else {}
    meta = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
    inp = u.get("promptTokens") or meta.get("promptTokens") or 0
    out = u.get("completionTokens") or meta.get("outputTokens") or 0
    if not (inp or out):
        return None
    return {"input_tokens": inp, "output_tokens": out,
            "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}


def _model(req: dict, result: dict) -> str | None:
    """The stable id ("copilot/claude-sonnet-4.5" -> "claude-sonnet-4.5"), so
    analytics group one model under one name. Only when the user let Copilot
    pick ("auto") is the display name, which says what actually ran, better."""
    mid = req.get("modelId")
    mid = mid.split("/", 1)[-1] if isinstance(mid, str) and mid else None
    if mid and mid != "auto":
        return mid
    details = result.get("details")
    if isinstance(details, str) and details.strip():
        return details.split(" • ")[0].strip()       # "Claude Sonnet 4.5 • 1x"
    return mid


_EMPTY_FENCE = re.compile(r"\n?```[\w+-]*\n\s*```\n?")


def _ref_name(part: dict) -> str:
    """An inline reference is a symbol {name, location} or a bare file URI."""
    if isinstance(part.get("name"), str) and part["name"]:
        return part["name"]
    ref = part.get("inlineReference")
    if isinstance(ref, dict):
        if isinstance(ref.get("name"), str) and ref["name"]:
            return ref["name"]
        loc = ref.get("location")
        target = ref.get("uri") or (loc.get("uri") if isinstance(loc, dict) else None) or ref
        path = uri_to_path(target)
        if path:
            return re.split(r"[\\/]", path.rstrip("\\/"))[-1]
    return "reference"


# ------------------------------------------------------------------ messages
def _assistant(req: dict) -> dict | None:
    content, thinking, part_tools, edited = [], [], [], []
    for part in req.get("response") or []:
        if not isinstance(part, dict):
            continue
        kind = part.get("kind")
        if kind in (None, "markdownContent", "markdownVuln"):
            content.append(_md(part.get("value") if kind is None else part.get("content")))
        elif kind == "inlineReference":
            content.append(f"`{_ref_name(part)}`")
        elif kind == "thinking":
            text = _md(part.get("value")) if not isinstance(part.get("value"), list) \
                else "\n".join(_md(v) for v in part["value"])
            if text.strip():
                thinking.append(text.strip())
        elif kind == "toolInvocationSerialized":
            part_tools.append(_tool_from_part(part))
        elif kind in ("textEditGroup", "notebookEditGroup"):
            p = uri_to_path(part.get("uri"))
            if p:
                edited.append(p)
        elif kind == "warning":
            content.append(f"\n\n> **Warning:** {_md(part.get('content'))}\n\n")

    result = req.get("result") if isinstance(req.get("result"), dict) else {}
    meta = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
    tools = _tools_from_rounds(meta) or part_tools
    for ev in req.get("editedFileEvents") or []:
        if isinstance(ev, dict):
            p = uri_to_path(ev.get("uri"))
            if p:
                edited.append(p)
    err = result.get("errorDetails")
    if isinstance(err, dict) and err.get("message"):
        content.append(f"\n\n> **Error:** {err['message']}")

    # Edits render as a fenced placeholder around a code-block URI; with the URI
    # part gone it is an empty ``` ``` pair, so drop those.
    text = _EMPTY_FENCE.sub("\n", "".join(content)).strip()
    if not (text or thinking or tools):
        return None
    state = req.get("modelState") if isinstance(req.get("modelState"), dict) else {}
    return {
        "role": "assistant",
        "content": text,
        "thinking": "\n\n".join(thinking) or None,
        "tool_uses": tools or None,
        "timestamp": iso_ms(state.get("completedAt")) or iso_ms(req.get("timestamp")),
        "model": _model(req, result),
        "usage": _usage(result),
        "uuid": req.get("responseId"),
        "edited_files": list(dict.fromkeys(edited)) or None,
    }


def _user(req: dict) -> dict | None:
    msg = req.get("message")
    text = ""
    if isinstance(msg, dict):
        text = msg.get("text") if isinstance(msg.get("text"), str) else ""
        if not text:
            text = "".join(p.get("text", "") for p in msg.get("parts") or []
                           if isinstance(p, dict) and isinstance(p.get("text"), str))
    elif isinstance(msg, str):
        text = msg
    if not text.strip():
        return None
    return {"role": "user", "content": text.strip(), "timestamp": iso_ms(req.get("timestamp")),
            "uuid": req.get("requestId")}


def session_cwd(path: Path) -> str:
    """The workspace folder: workspace.json sits beside chatSessions/."""
    return workspace_folder(path.parent.parent)


def parse_vscode_conversation(session_file: Path, session_id: str) -> dict:
    session = load_session(session_file)
    messages = []
    for req in session.get("requests") or []:
        if not isinstance(req, dict):
            continue
        for build in (_user, _assistant):
            try:
                m = build(req)
            except SHAPE_ERRORS:
                continue
            if m:
                messages.append(m)
    title = session.get("customTitle")
    return {
        "summaries": [title] if isinstance(title, str) and title else [],
        "messages": messages,
        "session_id": session.get("sessionId") or session_id,
        "meta": {
            "cwd": session_cwd(session_file),
            "startTime": iso_ms(session.get("creationDate")),
            "lastUpdated": iso_ms(session.get("lastMessageDate")),
            "responder": session.get("responderUsername"),
        },
    }
