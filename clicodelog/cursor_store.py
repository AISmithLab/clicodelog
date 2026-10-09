"""Read-only access to Cursor's SQLite state databases (`state.vscdb`).

Cursor is usually running while this reads, and its databases are routinely
several GB. Everything here opens them read-only, queries by key range so the
unique index is used, and decodes values defensively (they are TEXT or BLOB
depending on the build).
"""

import json
import sqlite3
from pathlib import Path

from .editors import safe_name, workspace_folder
from .logging_setup import get_logger

log = get_logger(__name__)

AICHAT_KEY = "workbench.panel.aichat.view.aichat.chatdata"


def open_readonly(db: Path) -> sqlite3.Connection | None:
    """Read-only connection that never writes to, or blocks, Cursor's database.

    Even `mode=ro` is not hands-off on a WAL database: it creates `-wal` and
    `-shm` sidecars when they are missing. So:

    * no `-wal` beside the file — nothing is mid-write (an idle CLI chat, or
      Cursor not running): open `immutable=1`, which creates nothing and also
      works when the `-shm` is gone, which plain `mode=ro` cannot.
    * a `-wal` exists — Cursor is writing: `mode=ro`, which reads the WAL's
      recent writes (immutable would silently miss them) and only uses the
      sidecars that are already there.

    If Cursor holds a lock that refuses us (Windows locks more strictly than
    POSIX), the chat is skipped and the next hourly sync picks it up.
    """
    base = db.resolve().as_uri()
    live = db.with_name(db.name + "-wal").exists()
    queries = ["?mode=ro"] if live else ["?mode=ro&immutable=1"]
    last = None
    for query in queries:
        try:
            conn = sqlite3.connect(base + query, uri=True, timeout=5)
            conn.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone()
            return conn
        except sqlite3.Error as e:
            last = e
    log.warning("Could not open %s read-only: %s", db, last)
    return None


def text(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).decode("utf-8", errors="replace")
    return str(value)


def as_json(value):
    raw = text(value)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def has_table(conn, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                        (name,)).fetchone() is not None


def _item(conn, key: str):
    if not has_table(conn, "ItemTable"):
        return None
    row = conn.execute("SELECT value FROM ItemTable WHERE key=?", (key,)).fetchone()
    return as_json(row[0]) if row else None


def key_range(prefix: str) -> tuple[str, str]:
    # A key range uses the table's unique index; LIKE 'prefix%' would scan a
    # database that is routinely several GB.
    return prefix, prefix[:-1] + chr(ord(prefix[-1]) + 1)



def workspaces(user_dir: Path, prefix: str) -> list[dict]:
    """Per workspace: its folder, the composer ids it owns, legacy aichat tabs."""
    out = []
    root = user_dir / "workspaceStorage"
    if not root.is_dir():
        return out
    try:
        dirs = sorted(p for p in root.iterdir() if p.is_dir())
    except OSError:
        return out
    for ws in dirs:
        info = {"key": prefix + safe_name(ws.name), "folder": workspace_folder(ws),
                "composers": [], "aichat": []}
        db = ws / "state.vscdb"
        if db.is_file():
            conn = open_readonly(db)
            if conn is not None:
                try:
                    data = _item(conn, "composer.composerData")
                    if isinstance(data, dict):
                        info["composers"] = [c for c in data.get("allComposers") or []
                                             if isinstance(c, dict) and c.get("composerId")]
                    chat = _item(conn, AICHAT_KEY)
                    if isinstance(chat, dict):
                        info["aichat"] = [t for t in chat.get("tabs") or []
                                          if isinstance(t, dict) and t.get("bubbles")]
                except sqlite3.Error as e:
                    log.warning("Could not read %s: %s", db, e)
                finally:
                    conn.close()
        out.append(info)
    return out
