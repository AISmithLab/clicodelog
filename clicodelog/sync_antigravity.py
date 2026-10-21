"""Back up encrypted Antigravity histories and cache readable local snapshots.

Encrypted .pb/.db files are preserved even with the IDE closed. Readable
trajectory JSON comes from an existing sidecar or the running language server.
Nothing is written into Antigravity's own directories.
"""

import hashlib
import json
import os
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

from . import antigravity_client
from .agent_paths import antigravity_roots
from .cursor_store import open_readonly
from .editors import safe_name
from .logging_setup import get_logger
from .storage import has_free_space, keep_superseded, write_json
from .utils import is_safe_id

log = get_logger(__name__)


def source_roots() -> list[Path]:
    return antigravity_roots()


def _load(path):
    try:
        with open(path, encoding="utf-8-sig") as fh:
            value = json.load(fh)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _steps(value):
    trajectory = value.get("trajectory", value)
    return trajectory.get("steps") if isinstance(trajectory, dict) else None


def _snapshot(path, value, stats):
    steps = _steps(value)
    if not isinstance(steps, list) or not steps:
        return False
    old = _load(path) if path.exists() else {}
    if old == value:
        stats["skipped"] += 1
        return True
    encoded_size = len(json.dumps(value).encode("utf-8"))
    if not has_free_space(path.parent, encoded_size)[0]:
        stats["skipped_no_space"] += 1
        return False
    previous = _steps(old)
    if isinstance(previous, list) and previous != steps[:len(previous)]:
        if keep_superseded(path) is None:
            stats["failed"] += 1
            return False
    if write_json(path, value):
        stats["copied"] += 1
        return True
    stats["failed"] += 1
    return False


def _raw(src, target, stats, copy):
    """Retain earlier encrypted versions; SQLite is snapshotted with its WAL."""
    st = src.stat()
    wal = src.with_name(src.name + "-wal")
    wal_size, stamp = 0, st.st_mtime
    if src.suffix == ".db":
        try:
            wal_stat = wal.stat()
            wal_size = wal_stat.st_size
            if wal_size:
                stamp = max(stamp, wal_stat.st_mtime)
        except FileNotFoundError:
            pass
    if target.exists() and target.stat().st_mtime >= stamp and \
            (src.suffix == ".db" or target.stat().st_size == st.st_size):
        stats["skipped"] += 1
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    # The SQLite snapshot includes committed WAL pages. Reserve space for the
    # new temporary database (and its journal) plus the archive of the existing
    # target; free space already excludes the target itself.
    archive_size = target.stat().st_size if target.exists() else 0
    needed = (st.st_size + wal_size) * 2 + archive_size
    if not has_free_space(target.parent, needed)[0]:
        stats["skipped_no_space"] += 1
        return
    if target.exists() and keep_superseded(target) is None:
        stats["failed"] += 1
        return
    if src.suffix != ".db":
        copy(src, target, stats)
        return
    fd, tmp = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
    os.close(fd)
    try:
        source = open_readonly(src)
        if source is None:
            raise sqlite3.OperationalError("History database unavailable")
        with closing(source), closing(sqlite3.connect(tmp)) as dest:
            source.backup(dest)
        os.utime(tmp, (stamp, stamp))
        os.replace(tmp, target)
        stats["copied"] += 1
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def sync(dest_dir: Path, stats: dict, copy) -> bool:
    roots = [p for p in source_roots() if p.is_dir()]
    found = bool(roots)
    pending, refreshed = set(), set()
    for root in roots:
        key = safe_name(root.name) + "-" + hashlib.sha256(str(root).encode()).hexdigest()[:10]
        conversations = root / "conversations"
        if not conversations.is_dir():
            continue
        for src in sorted(conversations.iterdir()):
            if not src.is_file():
                continue
            if src.suffix in (".pb", ".db"):
                sid = src.stem
                if not is_safe_id(sid):
                    continue
                pending.add(sid)
                try:
                    _raw(src, dest_dir / "raw" / key / src.name, stats, copy)
                except (OSError, sqlite3.Error):
                    log.warning("Could not back up Antigravity history %s", src.name)
                    stats["failed"] += 1
            elif src.suffix == ".json":
                sid = src.name.removesuffix(".trajectory.json").removesuffix(".json")
                target = dest_dir / "sessions" / (sid + ".json")
                # A sidecar can be older than a previous API export. Never
                # replace a newer cached trajectory with that stale sidecar.
                if is_safe_id(sid) and (not target.exists() or src.stat().st_mtime > target.stat().st_mtime):
                    _snapshot(target, _load(src), stats)
    try:
        clients = antigravity_client.discover()
    except (OSError, ValueError):
        clients = []
        log.warning("Could not discover Antigravity's local history endpoint")
    for client in clients:
        try:
            result = client.call("GetAllCascadeTrajectories")
            summaries = result.get("trajectorySummaries", {})
            if not isinstance(summaries, dict):
                continue
        except (OSError, ValueError):
            continue
        found = True
        # A server can decrypt old .pb histories omitted from its summaries.
        for sid in sorted(set(summaries) | pending):
            if not is_safe_id(sid) or sid in refreshed:
                continue
            try:
                trajectory = client.fetch(sid)
                snapshot = {"trajectory": trajectory, "summary": summaries.get(sid, {})}
                if _snapshot(dest_dir / "sessions" / (sid + ".json"), snapshot, stats):
                    refreshed.add(sid)
            except (OSError, ValueError):
                log.debug("Antigravity history %s unavailable from this server", sid)
    missing = [sid for sid in pending if not (dest_dir / "sessions" / (sid + ".json")).is_file()]
    warning = None
    if missing:
        warning = (f"{len(missing)} Antigravity histories await readable snapshots. "
                   "Open Antigravity and sync again to import their conversations.")
    if found:
        write_json(dest_dir / "history-status.json", {"warning": warning})
    return found


def source_warning(dest_dir: Path) -> str | None:
    value = _load(dest_dir / "history-status.json").get("warning")
    return value if isinstance(value, str) else None
