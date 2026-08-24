import base64
import json
from pathlib import Path


def encode_path_id(path: str) -> str:
    return base64.urlsafe_b64encode(path.encode()).decode().rstrip("=")


def decode_path_id(encoded_id: str) -> str:
    padding = 4 - len(encoded_id) % 4
    if padding != 4:
        encoded_id += "=" * padding
    return base64.urlsafe_b64decode(encoded_id.encode()).decode()


def is_safe_id(value: str) -> bool:
    """Reject ids that could escape the data directory when joined into a path.

    Route parameters land directly in `data_dir / project_id / session_id`.
    Containment used to be accidental — a consequence of the route shape rather
    than any check — so this makes it explicit before the ids are ever joined.
    """
    if not value or not isinstance(value, str):
        return False
    if value in (".", ".."):
        return False
    return not any(sep in value for sep in ("/", "\\", "\x00"))


def safe_child(base: Path, *parts: str) -> Path | None:
    """Join parts onto base, or None if the result escapes base."""
    for p in parts:
        if not is_safe_id(p):
            return None
    candidate = base.joinpath(*parts)
    try:
        candidate.resolve().relative_to(base.resolve())
    except (ValueError, OSError):
        return None
    return candidate


def get_codex_cwd(session_file) -> str | None:
    """Extract cwd from a Codex session file for project grouping."""
    try:
        with open(session_file, "r", errors="ignore") as f:
            for _ in range(50):
                line = f.readline()
                if not line:
                    break
                try:
                    entry = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if isinstance(entry, dict) and entry.get("type") == "session_meta":
                    payload = entry.get("payload")
                    if isinstance(payload, dict):
                        return payload.get("cwd", "")
    except OSError:
        pass
    return None


def get_gemini_project_hash(session_file) -> str | None:
    """Extract projectHash from a Gemini session file.

    Gemini CLI moved from one whole-file JSON object to JSON Lines, where the
    first line is the session header. The old json.load() of the entire file
    raised on every current file, which is part of why this source went dark.
    """
    try:
        with open(session_file, "r", errors="ignore") as f:
            for _ in range(5):
                line = f.readline()
                if not line:
                    break
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if isinstance(entry, dict) and entry.get("projectHash"):
                    return entry["projectHash"]
    except OSError:
        pass
    return None
