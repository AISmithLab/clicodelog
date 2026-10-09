"""Backup of VS Code (GitHub Copilot Chat) chat sessions.

Copilot Chat keeps one file per chat under the editor's user directory:

    workspaceStorage/<hash>/chatSessions/<id>.json    (older: one JSON object)
    workspaceStorage/<hash>/chatSessions/<id>.jsonl   (newer: a mutation log)
    workspaceStorage/<hash>/workspace.json            (which folder <hash> is)
    globalStorage/emptyWindowChatSessions/<id>.json[l] (chats with no folder open)

Only those files are copied. The rest of workspaceStorage is extension state —
often gigabytes of it — that has nothing to do with chat history. The layout in
the backup mirrors the source, so the same parser reads either:

    vscode/<prefix><hash>/chatSessions/<id>.jsonl
    vscode/<prefix><hash>/workspace.json
    vscode/<prefix>_empty-window/chatSessions/<id>.json
"""

from pathlib import Path

from .editors import safe_name, vscode_user_dirs
from .logging_setup import get_logger
from .storage import keep_superseded

log = get_logger(__name__)

EMPTY_WINDOW = "_empty-window"


def _request_ids(path: Path) -> set:
    from .parsers.vscode_log import load_session
    try:
        reqs = load_session(path).get("requests") or []
    except (OSError, ValueError):
        return set()
    return {r.get("requestId") for r in reqs if isinstance(r, dict)} - {None}


def _copy_chats(src: Path, dest: Path, stats: dict, copy) -> None:
    """Copy each chat file, never letting a rewrite drop turns from the backup.

    Copilot rewrites a chat file in place: removing or undoing a request, or
    compacting the .jsonl log. A plain overwrite would lose turns that only
    the backup still had, so the old copy is kept as a superseded .bak first.
    """
    try:
        files = sorted(f for f in src.iterdir() if f.is_file())
    except OSError as e:
        log.warning("Could not list %s: %s", src, e)
        return
    dest.mkdir(parents=True, exist_ok=True)
    for f in files:
        target = dest / f.name
        try:
            changed = target.exists() and (target.stat().st_size, target.stat().st_mtime) \
                != (f.stat().st_size, f.stat().st_mtime)
        except OSError:
            changed = False
        if changed and f.suffix in (".json", ".jsonl") and _request_ids(target) - _request_ids(f):
            if keep_superseded(target) is None:
                stats["failed"] += 1
                continue
            log.info("VS Code chat %s lost turns; kept the old copy", f.stem)
        copy(f, target, stats)


def source_roots() -> list[Path]:
    return [d for d, _ in vscode_user_dirs()]


def sync(dest_dir: Path, stats: dict, copy) -> bool:
    """Copy chat files from every installed VS Code flavour. `copy` is the
    additive, never-deleting copier from sync.py."""
    found = False
    for user_dir, prefix in vscode_user_dirs():
        ws_root = user_dir / "workspaceStorage"
        if ws_root.is_dir():
            found = True
            try:
                workspaces = sorted(p for p in ws_root.iterdir() if p.is_dir())
            except OSError:
                workspaces = []
            for ws in workspaces:
                chats = ws / "chatSessions"
                if not chats.is_dir():
                    continue
                target = dest_dir / (prefix + safe_name(ws.name))
                _copy_chats(chats, target / "chatSessions", stats, copy)
                if (ws / "workspace.json").is_file():
                    copy(ws / "workspace.json", target / "workspace.json", stats)

        empty = user_dir / "globalStorage" / "emptyWindowChatSessions"
        if empty.is_dir():
            found = True
            _copy_chats(empty, dest_dir / (prefix + EMPTY_WINDOW) / "chatSessions", stats, copy)
    return found
