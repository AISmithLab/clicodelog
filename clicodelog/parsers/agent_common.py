"""Small shape guards shared by agent history readers."""

import json


def obj(value) -> dict:
    return value if isinstance(value, dict) else {}


def records(value) -> list:
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def text(value) -> str:
    return value if isinstance(value, str) else ""


def arguments(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value if value is not None else {}


def output(value) -> str | None:
    if value is None or isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, indent=2)


def number(value) -> int:
    try:
        return max(0, int(value or 0))
    except (ValueError, TypeError, OverflowError):
        return 0


def message(role, content, stamp, uid=None, model=None) -> dict:
    return {"role": role, "content": content, "timestamp": text(stamp) or None,
            "uuid": text(uid) or None, "model": text(model) or None,
            "thinking": None, "tool_uses": None, "usage": None, "edited_files": None}
