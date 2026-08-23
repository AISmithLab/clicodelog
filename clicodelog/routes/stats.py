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
from ..search_index import entries

router = APIRouter()
log = get_logger(__name__)

USAGE_KEYS = ("input", "output", "cache_read", "cache_creation")


def _blank():
    return {k: 0 for k in USAGE_KEYS}


def _add(into: dict, usage: dict) -> None:
    for k in USAGE_KEYS:
        into[k] += usage.get(k) or 0


def _billable(usage: dict) -> int:
    return sum(usage.get(k) or 0 for k in USAGE_KEYS)


@router.get("/api/stats")
def api_stats(source: Optional[str] = None, group: str = "day",
              project: Optional[str] = None):
    """Aggregate token usage by day, project or model."""
    source_id = source or _sync.current_source
    if source_id != "all" and source_id not in SOURCES:
        return JSONResponse({"error": "Unknown source"}, status_code=400)
    if group not in ("day", "project", "model", "tool"):
        return JSONResponse({"error": "group must be day, project, model or tool"},
                            status_code=400)

    sources = list(SOURCES) if source_id == "all" else [source_id]

    buckets: dict = defaultdict(_blank)
    counts: dict = defaultdict(int)
    tool_counts: dict = defaultdict(int)
    totals = _blank()
    sessions = 0
    models_seen: dict = defaultdict(int)

    for sid in sources:
        for e in entries(sid):
            if project and e["project_id"] != project:
                continue
            usage = e.get("usage") or {}
            sessions += 1
            _add(totals, usage)

            for name, n in (e.get("models") or {}).items():
                models_seen[name] += n
            for name, n in (e.get("tools") or {}).items():
                tool_counts[name] += n

            if group == "day":
                ts = e.get("last_ts") or e.get("first_ts") or ""
                key = ts[:10] or "unknown"
            elif group == "project":
                key = e.get("project_name") or e["project_id"]
            elif group == "model":
                names = e.get("models") or {}
                key = max(names.items(), key=lambda kv: kv[1])[0] if names else "unknown"
            else:
                for name, n in (e.get("tools") or {}).items():
                    counts[name] += n
                continue

            _add(buckets[key], usage)
            counts[key] += 1

    if group == "tool":
        rows = [{"key": k, "count": v} for k, v in
                sorted(counts.items(), key=lambda kv: -kv[1])]
    else:
        rows = [{
            "key": k,
            "sessions": counts[k],
            "usage": v,
            "total_tokens": _billable(v),
        } for k, v in buckets.items()]
        rows.sort(key=lambda r: r["key"] if group == "day" else -r["total_tokens"],
                  reverse=(group == "day"))

    return {
        "source": source_id,
        "group": group,
        "sessions": sessions,
        "totals": totals,
        "total_tokens": _billable(totals),
        "models": dict(sorted(models_seen.items(), key=lambda kv: -kv[1])),
        "top_tools": dict(sorted(tool_counts.items(), key=lambda kv: -kv[1])[:25]),
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
