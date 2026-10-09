"""Full-text search over message bodies, backed by SQLite FTS5.

Why SQLite: sqlite3 is in the standard library, FTS5 supports incremental
per-row updates (so an hourly sync costs milliseconds when nothing changed),
and snippet()/bm25() come built in. ripgrep over the raw logs measured 1.2-3.2 s
per query and matches JSON scaffolding rather than text; tantivy and duckdb add
wheels, and duckdb's FTS cannot update incrementally at all.

Two details here were established by measurement, not by reading documentation:

* The tokenizer is `tokenchars '_.-'`, NOT `'_./-'`. Including "/" makes
  `routes/search.py` a single token, which means typing `search.py` finds
  nothing. Leaving "/" a separator satisfies 7/7 real developer queries; the
  other config satisfies 5/7.

* User input is never passed to MATCH raw. FTS5's query grammar is not its
  tokenizer: `search.py` raises "syntax error near ." Eight of eleven realistic
  queries crash without quoting, including every file path.
"""

import json
import sqlite3
import threading
import time
from pathlib import Path

from .config import APP_DATA_DIR, DATA_DIR, SOURCES
from .fts_extract import EXTRACT, extract_parsed
from .logging_setup import get_logger
from .parsers import WHOLE_FILE_PARSERS
from .search_index import session_files
from .storage import has_free_space

log = get_logger(__name__)

DB_FILE = APP_DATA_DIR / "fts.db"

# Tool output is the bulk of the bytes and the least valuable thing to search.
# Extracted text is ~14.4% of raw; the projected index is ~0.9 GB uncapped,
# ~0.72 GB at 1k, ~0.46 GB with tool text excluded entirely.
DEFAULT_TOOL_CAP = 4000
TEXT_RATIO = 0.144          # measured share of raw bytes that is indexable text
INDEX_OVERHEAD = 1.75       # stored content + inverted index

TOKENIZER = "unicode61 remove_diacritics 2 tokenchars '_.-'"

SCHEMA = f"""
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
CREATE TABLE IF NOT EXISTS files(
  id INTEGER PRIMARY KEY,
  path TEXT UNIQUE, source TEXT, project_id TEXT, project_name TEXT,
  session_id TEXT, size INTEGER, mtime REAL, indexed_bytes INTEGER, last_ts TEXT
);
CREATE INDEX IF NOT EXISTS files_source ON files(source, project_id);
CREATE TABLE IF NOT EXISTS messages(
  id INTEGER PRIMARY KEY,
  file_id INTEGER REFERENCES files(id) ON DELETE CASCADE,
  msg_idx INTEGER, uuid TEXT, role TEXT, kind TEXT, ts TEXT,
  dialog TEXT, tool TEXT
);
CREATE INDEX IF NOT EXISTS messages_file ON messages(file_id);
CREATE TABLE IF NOT EXISTS edits(
  file_id INTEGER REFERENCES files(id) ON DELETE CASCADE,
  path TEXT, uuid TEXT, ts TEXT
);
CREATE INDEX IF NOT EXISTS edits_path ON edits(path);
CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(
  dialog, tool, content='messages', content_rowid='id',
  tokenize = "{TOKENIZER}", prefix = '2 3'
);
CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
  INSERT INTO fts(rowid, dialog, tool) VALUES (new.id, new.dialog, new.tool);
END;
CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
  INSERT INTO fts(fts, rowid, dialog, tool) VALUES('delete', old.id, old.dialog, old.tool);
END;
CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY, value TEXT);
"""

_local = threading.local()
_build_lock = threading.Lock()
_progress: dict = {}


# --------------------------------------------------------------------- plumbing
def fts5_available() -> bool:
    try:
        c = sqlite3.connect(":memory:")
        c.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        c.close()
        return True
    except sqlite3.OperationalError:
        return False


def _connect(readonly: bool = False) -> sqlite3.Connection:
    """One connection per thread; handlers run in FastAPI's threadpool."""
    key = "ro" if readonly else "rw"
    conn = getattr(_local, key, None)
    if conn is not None:
        return conn
    APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_FILE), check_same_thread=False, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=15000")
    setattr(_local, key, conn)
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def is_available(source_id: str) -> bool:
    if not DB_FILE.exists():
        return False
    try:
        conn = _connect()
        row = conn.execute("SELECT count(*) c FROM files WHERE source=?", (source_id,)).fetchone()
        return bool(row and row["c"])
    except sqlite3.Error:
        return False


def estimate_index_bytes(source_id: str | None = None) -> int:
    """Projected index size from the raw corpus, using the measured text ratio."""
    total = 0
    sources = [source_id] if source_id else list(SOURCES.keys())
    for sid in sources:
        data_dir = DATA_DIR / SOURCES[sid]["data_subdir"]
        if not data_dir.exists():
            continue
        for f, _ in session_files(sid, data_dir):
            try:
                total += f.stat().st_size
            except OSError:
                pass
    return int(total * TEXT_RATIO * INDEX_OVERHEAD)


# --------------------------------------------------------------------- build
def _index_file(conn, path: Path, source_id: str, project_id: str, project_name: str,
                tool_cap: int) -> int:
    try:
        st = path.stat()
    except OSError:
        return 0

    row = conn.execute("SELECT id, size, mtime, indexed_bytes FROM files WHERE path=?",
                       (str(path),)).fetchone()
    start_offset = 0
    if row:
        fid = row["id"]
        if row["size"] == st.st_size and row["mtime"] == st.st_mtime:
            return 0                                        # unchanged
        if (st.st_size > row["size"] and row["indexed_bytes"]
                and source_id not in WHOLE_FILE_PARSERS):
            start_offset = row["indexed_bytes"]             # append-only fast path
        else:
            conn.execute("DELETE FROM messages WHERE file_id=?", (fid,))
            conn.execute("DELETE FROM edits WHERE file_id=?", (fid,))
    else:
        cur = conn.execute(
            "INSERT INTO files(path, source, project_id, project_name, session_id, "
            "size, mtime, indexed_bytes) VALUES (?,?,?,?,?,?,?,0)",
            (str(path), source_id, project_id, project_name, path.stem, st.st_size, st.st_mtime))
        fid = cur.lastrowid

    idx = conn.execute("SELECT COALESCE(MAX(msg_idx), -1) m FROM messages WHERE file_id=?",
                       (fid,)).fetchone()["m"]
    rows, edit_rows, last_ts = [], [], None

    if source_id in WHOLE_FILE_PARSERS:
        try:
            produced = list(extract_parsed(path, source_id))
        except Exception:
            log.exception("Could not index %s", path)
            # The row was stamped with this size/mtime before parsing; clear the
            # stamp so the next build retries instead of treating it as done.
            conn.execute("UPDATE files SET size=-1, mtime=-1 WHERE id=?", (fid,))
            return 0
        for uid, role, kind, ts, dialog, tool, edited in produced:
            if edited:
                edit_rows.append((fid, edited, uid, ts))
                continue
            idx += 1
            if tool and tool_cap >= 0:
                tool = tool[:tool_cap] if tool_cap else ""
            rows.append((fid, idx, uid, role, kind, ts, dialog, tool))
            last_ts = ts or last_ts
        return _store_rows(conn, fid, st, project_id, project_name, rows, edit_rows, last_ts)

    extract = EXTRACT[source_id]
    try:
        with open(path, "rb") as fh:
            if start_offset:
                fh.seek(start_offset)
                probe = fh.readline()
                try:
                    json.loads(probe)
                    fh.seek(start_offset)
                except (json.JSONDecodeError, ValueError):
                    conn.execute("DELETE FROM messages WHERE file_id=?", (fid,))
                    conn.execute("DELETE FROM edits WHERE file_id=?", (fid,))
                    fh.seek(0)
                    idx = -1
            for line in fh:
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if not isinstance(entry, dict):
                    continue
                try:
                    produced = list(extract(entry))
                except (AttributeError, TypeError):
                    continue
                for uid, role, kind, ts, dialog, tool, edited in produced:
                    idx += 1
                    if tool and tool_cap >= 0:
                        tool = tool[:tool_cap] if tool_cap else ""
                    rows.append((fid, idx, uid, role, kind, ts, dialog, tool))
                    if edited:
                        edit_rows.append((fid, edited, uid, ts))
                    if ts:
                        last_ts = ts
    except OSError as e:
        log.warning("Could not index %s: %s", path, e)
        return 0
    return _store_rows(conn, fid, st, project_id, project_name, rows, edit_rows, last_ts)


def _store_rows(conn, fid, st, project_id, project_name, rows, edit_rows, last_ts) -> int:
    if rows:
        conn.executemany(
            "INSERT INTO messages(file_id,msg_idx,uuid,role,kind,ts,dialog,tool) "
            "VALUES (?,?,?,?,?,?,?,?)", rows)
    if edit_rows:
        conn.executemany("INSERT INTO edits(file_id,path,uuid,ts) VALUES (?,?,?,?)", edit_rows)
    conn.execute("UPDATE files SET size=?, mtime=?, indexed_bytes=?, project_id=?, "
                 "project_name=?, last_ts=COALESCE(?, last_ts) WHERE id=?",
                 (st.st_size, st.st_mtime, st.st_size, project_id, project_name, last_ts, fid))
    return len(rows)


def build_index(source_id: str, *, tool_cap: int = DEFAULT_TOOL_CAP,
                force: bool = False) -> dict:
    """Build or refresh the content index for one source.

    Refuses to start if the projected index would not fit, rather than filling
    the disk and failing partway through.
    """
    if not fts5_available():
        return {"state": "unavailable", "reason": "SQLite build lacks FTS5"}

    from . import search_index as _idx

    with _build_lock:
        needed = estimate_index_bytes(source_id)
        existing = DB_FILE.stat().st_size if DB_FILE.exists() else 0
        ok, free = has_free_space(APP_DATA_DIR, max(0, needed - existing))
        if not ok and not force:
            msg = (f"Needs about {needed / 1e9:.2f} GB; only {free / 1e6:.0f} MB free. "
                   f"Free space, lower the tool cap, or pass force.")
            log.warning("Refusing to build FTS index: %s", msg)
            _progress[source_id] = {"state": "blocked", "reason": msg,
                                    "needed_bytes": needed, "free_bytes": free}
            return _progress[source_id]

        conn = _connect()
        _ensure_schema(conn)

        # A compact {path: (project_id, project_name)} map, not full rows.
        entries = _idx.path_project_map(source_id)
        data_dir = DATA_DIR / SOURCES[source_id]["data_subdir"]
        if source_id in WHOLE_FILE_PARSERS:
            # The metadata index already settled which copy of a chat is listed
            # (Cursor store vs transcript, .json vs .jsonl); index exactly that.
            paths = sorted(Path(p) for p in entries if Path(p).is_file())
        else:
            paths = [f for f, _ in session_files(source_id, data_dir)]
        total = len(paths)
        _progress[source_id] = {"state": "building", "done": 0, "total": total,
                                "messages": 0, "started": time.time()}

        if force:
            conn.execute("DELETE FROM messages WHERE file_id IN "
                         "(SELECT id FROM files WHERE source=?)", (source_id,))
            conn.execute("DELETE FROM files WHERE source=?", (source_id,))
            conn.commit()

        seen, nmsg = set(), 0
        for i, path in enumerate(paths):
            seen.add(str(path))
            pid, pname = entries.get(str(path), ("", ""))
            try:
                nmsg += _index_file(conn, path, source_id, pid, pname, tool_cap)
            except sqlite3.Error:
                log.exception("Indexing failed for %s", path)
            if (i + 1) % 200 == 0:
                conn.commit()
                _progress[source_id].update(done=i + 1, messages=nmsg)
                ok, free = has_free_space(APP_DATA_DIR, 100 * 1024 * 1024, margin=1.0)
                if not ok:
                    conn.commit()
                    msg = f"Stopped at {i + 1}/{total}: only {free / 1e6:.0f} MB free."
                    log.warning(msg)
                    _progress[source_id] = {"state": "blocked", "reason": msg,
                                            "done": i + 1, "total": total}
                    return _progress[source_id]
        conn.commit()

        # Project names can change without the file changing (a Cursor
        # transcript learns its folder from another file), so re-apply them.
        conn.executemany(
            "UPDATE files SET project_id=?, project_name=? WHERE path=? "
            "AND (project_id IS NOT ? OR project_name IS NOT ?)",
            [(pid, pname, path, pid, pname) for path, (pid, pname) in entries.items()])
        conn.commit()

        # Drop rows for files that are gone from the backup.
        rows = conn.execute("SELECT id, path FROM files WHERE source=?", (source_id,)).fetchall()
        stale = [r["id"] for r in rows if r["path"] not in seen]
        for fid in stale:
            conn.execute("DELETE FROM messages WHERE file_id=?", (fid,))
            conn.execute("DELETE FROM edits WHERE file_id=?", (fid,))
            conn.execute("DELETE FROM files WHERE id=?", (fid,))
        conn.commit()

        _progress[source_id] = {"state": "ready", "done": total, "total": total,
                                "messages": nmsg, "db_bytes": DB_FILE.stat().st_size}
        log.info("FTS index %s: %d files, %d new messages, db %.1f MB",
                 source_id, total, nmsg, DB_FILE.stat().st_size / 1048576)
        return _progress[source_id]


def index_status(source_id: str) -> dict:
    if not fts5_available():
        return {"state": "unavailable", "reason": "SQLite build lacks FTS5"}
    prog = _progress.get(source_id)
    if prog and prog.get("state") == "building":
        return prog
    if is_available(source_id):
        conn = _connect()
        row = conn.execute(
            "SELECT count(*) files, (SELECT count(*) FROM messages m JOIN files f "
            "ON f.id=m.file_id WHERE f.source=?) msgs FROM files WHERE source=?",
            (source_id, source_id)).fetchone()
        return {"state": "ready", "files": row["files"], "messages": row["msgs"],
                "db_bytes": DB_FILE.stat().st_size if DB_FILE.exists() else 0}
    if prog:
        return prog
    needed = estimate_index_bytes(source_id)
    ok, free = has_free_space(APP_DATA_DIR, needed)
    return {"state": "not_built", "needed_bytes": needed, "free_bytes": free,
            "fits": ok}


# --------------------------------------------------------------------- query
def build_match(user_query: str, *, prefix_last: bool = True) -> str:
    """Turn arbitrary user text into a safe FTS5 MATCH expression.

    Every term is double-quoted (internal quotes doubled), which makes it a
    literal string and disables the special meaning of . / - : ( ) *. Without
    this, `search.py` — the single most likely query against a coding log —
    raises a syntax error.
    """
    import re
    raw = re.findall(r'"([^"]*)"|(\S+)', user_query or "")
    terms = [(phrase or word) for phrase, word in raw]
    terms = [t for t in terms if t.strip()]
    if not terms:
        return ""
    parts = []
    for i, term in enumerate(terms):
        negate = term.startswith("-") and len(term) > 1
        if negate:
            term = term[1:]
        quoted = '"' + term.replace('"', '""') + '"'
        if prefix_last and i == len(terms) - 1 and not negate and " " not in term:
            quoted += " *"
        parts.append(("NOT " + quoted) if negate else quoted)
    expr = ""
    for piece in parts:
        if piece.startswith("NOT "):
            expr += " " + piece
        else:
            expr += (" AND " if expr else "") + piece
    return expr.strip()


def _snippet_parts(text: str) -> list:
    """Split a snippet marked with \\x01/\\x02 into [{t,hit}] for safe rendering.

    The frontend builds <mark> nodes with createElement from this, so
    highlighting never requires innerHTML with search-derived content.
    """
    out, buf, hit = [], [], False
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\x01":
            if buf:
                out.append({"t": "".join(buf), "hit": hit})
                buf = []
            hit = True
        elif ch == "\x02":
            if buf:
                out.append({"t": "".join(buf), "hit": hit})
                buf = []
            hit = False
        else:
            buf.append(ch)
        i += 1
    if buf:
        out.append({"t": "".join(buf), "hit": hit})
    return out


def search_content(query: str, source_id: str, *, limit: int = 50,
                   project: str | None = None, role: str | None = None,
                   after: str | None = None, before: str | None = None) -> list:
    """Ranked content search, grouped by session."""
    expr = build_match(query)
    if not expr or not is_available(source_id):
        return []

    conn = _connect()
    where = ["f.source = ?"]
    params: list = [source_id]
    if project:
        where.append("f.project_id = ?")
        params.append(project)
    if role:
        where.append("m.role = ?")
        params.append(role)
    if after:
        where.append("m.ts >= ?")
        params.append(after)
    if before:
        where.append("m.ts <= ?")
        params.append(before)

    sql = f"""
      SELECT f.project_id, f.project_name, f.session_id, f.last_ts,
             m.uuid, m.role, m.kind, m.ts, m.msg_idx,
             snippet(fts, 0, char(1), char(2), '…', 12) AS snip_dialog,
             snippet(fts, 1, char(1), char(2), '…', 10) AS snip_tool,
             bm25(fts, 1.0, 0.3) AS rank
      FROM fts
      JOIN messages m ON m.id = fts.rowid
      JOIN files f ON f.id = m.file_id
      WHERE fts MATCH ? AND {' AND '.join(where)}
      ORDER BY rank
      LIMIT ?
    """
    try:
        rows = conn.execute(sql, [expr] + params + [limit * 6]).fetchall()
    except sqlite3.OperationalError:
        log.exception("FTS query failed for %r (expr=%r)", query, expr)
        return []

    sessions: dict = {}
    for r in rows:
        key = (r["project_id"], r["session_id"])
        s = sessions.get(key)
        if s is None:
            s = sessions[key] = {
                "project_id": r["project_id"],
                "project_name": r["project_name"],
                "session_id": r["session_id"],
                "last_ts": r["last_ts"],
                "summary": "",
                "cwd": "",
                "matches": [],
                "match_count": 0,
                "rank": r["rank"],
            }
        s["match_count"] += 1
        if len(s["matches"]) < 3:
            snippet = r["snip_dialog"] or r["snip_tool"] or ""
            s["matches"].append({
                "uuid": r["uuid"],
                "msg_idx": r["msg_idx"],
                "role": r["role"],
                "kind": r["kind"],
                "ts": r["ts"],
                "snippet_parts": _snippet_parts(snippet),
            })
    out = sorted(sessions.values(), key=lambda s: s["rank"])[:limit]

    # Fill in summaries for just these results, rather than loading every row.
    from . import search_index as _idx
    keys = [(s["project_id"], s["session_id"]) for s in out]
    lookup = _idx.summaries_for(source_id, keys)
    for s in out:
        e = lookup.get((s["project_id"], s["session_id"]))
        if e:
            s["summary"] = e["summary"]
            s["cwd"] = e["cwd"]
            s["last_ts"] = s["last_ts"] or e["last_ts"]
    return out


def sessions_touching(path_substring: str, source_id: str | None = None,
                      limit: int = 100) -> list:
    """File-edit archaeology: which sessions edited a file, newest first."""
    if not path_substring or not DB_FILE.exists():
        return []
    conn = _connect()
    sql = ("SELECT e.path, e.uuid, e.ts, f.source, f.project_id, f.project_name, f.session_id "
           "FROM edits e JOIN files f ON f.id = e.file_id WHERE e.path LIKE ?")
    params: list = [f"%{path_substring}%"]
    if source_id:
        sql += " AND f.source = ?"
        params.append(source_id)
    sql += " ORDER BY e.ts DESC LIMIT ?"
    params.append(limit)
    try:
        rows = conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        return []
    return [dict(r) for r in rows]
