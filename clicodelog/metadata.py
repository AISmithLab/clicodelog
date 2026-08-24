import threading

from .config import PROJECT_META_FILE
from .storage import load_json, write_json

# Custom project names and tags are user-authored too — same protection as
# bookmarks: a lock around read-modify-write, atomic save, backup copy.
_lock = threading.Lock()


def load_project_meta() -> dict:
    data = load_json(PROJECT_META_FILE, {})
    return data if isinstance(data, dict) else {}


def save_project_meta(meta: dict) -> None:
    write_json(PROJECT_META_FILE, meta, keep_backup=True, indent=2)


def update_project_meta(key: str, fields: dict) -> dict:
    """Merge fields into one project's metadata under a lock."""
    with _lock:
        meta = load_project_meta()
        meta.setdefault(key, {})
        meta[key].update(fields)
        save_project_meta(meta)
        return meta[key]


def get_project_meta_key(project_id: str, source_id: str) -> str:
    return f"{source_id}:{project_id}"
