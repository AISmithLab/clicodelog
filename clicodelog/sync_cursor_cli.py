"""Backup of the cursor-agent CLI's chats.

The CLI keeps each chat in its own directory under Cursor's home:

    chats/<workspace-hash>/<chat-id>/meta.json   cwd, title, createdAtMs, updatedAtMs,
                                                 hasConversation
    chats/<workspace-hash>/<chat-id>/store.db    SQLite: meta(key, value), blobs(id, data)

`blobs` is content-addressed (id = a hex digest). In rowid order its rows are
the conversation as JSON messages — {role: system|user|assistant|tool,
content: [parts]} — interleaved with binary rows (protobuf nodes that link
blobs into the conversation tree; nobody has published that schema). The
`meta` table's values are JSON, sometimes hex-encoded.

Each chat is exported, like the IDE store's, to one JSON Lines file:

    cursor/cli/<workspace-hash>/<chat-id>.jsonl
      {"type": "cursor-backup", "kind": "cli", "meta": <meta.json>, "storeMeta": ...}
      {"type": "cli-blob", "id": ..., "json": <message>}     a JSON row
      {"type": "cli-blob", "id": ..., "b64": ...}            a binary row, kept verbatim

Binary rows are kept so the backup stays a complete copy even though nothing
reads them yet. A store with no meta.json is a sub-agent run: it is backed up
under `_subagents/`, where it is preserved but not listed.
"""

import base64
import hashlib
import json
import sqlite3
from pathlib import Path

from .cursor_store import as_json, has_table, open_readonly, text
from .editors import cursor_cli_chats_dir, safe_name
from .logging_setup import get_logger

log = get_logger(__name__)

SUBAGENTS = "_subagents"


def _store_meta(conn) -> dict:
    out = {}
    if not has_table(conn, "meta"):
        return out
    for key, value in conn.execute("SELECT key, value FROM meta"):
        raw = text(value) or ""
        decoded = as_json(raw)
        if decoded is None:
            try:
                decoded = as_json(bytes.fromhex(raw).decode("utf-8"))
            except ValueError:
                decoded = None
        out[str(key)] = decoded if decoded is not None else raw
    return out


def _rows(conn) -> list:
    out = []
    for blob_id, data in conn.execute("SELECT id, data FROM blobs ORDER BY rowid"):
        raw = bytes(data) if isinstance(data, (bytes, bytearray, memoryview)) else \
            str(data or "").encode("utf-8")
        try:
            msg = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            msg = None
        if isinstance(msg, (dict, list)):
            out.append({"type": "cli-blob", "id": blob_id, "json": msg})
        else:
            out.append({"type": "cli-blob", "id": blob_id,
                        "b64": base64.b64encode(raw).decode("ascii")})
    return out


def _fingerprint(conn, meta_raw: bytes) -> str:
    n, top, size = conn.execute(
        "SELECT count(*), COALESCE(max(rowid), 0), COALESCE(sum(length(data)), 0) FROM blobs"
    ).fetchone()
    return hashlib.sha1(meta_raw).hexdigest() + f":{n}:{top}:{size}"


def _export_chat(chat: Path, ws_key: str, dest: Path, stats: dict, write_export,
                 header_fingerprint) -> None:
    meta_path = chat / "meta.json"
    meta, meta_raw = None, b""
    if meta_path.is_file():
        try:
            meta_raw = meta_path.read_bytes()
            meta = json.loads(meta_raw.decode("utf-8"))
        except (OSError, ValueError):
            meta = None
        if not isinstance(meta, dict):
            return                          # unreadable meta: try again next sync
        if meta.get("hasConversation") is False:
            return                          # opened, never prompted
    conn = open_readonly(chat / "store.db")
    if conn is None:
        return
    try:
        if not has_table(conn, "blobs"):
            return
        fingerprint = _fingerprint(conn, meta_raw)
        folder = ws_key if meta is not None else f"{ws_key}/{SUBAGENTS}"
        path = dest / "cli" / folder / f"{safe_name(chat.name)}.jsonl"
        if path.exists() and header_fingerprint(path) == fingerprint:
            stats["skipped"] += 1
            return
        rows = _rows(conn)
        header = {"type": "cursor-backup", "version": 1, "kind": "cli",
                  "composerId": chat.name, "workspace": (meta or {}).get("cwd") or "",
                  "workspaceKey": ws_key, "fingerprint": fingerprint,
                  "meta": meta, "storeMeta": _store_meta(conn)}
        write_export(path, [header] + rows, {r["id"] for r in rows}, stats)
    except sqlite3.Error as e:
        log.warning("Could not read Cursor CLI chat %s: %s", chat, e)
        stats["failed"] += 1
    finally:
        conn.close()


def sync(dest: Path, stats: dict, write_export, header_fingerprint) -> bool:
    """Export every CLI chat. The writer helpers come from sync_cursor so both
    Cursor stores share one additive, superseded-copy-keeping write path."""
    root = cursor_cli_chats_dir()
    if not root.is_dir():
        return False
    for ws in _subdirs(root):
        ws_key = safe_name(ws.name)
        for chat in _subdirs(ws):
            try:
                if (chat / "store.db").is_file():
                    _export_chat(chat, ws_key, dest, stats, write_export, header_fingerprint)
            except OSError as e:           # removed mid-sync, or unreadable
                log.warning("Could not back up Cursor CLI chat %s: %s", chat, e)
                stats["failed"] += 1
    return True


def _subdirs(path: Path) -> list:
    """One unreadable directory must not abort the whole sync."""
    try:
        return sorted(p for p in path.iterdir() if p.is_dir())
    except OSError as e:
        log.warning("Could not list %s: %s", path, e)
        return []
