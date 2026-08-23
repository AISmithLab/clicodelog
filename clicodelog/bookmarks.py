import threading
from datetime import datetime

from .config import APP_DATA_DIR
from .storage import load_json, write_json

BOOKMARKS_FILE = APP_DATA_DIR / "bookmarks.json"

# Bookmarks are the only user-authored data in the app — everything else under
# ~/.clicodelog can be regenerated from the source logs. They get a lock so
# concurrent requests can't interleave a read-modify-write, and a .bak copy on
# every save.
_lock = threading.Lock()


def load_bookmarks() -> list:
    data = load_json(BOOKMARKS_FILE, [])
    return data if isinstance(data, list) else []


def save_bookmarks(bookmarks: list) -> None:
    write_json(BOOKMARKS_FILE, bookmarks, keep_backup=True, indent=2)


def _bid(b: dict) -> str:
    # Stable key: source + project + session + message anchor (uuid or index).
    anchor = b.get("uuid") or f"idx{b.get('msg_index')}"
    return f"{b.get('source')}:{b.get('project_id')}:{b.get('session_id')}:{anchor}"


def add_bookmark(b: dict) -> list:
    with _lock:
        bookmarks = load_bookmarks()
        key = _bid(b)
        bookmarks = [x for x in bookmarks if _bid(x) != key]  # de-dup / replace
        b["id"] = key
        b.setdefault("created", datetime.now().isoformat())
        bookmarks.append(b)
        save_bookmarks(bookmarks)
        return bookmarks


def remove_bookmark(bid: str) -> list:
    with _lock:
        bookmarks = [x for x in load_bookmarks() if x.get("id") != bid]
        save_bookmarks(bookmarks)
        return bookmarks
