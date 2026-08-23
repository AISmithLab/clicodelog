"""Usage analytics and file-edit archaeology.

Both are cheap because the index pass already reads every changed session file
once: token totals, model counts and tool counts are accumulated during that
same pass rather than needing one of their own.

Token figures here include cache_read and cache_creation. The UI previously
summed only input+output, which reported 0.9% of real usage.
"""

from collections import defaultdict
from typing import Optional

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from .. import sync as _sync
from ..config import SOURCES
from ..logging_setup import get_logger
from ..search_index import json_counter_totals, usage_by, usage_totals

router = APIRouter()
log = get_logger(__name__)

USAGE_KEYS = ("input", "output", "cache_read", "cache_creation")


def _billable(usage: dict) -> int:
    return sum(usage.get(k) or 0 for k in USAGE_KEYS)


@router.get("/api/stats")
def api_stats(source: Optional[str] = None, group: str = "day",
              project: Optional[str] = None):
    """Aggregate token usage by day, project, model or tool.

    Everything is summed by SQL. Building these totals in Python meant loading
    every session row into memory first, which is what the metadata store was
    moved to SQLite to avoid.
    """
    source_id = source or _sync.current_source
    if source_id != "all" and source_id not in SOURCES:
        return JSONResponse({"error": "Unknown source"}, status_code=400)
    if group not in ("day", "project", "model", "tool"):
        return JSONResponse({"error": "group must be day, project, model or tool"},
                            status_code=400)

    sources = list(SOURCES) if source_id == "all" else [source_id]

    totals = {k: 0 for k in USAGE_KEYS}
    sessions = 0
    models: dict = defaultdict(int)
    tools: dict = defaultdict(int)
    merged: dict = defaultdict(lambda: {k: 0 for k in USAGE_KEYS} | {"sessions": 0})

    for sid in sources:
        t = usage_totals(sid, project)
        sessions += t.pop("sessions", 0)
        for k in USAGE_KEYS:
            totals[k] += t.get(k, 0)

        for name, n in json_counter_totals(sid, "models", limit=50).items():
            models[name] += n
        for name, n in json_counter_totals(sid, "tools", limit=50).items():
            tools[name] += n

        if group in ("day", "project"):
            for row in usage_by(sid, group, project):
                bucket = merged[row["key"]]
                bucket["sessions"] += row["sessions"]
                for k in USAGE_KEYS:
                    bucket[k] += row["usage"][k]

    if group == "tool":
        rows = [{"key": k, "count": v} for k, v in
                sorted(tools.items(), key=lambda kv: -kv[1])]
    elif group == "model":
        rows = [{"key": k, "count": v} for k, v in
                sorted(models.items(), key=lambda kv: -kv[1])]
    else:
        rows = [{
            "key": k,
            "sessions": v["sessions"],
            "usage": {kk: v[kk] for kk in USAGE_KEYS},
            "total_tokens": sum(v[kk] for kk in USAGE_KEYS),
        } for k, v in merged.items()]
        rows.sort(key=lambda r: r["key"] if group == "day" else -r["total_tokens"],
                  reverse=(group == "day"))

    return {
        "source": source_id,
        "group": group,
        "sessions": sessions,
        "totals": totals,
        "total_tokens": _billable(totals),
        "models": dict(sorted(models.items(), key=lambda kv: -kv[1])[:25]),
        "top_tools": dict(sorted(tools.items(), key=lambda kv: -kv[1])[:25]),
        "rows": rows[:400],
    }


@router.get("/api/files")
def api_files(path: Optional[str] = None, source: Optional[str] = None,
              limit: int = 100):
    """Which sessions edited a file? Answers 'when did an agent last touch X'."""
    if not path or len(path) < 2:
        return JSONResponse({"error": "path query is required (min 2 chars)"},
                            status_code=400)
    source_id = source or _sync.current_source
    try:
        from ..fts import sessions_touching
        hits = sessions_touching(path, None if source_id == "all" else source_id,
                                 limit=max(1, min(limit, 500)))
    except Exception:
        log.exception("File archaeology lookup failed")
        return {"results": [], "available": False,
                "message": "Build the content index to enable this."}

    grouped: dict = {}
    for h in hits:
        key = (h["source"], h["project_id"], h["session_id"])
        g = grouped.setdefault(key, {
            "source": h["source"],
            "project_id": h["project_id"],
            "project_name": h["project_name"],
            "session_id": h["session_id"],
            "edits": [],
        })
        if len(g["edits"]) < 20:
            g["edits"].append({"path": h["path"], "uuid": h["uuid"], "ts": h["ts"]})
    out = list(grouped.values())
    out.sort(key=lambda g: max((e["ts"] or "") for e in g["edits"]), reverse=True)
    return {"results": out, "available": True, "match_count": len(hits)}


@router.post("/api/search/build")
def api_build_index(source: Optional[str] = None, tool_cap: Optional[int] = None,
                    force: bool = False):
    """Build the full-text index. Refuses if it would not fit on disk."""
    import threading

    from ..fts import DEFAULT_TOOL_CAP, build_index, index_status

    source_id = source or _sync.current_source
    if source_id not in SOURCES:
        return JSONResponse({"error": "Unknown source"}, status_code=400)

    status = index_status(source_id)
    if status.get("state") == "building":
        return status

    cap = DEFAULT_TOOL_CAP if tool_cap is None else max(0, tool_cap)

    def _run():
        try:
            build_index(source_id, tool_cap=cap, force=force)
        except Exception:
            log.exception("FTS build failed for %s", source_id)

    threading.Thread(target=_run, daemon=True).start()
    return {"state": "started", "source": source_id, "tool_cap": cap}
