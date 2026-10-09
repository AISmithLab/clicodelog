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
import shutil
import sqlite3
import time
from pathlib import Path

from .cursor_store import as_json, has_table, key_range, open_readonly, text, workspaces
from .editors import cursor_cli_chats_dir, cursor_projects_dir, cursor_user_dirs, safe_name
from .logging_setup import get_logger
from .storage import has_free_space, write_text_atomic

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


def header_fingerprint(path: Path) -> str | None:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            head = json.loads(fh.readline() or "null")
        return head.get("fingerprint") if isinstance(head, dict) else None
    except (OSError, ValueError):
        return None


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
            stamp, n = time.strftime("%Y%m%d-%H%M%S"), 0
            keep = path.with_name(f"{path.stem}.superseded-{stamp}.bak")
            while keep.exists():           # never replace an earlier preserved copy
                n += 1
                keep = path.with_name(f"{path.stem}.superseded-{stamp}-{n}.bak")
            try:
                shutil.copy2(path, keep)
                log.info("Cursor chat %s lost %d messages; kept old copy %s",
                         path.stem, len(lost), keep.name)
            except OSError:
                log.warning("Could not preserve %s; leaving it untouched", path)
                stats["failed"] += 1
                return
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
        rows = conn.execute("SELECT key, value FROM cursorDiskKV WHERE key >= ? AND key < ?",
                            (lo, hi))
    for key, value in rows:
        cid = key.split(":", 1)[1]
        raw = text(value) or ""
        composer = as_json(raw)
        if not isinstance(composer, dict) or not cid:
            continue
        seen.add(cid)
        _export_one(conn, cid, raw, composer, owner.get(cid), existing, dest, stats)

    # Builds that kept whole chats inline in the workspace list, never in the KV.
    for cid, c in inline.items():
        if cid not in seen and c.get("conversation"):
            _export_one(None, cid, json.dumps(c, sort_keys=True), c, owner.get(cid),
                        existing, dest, stats)


def _export_one(conn, cid, raw, composer, ws, existing, dest, stats) -> None:
    bubbles = {}
    # Content, not lengths: Cursor rewrites bubbles in place (a token count, a
    # status, streamed text replaced by text of the same length).
    digest = hashlib.sha1(raw.encode("utf-8", "replace"))
    if conn is not None:
        lo, hi = key_range(f"bubbleId:{cid}:")
        for key, value in conn.execute(
                "SELECT key, value FROM cursorDiskKV WHERE key >= ? AND key < ? ORDER BY key",
                (lo, hi)):
            body = text(value) or ""
            digest.update(key.encode("utf-8", "replace") + b"\0" + body.encode("utf-8", "replace"))
            b = as_json(body)
            if isinstance(b, dict):
                bubbles[key.rsplit(":", 1)[1]] = b
    fingerprint = digest.hexdigest() + f":{len(bubbles)}"

    path = existing.get(cid) or (dest / "composers" / (ws["key"] if ws else NO_WORKSPACE)
                                 / f"{safe_name(cid)}.jsonl")
    if path.exists() and header_fingerprint(path) == fingerprint:
        stats["skipped"] += 1
        return

    header = {"type": "cursor-backup", "version": 1, "kind": "composer",
              "composerId": cid, "workspace": (ws or {}).get("folder", ""),
              "workspaceKey": (ws or {}).get("key", ""), "fingerprint": fingerprint,
              "exportedAt": int(time.time() * 1000)}
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
