"""Windowed reads of a Claude Code session, so a page costs page-sized memory.

Twelve files hold 61% of this corpus, and the largest is 966 MB / 89,823
messages. Parsing one whole session to show fifty messages peaked at 604 MB of
RSS and produced a 138 MB JSON body.

Instead we build a line-offset table once per (path, size, mtime): the byte
offset of every line that yields a message. The table is an array of 64-bit
ints — about 700 KB for 90,000 messages — so a page then seeks directly to its
own lines and parses only those. Opening a session is O(file) once for the
scan, and every page after that is O(page).
"""

import json
import threading
from array import array
from collections import OrderedDict
from pathlib import Path

from .logging_setup import get_logger
from .parsers.claude import build_message

log = get_logger(__name__)

_MAX_CACHED_TABLES = 6
_tables: "OrderedDict[tuple, dict]" = OrderedDict()
_lock = threading.Lock()


def _get_cached(key):
    with _lock:
        if key in _tables:
            _tables.move_to_end(key)
            return _tables[key]
    return None


def _put_cached(key, value):
    with _lock:
        _tables[key] = value
        _tables.move_to_end(key)
        while len(_tables) > _MAX_CACHED_TABLES:
            _tables.popitem(last=False)


def clear_cache() -> None:
    with _lock:
        _tables.clear()


def build_table(path: Path) -> dict:
    """Scan once, remembering where each message-bearing line starts.

    Nothing from the file is retained except offsets and the (small) summaries,
    so this stays flat in memory regardless of file size.
    """
    offsets = array("q")
    summaries: list = []
    pos = 0
    with open(path, "rb") as fh:
        for line in fh:
            start = pos
            pos += len(line)
            if not line.strip():
                continue
            # Cheap prefilter: skip json.loads on lines that cannot be messages.
            # The type field appears early in every record these tools write.
            head = line[:200]
            if b'"type"' not in head:
                continue
            try:
                entry = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if not isinstance(entry, dict):
                continue
            etype = entry.get("type")
            if etype == "summary":
                s = entry.get("summary")
                if isinstance(s, str):
                    summaries.append(s)
            elif etype in ("user", "assistant"):
                offsets.append(start)
    return {"offsets": offsets, "summaries": summaries}


def table_for(path: Path, size: int, mtime: float) -> dict:
    key = (str(path), size, mtime)
    table = _get_cached(key)
    if table is None:
        table = build_table(path)
        _put_cached(key, table)
    return table


def read_window(path: Path, size: int, mtime: float, offset: int,
                limit: int | None, tail: bool = False) -> tuple[list, int, list, int]:
    """Return (messages, total, summaries, start) for one page.

    tail=True reads the LAST `limit` messages. The viewer defaults to
    newest-first, so it needs the end of the conversation, not the beginning.
    """
    table = table_for(path, size, mtime)
    offsets = table["offsets"]
    total = len(offsets)

    if tail and limit is not None:
        start = max(0, total - limit)
    else:
        start = max(0, min(offset, total))
    end = total if limit is None else min(start + max(0, limit), total)

    messages = []
    if start < end:
        with open(path, "rb") as fh:
            for i in range(start, end):
                fh.seek(offsets[i])
                line = fh.readline()
                try:
                    entry = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if not isinstance(entry, dict):
                    continue
                try:
                    # tool_results is None: the viewer never renders tool output,
                    # and resolving it would require reading the whole file.
                    m = build_message(entry, None)
                except (AttributeError, TypeError):
                    continue
                if m:
                    messages.append(m)

    return messages, total, table["summaries"], start
