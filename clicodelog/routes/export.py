import json
from typing import Optional

from fastapi import APIRouter
from fastapi.responses import JSONResponse, Response

from .. import sync as _sync
from ..conversation import find_session_path, get_conversation

router = APIRouter()


@router.get("/api/projects/{project_id}/sessions/{session_id}/export-raw")
async def api_export_raw(project_id: str, session_id: str, source: Optional[str] = None):
    """Download the exact source file verbatim — the complete, nothing-omitted
    record (tool inputs, tool outputs, thinking, usage, uuids, everything)."""
    source_id = source or _sync.current_source
    path = find_session_path(project_id, session_id, source_id)
    if not path or not path.exists():
        return JSONResponse({"error": "Session not found"}, status_code=404)
    data = path.read_bytes()
    return Response(
        content=data,
        media_type="application/x-ndjson",
        headers={"Content-Disposition": f"attachment; filename={session_id}.raw{path.suffix}"},
    )


@router.get("/api/projects/{project_id}/sessions/{session_id}/export")
async def api_export(project_id: str, session_id: str, source: Optional[str] = None):
    source_id = source or _sync.current_source
    conv = get_conversation(project_id, session_id, source_id)

    if "error" in conv:
        return JSONResponse(conv, status_code=404)

    lines = ["=" * 60, f"Session: {session_id}",
             f"Project: {project_id.replace('-', '/').lstrip('/')}", "=" * 60, ""]

    if conv.get("summaries"):
        lines.append("SUMMARIES:")
        for s in conv["summaries"]:
            lines.append(f"  • {s}")
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
        if msg.get("tool_uses"):
            lines.append("")
            for tool in msg["tool_uses"]:
                lines.append(f"[TOOL: {tool['name']}]")
                # Full, untruncated input as pretty JSON — nothing is cut off.
                inp = tool.get("input")
                lines.append("INPUT:")
                if isinstance(inp, (dict, list)):
                    lines.append(json.dumps(inp, indent=2, ensure_ascii=False))
                else:
                    lines.append(str(inp if inp is not None else ""))
                result = tool.get("result")
                if result:
                    lines.append("OUTPUT:")
                    lines.append(str(result))
        if msg.get("usage"):
            tokens = msg["usage"].get("input_tokens", 0) + msg["usage"].get("output_tokens", 0)
            lines.append(f"\n[Tokens: {tokens}]")
        lines += ["", "=" * 60, ""]

    return Response(
        content="\n".join(lines),
        media_type="text/plain",
        headers={"Content-Disposition": f"attachment; filename={session_id}.txt"},
    )
