"""Search endpoints.

The old fallback read up to 8 MB from every session file — 2.75 GB per query,
measured at 15.8 seconds to return an empty list, on the event loop. It also
missed any match spanning a 64 KB chunk boundary and matched raw JSON, so
"claude" hit every file via the model field.

Content search is now served by the SQLite FTS index when it exists. Metadata
matching (id, cwd, summary, project) always runs, and results from both are
merged rather than being an either/or.
"""

from typing import Optional

from fastapi import APIRouter

from .. import sync as _sync
from ..config import SOURCES
from ..search_index import search_index
from ..logging_setup import get_logger

router = APIRouter()
log = get_logger(__name__)

DEFAULT_LIMIT = 50
MAX_LIMIT = 200


@router.get("/api/search")
def api_search(q: Optional[str] = None,
               source: Optional[str] = None,
               limit: int = DEFAULT_LIMIT,
               offset: int = 0,
               project: Optional[str] = None,
               role: Optional[str] = None,
               after: Optional[str] = None,
               before: Optional[str] = None):
    query = (q or "").strip()
    source_id = source or _sync.current_source
    limit = max(1, min(limit, MAX_LIMIT))
    offset = max(0, offset)

    if not query or source_id not in SOURCES:
        return {"results": [], "total": 0, "content_search": False}

    # Metadata hits: instant, in memory, no file reads.
    meta_hits = search_index(query, source_id)
    if project:
        meta_hits = [h for h in meta_hits if h["project_id"] == project]

    results = []
    seen = set()
    for h in meta_hits:
        key = (h["project_id"], h["session_id"])
        seen.add(key)
        results.append({**h, "matches": [], "match_count": 0, "matched": "metadata"})

    # Content hits from the full-text index, when it has been built.
    content_available = False
    try:
        from ..fts import search_content, is_available
        content_available = is_available(source_id)
        if content_available:
            for hit in search_content(query, source_id, limit=limit * 2,
                                      project=project, role=role,
                                      after=after, before=before):
                key = (hit["project_id"], hit["session_id"])
                if key in seen:
                    # Enrich the metadata hit with its snippets instead of
                    # dropping one or the other.
                    for r in results:
                        if (r["project_id"], r["session_id"]) == key:
                            r["matches"] = hit["matches"]
                            r["match_count"] = hit["match_count"]
                            r["matched"] = "both"
                            break
                    continue
                seen.add(key)
                results.append({**hit, "matched": "content"})
    except Exception:
        log.exception("Content search failed; returning metadata results only")

    total = len(results)
    return {
        "results": results[offset:offset + limit],
        "total": total,
        "offset": offset,
        "content_search": content_available,
    }


@router.get("/api/search/status")
def api_search_status(source: Optional[str] = None):
    """Whether full-text search is available, and how far a build has got."""
    source_id = source or _sync.current_source
    try:
        from ..fts import index_status
        return index_status(source_id)
    except Exception:
        log.exception("Could not read FTS status")
        return {"state": "unavailable", "reason": "index module error"}
