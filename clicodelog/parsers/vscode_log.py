"""Loading a VS Code chat session from either on-disk format.

* `<id>.json` — one JSON document.
* `<id>.jsonl` — a mutation log. Line one is {"kind": 0, "v": <session>}; each
  later line edits it at a key path `k`:
      kind 1  set      obj[k] = v
      kind 2  push     arr[k].extend(v); with "i", truncate to i first
      kind 3  delete   del obj[k]
  Replaying every line yields the same object the .json file would hold. A
  torn final line (sync copying mid-write) just stops the replay there.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

# What a record of an unexpected shape raises while being normalised. Parsers
# catch these per record — like the line-based parsers skip a bad line — so
# one odd record never drops the whole chat.
SHAPE_ERRORS = (AttributeError, TypeError, KeyError, IndexError, ValueError)


def iso_ms(ms) -> str | None:
    """Epoch milliseconds -> the ISO-8601 UTC string the other sources use."""
    if not isinstance(ms, (int, float)) or ms <= 0:
        return None
    try:
        dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _apply(state, entry: dict):
    kind = entry.get("kind")
    if kind == 0:
        return entry.get("v")
    path = entry.get("k")
    if not isinstance(path, list) or not path or state is None:
        return state
    obj = state
    for key in path[:-1]:
        obj = obj[key]
    last = path[-1]
    if kind == 1:
        obj[last] = entry.get("v")
    elif kind == 2:
        arr = obj.get(last) if isinstance(obj, dict) else obj[last]
        if arr is None:
            arr = obj[last] = []
        if isinstance(entry.get("i"), int):
            del arr[entry["i"]:]
        arr.extend(entry.get("v") or [])
    elif kind == 3:
        if isinstance(obj, dict):
            obj.pop(last, None)
        else:
            del obj[last]
    return state


def load_session(path: Path) -> dict:
    """The session object, from either on-disk format."""
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        if path.suffix == ".json":
            try:
                data = json.load(fh)
            except ValueError:
                return {}
            return data if isinstance(data, dict) else {}
        state = None
        for line in fh:
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                break                            # torn tail; keep what replayed
            if not isinstance(entry, dict):
                continue
            try:
                state = _apply(state, entry)
            except (KeyError, IndexError, TypeError, AttributeError):
                continue                         # an edit to a path we never saw
    return state if isinstance(state, dict) else {}
