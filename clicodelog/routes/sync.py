from typing import Optional

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from .. import sync as _sync
from ..config import DATA_DIR, SOURCES
from ..logging_setup import get_logger
from ..storage import has_free_space

router = APIRouter()
log = get_logger(__name__)


@router.post("/api/sync")
def api_sync(source: Optional[str] = None):
    source_id = source or _sync.current_source

    # Don't queue behind a running sync on the event loop — say so instead.
    if not _sync.sync_lock.acquire(blocking=False):
        return JSONResponse(
            {"status": "busy", "message": "A sync is already running."},
            status_code=409,
        )
    _sync.sync_lock.release()

    try:
        ran = _sync.sync_data(source_id=source_id, silent=True)
        if not ran:
            return JSONResponse(
                {"status": "error",
                 "message": f"Sync did not run for source '{source_id}'."},
                status_code=503,
            )
        last = _sync.last_sync_time.get(source_id)
        return {"status": "success", "source": source_id,
                "last_sync": last.isoformat() if last else None}
    except Exception as e:
        log.exception("Manual sync failed for %s", source_id)
        return JSONResponse({"status": "error", "message": str(e)}, status_code=500)


@router.get("/api/status")
def api_status(source: Optional[str] = None):
    source_id = source or _sync.current_source
    data_dir = DATA_DIR / SOURCES.get(source_id, {}).get("data_subdir", "")
    last = _sync.last_sync_time.get(source_id)
    _, free = has_free_space(DATA_DIR, 0)
    return {
        "source": source_id,
        "last_sync": last.isoformat() if last else None,
        "sync_interval_hours": 1,
        "data_dir": str(data_dir),
        "initial_sync_done": _sync.initial_sync_done,
        "free_bytes": free,
    }
