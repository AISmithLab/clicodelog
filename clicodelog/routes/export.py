import json
from typing import Optional

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse, Response

from .. import sync as _sync
from ..conversation import find_session_path, get_conversation
from ..utils import is_safe_id

router = APIRouter()


def _total_tokens(usage: dict) -> int:
    """All four fields. Summing only input+output under-reported real usage by
    ~116x, because prompt caching moves nearly all input into cache reads."""
    if not isinstance(usage, dict):
        return 0
    return sum(usage.get(k) or 0 for k in (
        "input_tokens", "output_tokens",
        "cache_read_input_tokens", "cache_creation_input_tokens"))


def _project_label(project_id: str, source_id: str, conv: dict) -> str:
    """A readable project name per source.

    The old code applied Claude's dash-decoding to every source, so Codex (whose
    id is base64) and Gemini exported a garbled 'Project:' line.
    """
    meta = conv.get("meta") or {}
    if meta.get("cwd"):
        return meta["cwd"]
    if source_id == "codex":
        try:
            from ..utils import decode_path_id
            return decode_path_id(project_id)
        except Exception:
            return project_id
    if source_id == "gemini":
        return project_id
    from ..search_index import sessions_for_project
    rows = sessions_for_project(source_id, project_id, limit=1)
    if rows:
        return rows[0].get("cwd") or rows[0].get("project_name") or project_id
    return project_id


@router.get("/api/projects/{project_id}/sessions/{session_id}/export-raw")
def api_export_raw(project_id: str, session_id: str, source: Optional[str] = None):
    """Download the exact source file verbatim — the complete, nothing-omitted
    record (tool inputs, tool outputs, thinking, usage, uuids, everything)."""
    if not (is_safe_id(project_id) and is_safe_id(session_id)):
        return JSONResponse({"error": "Invalid id"}, status_code=400)
    source_id = source or _sync.current_source
    path = find_session_path(project_id, session_id, source_id)
    if not path or not path.exists():
        return JSONResponse({"error": "Session not found"}, status_code=404)
    # FileResponse streams via sendfile at constant memory; read_bytes() pulled
    # the whole file (up to 966 MB here) into RAM first.
    return FileResponse(
        path,
        media_type="application/x-ndjson",
        filename=f"{session_id}.raw{path.suffix}",
    )


@router.get("/api/projects/{project_id}/sessions/{session_id}/export")
def api_export(project_id: str, session_id: str, source: Optional[str] = None,
               fmt: str = "txt"):
    if not (is_safe_id(project_id) and is_safe_id(session_id)):
        return JSONResponse({"error": "Invalid id"}, status_code=400)
    source_id = source or _sync.current_source
    conv = get_conversation(project_id, session_id, source_id, include_results=True)

    if "error" in conv:
        return JSONResponse(conv, status_code=404)

    label = _project_label(project_id, source_id, conv)
    build = _build_markdown if fmt == "md" else _build_text
    body, media, ext = build(session_id, label, conv)
    return Response(
        content=body,
        media_type=media,
        headers={"Content-Disposition": f"attachment; filename={session_id}.{ext}"},
    )


def _build_text(session_id: str, label: str, conv: dict):
    lines = ["=" * 60, f"Session: {session_id}", f"Project: {label}", "=" * 60, ""]

    if conv.get("summaries"):
        lines.append("SUMMARIES:")
        for s in conv["summaries"]:
            lines.append(f"  - {s}")
        lines += ["", "-" * 60, ""]

    for msg in conv.get("messages", []):
        lines.append(f"[{msg['role'].upper()}] {msg.get('timestamp', '')}")
        if msg.get("model"):
            lines.append(f"Model: {msg['model']}")
        lines.append("-" * 40)
        if msg.get("content"):
            lines.append(msg["content"])
        if msg.get("thinking"):
            lines += ["", "--- THINKING ---", msg["thinking"], "--- END THINKING ---"]
        for tool in msg.get("tool_uses") or []:
            lines.append("")
            lines.append(f"[TOOL: {tool['name']}]")
            inp = tool.get("input")
            lines.append("INPUT:")
            lines.append(json.dumps(inp, indent=2, ensure_ascii=False)
                         if isinstance(inp, (dict, list))
                         else str(inp if inp is not None else ""))
            if tool.get("result"):
                lines.append("OUTPUT:")
                lines.append(str(tool["result"]))
        if msg.get("usage"):
            lines.append(f"\n[Tokens: {_total_tokens(msg['usage'])}]")
        lines += ["", "=" * 60, ""]

    return "\n".join(lines), "text/plain", "txt"


def _build_markdown(session_id: str, label: str, conv: dict):
    """Markdown export — the format people actually paste into issues and PRs.

    Message content is already markdown, so this is mostly headings plus
    <details> blocks for thinking and tool calls.
    """
    out = [f"# Session `{session_id}`", "", f"**Project:** `{label}`", ""]

    if conv.get("summaries"):
        out.append("## Summary")
        for s in conv["summaries"]:
            out.append(f"- {s}")
        out.append("")

    total = 0
    for msg in conv.get("messages", []):
        role = msg["role"]
        heading = "User" if role == "user" else "Assistant"
        stamp = msg.get("timestamp") or ""
        model = f" · `{msg['model']}`" if msg.get("model") else ""
        out += ["---", "", f"### {heading}{model}", ""]
        if stamp:
            out += [f"<sub>{stamp}</sub>", ""]

        if msg.get("content"):
            out += [msg["content"], ""]

        if msg.get("thinking"):
            out += ["<details><summary>Thinking</summary>", "",
                    msg["thinking"], "", "</details>", ""]

        for tool in msg.get("tool_uses") or []:
            out.append(f"<details><summary>Tool: <code>{tool['name']}</code></summary>")
            out.append("")
            inp = tool.get("input")
            out += ["**Input**", "", "```json",
                    json.dumps(inp, indent=2, ensure_ascii=False)
                    if isinstance(inp, (dict, list)) else str(inp), "```", ""]
            if tool.get("result"):
                out += ["**Output**", "", "```", str(tool["result"])[:200_000], "```", ""]
            out += ["</details>", ""]

        total += _total_tokens(msg.get("usage") or {})

    if total:
        out += ["---", "", f"*Total tokens (including cache reads): {total:,}*", ""]
    return "\n".join(out), "text/markdown", "md"
