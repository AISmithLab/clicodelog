"""Backup of Cursor chat and agent history.

Cursor keeps history in three places, and all are backed up:

1. **The composer store** — SQLite (`state.vscdb`) under Cursor's user dir:
     globalStorage/state.vscdb   cursorDiskKV  composerData:<id>        one chat
                                               bubbleId:<id>:<bubble>   one message
     workspaceStorage/<hash>/state.vscdb  ItemTable  composer.composerData
                                          (which chats belong to the workspace)
   This is the complete record: timestamps, model, tokens, thinking, tool output.
   A live SQLite file cannot be safely cloned while Cursor writes to it, so each
   chat is exported through a read-only connection to its own JSON Lines file:
     cursor/composers/<workspace>/<id>.jsonl
       {"type": "cursor-backup", ...header, "fingerprint": ...}
       {"type": "composer", "data": <composerData verbatim>}
       {"type": "bubble",   "data": <bubble verbatim>}         one per message
   Very old Cursor builds kept chats in the workspace ItemTable under
   `workbench.panel.aichat.view.aichat.chatdata`; those export the same way with
   one {"type": "aichat-tab"} record.

2. **Agent transcripts** — plain JSON Lines Cursor writes under
   ~/.cursor/projects/<slug>/agent-transcripts/, copied file for file into
   cursor/transcripts/<slug>/. Leaner (no timestamps, model, tokens or tool
   output) but written by current builds whether or not the store has the chat.

3. **The cursor-agent CLI's chats** — one SQLite `store.db` per chat under
   ~/.cursor/chats/; see sync_cursor_cli.py.

The backup is additive. A chat is only rewritten when its fingerprint changes,
and if the new snapshot has lost messages the old one had (Cursor rewinds a
chat when you edit an earlier prompt), the old file is first kept beside it as
`<id>.superseded-<time>.bak`.
"""

import hashlib
import json
import sqlite3
import time
from pathlib import Path

from .cursor_store import as_json, has_table, key_range, open_readonly, text, workspaces
from .editors import cursor_cli_chats_dir, cursor_projects_dir, cursor_user_dirs, safe_name
from .logging_setup import get_logger
from .storage import has_free_space, keep_superseded, write_text_atomic

log = get_logger(__name__)

NO_WORKSPACE = "_global"


def source_roots() -> list[Path]:
    return [d for d, _ in cursor_user_dirs()] + [cursor_projects_dir(), cursor_cli_chats_dir()]


# ------------------------------------------------------------------ writing
def _existing(dest: Path) -> dict:
    """composer id -> its current backup file, wherever it lives. A chat stays
    in the file it was first written to even if Cursor reassigns it."""
    out = {}
    for f in (dest / "composers").glob("*/*.jsonl"):
        out.setdefault(f.stem, f)
    return out


def read_header(path: Path) -> dict:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            head = json.loads(fh.readline() or "null")
        return head if isinstance(head, dict) else {}
    except (OSError, ValueError):
        return {}


def header_fingerprint(path: Path) -> str | None:
    return read_header(path).get("fingerprint")


def _record_ids(path: Path) -> set:
    ids = set()
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                data = rec.get("data") if isinstance(rec, dict) else None
                if rec.get("type") == "bubble" and isinstance(data, dict):
                    ids.add(data.get("bubbleId"))
                elif rec.get("type") == "composer" and isinstance(data, dict):
                    ids.update(b.get("bubbleId") for b in data.get("conversation") or []
                               if isinstance(b, dict))
                elif rec.get("type") == "cli-blob":
                    ids.add(rec.get("id"))
                elif rec.get("type") == "aichat-tab" and isinstance(data, dict):
                    ids.update(b.get("id") for b in data.get("bubbles") or []
                               if isinstance(b, dict))
    except OSError:
        pass
    ids.discard(None)
    return ids


def write_export(path: Path, records: list, new_ids: set, stats: dict) -> None:
    body = "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n"
    ok, _ = has_free_space(path.parent, len(body.encode("utf-8")), margin=1.05)
    if not ok:
        stats["skipped_no_space"] += 1
        return
    if path.exists():
        lost = _record_ids(path) - new_ids
        if lost:
            keep = keep_superseded(path)
            if keep is None:
                stats["failed"] += 1
                return
            log.info("Cursor chat %s lost %d messages; kept old copy %s",
                     path.stem, len(lost), keep.name)
    if write_text_atomic(path, body):
        stats["copied"] += 1
    else:
        stats["failed"] += 1


def _export_composers(conn, spaces: list, dest: Path, stats: dict) -> None:
    owner = {}           # composer id -> workspace info
    inline = {}          # composer id -> header from the workspace list
    for ws in spaces:
        for c in ws["composers"]:
            owner.setdefault(c["composerId"], ws)
            inline.setdefault(c["composerId"], c)

    existing = _existing(dest)
    seen = set()
    has_kv = conn is not None and has_table(conn, "cursorDiskKV")
    rows = []
    if has_kv:
        lo, hi = key_range("composerData:")
        # fetchall: each later query then runs in its own short read, instead
        # of one transaction spanning the export that stops Cursor's WAL from
        # checkpointing.
        rows = conn.execute("SELECT key, value FROM cursorDiskKV WHERE key >= ? AND key < ?",
                            (lo, hi)).fetchall()
    full = _full_verify_due(dest)
    for key, value in rows:
        cid = key.split(":", 1)[1]
        raw = text(value) or ""
        composer = as_json(raw)
        if not isinstance(composer, dict) or not cid:
            continue
        seen.add(cid)
        _export_one(conn, cid, raw, composer, owner.get(cid), existing, dest, stats, full)

    # Builds that kept whole chats inline in the workspace list, never in the KV.
    for cid, c in inline.items():
        if cid not in seen and c.get("conversation"):
            _export_one(None, cid, json.dumps(c, sort_keys=True), c, owner.get(cid),
                        existing, dest, stats, full)
    if full and has_kv:
        _mark_verified(dest)


# Change detection runs in two tiers, because the global database is routinely
# several GB and is read every hour:
#   hourly — the composer record plus count, total length and rowid sum of its
#            bubbles, all from one aggregate query that never reads a bubble.
#            Cursor rewrites composerData (lastUpdatedAt, headers) whenever a
#            chat changes, and a replaced row gets a new rowid.
#   daily  — a hash of every bubble's content, which also catches an in-place
#            UPDATE that kept the same length.
FULL_VERIFY_SECONDS = 24 * 3600
_VERIFIED = ".content-verified"


def _full_verify_due(dest: Path) -> bool:
    try:
        return time.time() - (dest / _VERIFIED).stat().st_mtime >= FULL_VERIFY_SECONDS
    except OSError:
        return True


def _mark_verified(dest: Path) -> None:
    try:
        dest.mkdir(parents=True, exist_ok=True)
        (dest / _VERIFIED).touch()
    except OSError:
        pass


def _signature(conn, cid: str, raw: str) -> str:
    sig = hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()
    if conn is None:
        return sig
    lo, hi = key_range(f"bubbleId:{cid}:")
    n, size, rowids = conn.execute(
        "SELECT count(*), COALESCE(sum(length(value)), 0), COALESCE(sum(rowid), 0) "
        "FROM cursorDiskKV WHERE key >= ? AND key < ?", (lo, hi)).fetchone()
    return f"{sig}:{n}:{size}:{rowids}"


def _bubbles(conn, cid: str, raw: str) -> tuple[dict, str]:
    """(bubble id -> bubble, hash of the composer and every bubble's content)."""
    bubbles = {}
    digest = hashlib.sha1(raw.encode("utf-8", "replace"))
    if conn is not None:
        lo, hi = key_range(f"bubbleId:{cid}:")
        for key, value in conn.execute(
                "SELECT key, value FROM cursorDiskKV WHERE key >= ? AND key < ? ORDER BY key",
                (lo, hi)).fetchall():
            body = text(value) or ""
            digest.update(key.encode("utf-8", "replace") + b"\0" + body.encode("utf-8", "replace"))
            b = as_json(body)
            if isinstance(b, dict):
                bubbles[key.rsplit(":", 1)[1]] = b
    return bubbles, digest.hexdigest()


def _export_one(conn, cid, raw, composer, ws, existing, dest, stats, full=False) -> None:
    signature = _signature(conn, cid, raw)
    path = existing.get(cid) or (dest / "composers" / (ws["key"] if ws else NO_WORKSPACE)
                                 / f"{safe_name(cid)}.jsonl")
    head = read_header(path) if path.exists() else {}
    if head.get("fingerprint") == signature and not full:
        stats["skipped"] += 1
        return
    bubbles, content = _bubbles(conn, cid, raw)
    if head.get("fingerprint") == signature and head.get("contentHash") == content:
        stats["skipped"] += 1
        return

    header = {"type": "cursor-backup", "version": 1, "kind": "composer",
              "composerId": cid, "workspace": (ws or {}).get("folder", ""),
              "workspaceKey": (ws or {}).get("key", ""), "fingerprint": signature,
              "contentHash": content, "exportedAt": int(time.time() * 1000)}
    records = [header, {"type": "composer", "data": composer}]
    order = [h.get("bubbleId") for h in composer.get("fullConversationHeadersOnly") or []
             if isinstance(h, dict)]
    for bid in order:
        if bid in bubbles:
            records.append({"type": "bubble", "data": bubbles.pop(bid)})
    # Bubbles no header references (a regenerated reply, say) are kept, flagged.
    for b in bubbles.values():
        records.append({"type": "bubble", "orphan": True, "data": b})
    ids = {r["data"].get("bubbleId") for r in records[2:]}
    ids.update(b.get("bubbleId") for b in composer.get("conversation") or []
               if isinstance(b, dict))
    ids.discard(None)
    write_export(path, records, ids, stats)


def _export_aichat(ws: dict, dest: Path, stats: dict) -> None:
    for tab in ws["aichat"]:
        tid = safe_name(tab.get("tabId") or "", "tab")
        raw = json.dumps(tab, sort_keys=True)
        fingerprint = hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()
        path = dest / "composers" / ws["key"] / f"aichat-{tid}.jsonl"
        if path.exists() and header_fingerprint(path) == fingerprint:
            stats["skipped"] += 1
            continue
        header = {"type": "cursor-backup", "version": 1, "kind": "aichat",
                  "composerId": f"aichat-{tid}", "workspace": ws["folder"],
                  "workspaceKey": ws["key"], "fingerprint": fingerprint}
        ids = {b.get("id") for b in tab.get("bubbles") or [] if isinstance(b, dict)}
        ids.discard(None)
        write_export(path, [header, {"type": "aichat-tab", "data": tab}], ids, stats)


# ------------------------------------------------------------------ entry point
def sync(dest_dir: Path, stats: dict, copy) -> bool:
    found = False
    for user_dir, prefix in cursor_user_dirs():
        if not user_dir.is_dir():
            continue
        found = True
        spaces = workspaces(user_dir, prefix)
        for ws in spaces:
            if ws["aichat"]:
                _export_aichat(ws, dest_dir, stats)
        db = user_dir / "globalStorage" / "state.vscdb"
        conn = open_readonly(db) if db.is_file() else None
        try:
            _export_composers(conn, spaces, dest_dir, stats)
        except sqlite3.Error as e:
            log.warning("Could not export Cursor chats from %s: %s", db, e)
            stats["failed"] += 1
        finally:
            if conn is not None:
                conn.close()

    projects = cursor_projects_dir()
    if projects.is_dir():
        try:
            slugs = sorted(p for p in projects.iterdir() if p.is_dir())
        except OSError:
            slugs = []
        for proj in slugs:
            transcripts = proj / "agent-transcripts"
            if transcripts.is_dir():
                found = True
                copy(transcripts, dest_dir / "transcripts" / safe_name(proj.name), stats)

    from . import sync_cursor_cli
    if sync_cursor_cli.sync(dest_dir, stats, write_export, header_fingerprint):
        found = True
    return found
