import importlib

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from .. import sync as _sync
from ..config import DATA_DIR, SOURCES
from ..search_index import count as indexed_count

router = APIRouter()


def _roots(cfg: dict) -> list:
    """Editor sources look in several places, resolved now rather than at import."""
    if cfg.get("syncer"):
        return importlib.import_module(f"clicodelog.{cfg['syncer']}").source_roots()
    return [cfg["source_dir"]]


@router.get("/api/sources")
def api_sources():
    out = []
    for sid, cfg in SOURCES.items():
        data_dir = DATA_DIR / cfg["data_subdir"]
        indexed = indexed_count(sid)      # SELECT count(*), not a full load

        # A source whose directory has files but yields no sessions means the
        # vendor changed format and our reader no longer matches. That is
        # exactly how Gemini support broke and stayed broken silently: it
        # rendered as "no projects", indistinguishable from never having used it.
        has_files = False
        if data_dir.exists():
            try:
                has_files = any(data_dir.rglob("*.json*"))
            except OSError:
                pass

        out.append({
            "id": sid,
            "name": cfg["name"],
            "available": any(p.exists() for p in _roots(cfg)),
            "session_count": indexed,
            "warning": ("Files are present but none could be read — the log format "
                        "may have changed.") if (has_files and indexed == 0) else None,
        })
    return {"sources": out, "current": _sync.current_source}


@router.post("/api/sources/{source_id}")
def api_set_source(source_id: str):
    if source_id not in SOURCES:
        return JSONResponse({"error": "Unknown source"}, status_code=400)
    # Kept only as the default for an omitted `source` query parameter; the
    # frontend passes source explicitly on every request.
    _sync.current_source = source_id
    return {"status": "success", "current": _sync.current_source}
