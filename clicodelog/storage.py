"""Durable JSON persistence.

Every write in this module is atomic: content goes to a temp file in the same
directory, is fsynced, and is then os.replace()d onto the target. A crash can
therefore leave either the old file or the new one, never a truncated one.

Reads never silently discard data. A file that fails to parse is moved aside
with a timestamp rather than treated as empty, because the previous behaviour
(return [] on JSONDecodeError, then save that empty list back) permanently
destroyed bookmarks and project metadata on the next write.
"""

import json
import logging
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)


def quarantine(path: Path) -> Path | None:
    """Move an unreadable file aside so it can be recovered by hand."""
    if not path.exists():
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = path.with_name(f"{path.name}.corrupt-{stamp}")
    try:
        os.replace(path, dest)
        log.error("Could not parse %s — moved to %s (nothing was discarded)", path, dest)
        return dest
    except OSError:
        log.exception("Could not quarantine %s; leaving it untouched", path)
        return None


def load_json(path: Path, default):
    """Read JSON, or return default. A corrupt file is quarantined, not dropped."""
    if not path.exists():
        return default
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, UnicodeDecodeError):
        quarantine(path)
        return default
    except OSError:
        log.exception("Could not read %s", path)
        return default


def write_json(path: Path, data, *, keep_backup: bool = False, indent: int | None = None) -> bool:
    """Atomically write JSON to path. Returns True on success.

    keep_backup copies the current file to <name>.bak first. Use it for data the
    user authored and cannot regenerate.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    if keep_backup and path.exists():
        try:
            shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
        except OSError:
            log.warning("Could not refresh backup for %s", path, exc_info=True)

    return write_text_atomic(path, json.dumps(data, indent=indent))


def write_text_atomic(path: Path, text: str) -> bool:
    """Atomically write UTF-8 text to path (temp file, fsync, os.replace).

    The encoding is explicit: without it Python uses the locale's, which on
    Windows is cp1252 — non-Latin text then fails to write at all.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_name = None
    try:
        # Same directory, so os.replace stays on one filesystem and is atomic.
        fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
        tmp_name = None
        return True
    except OSError:
        log.exception("Could not write %s — previous contents left intact", path)
        return False
    finally:
        if tmp_name and os.path.exists(tmp_name):
            try:
                os.unlink(tmp_name)   # our own temp file only, never user data
            except OSError:
                pass


def has_free_space(path: Path, needed_bytes: int, *, margin: float = 1.2) -> tuple[bool, int]:
    """Is there room for needed_bytes (plus margin) on path's filesystem?

    Returns (ok, free_bytes). Used to refuse work that would fill the disk
    rather than failing halfway through it.
    """
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        free = shutil.disk_usage(probe).free
    except OSError:
        return True, -1                    # can't tell; don't block the user
    return free >= needed_bytes * margin, free
