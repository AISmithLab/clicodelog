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

EMPTY_WINDOW = "_empty-window"


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
                copy(chats, target / "chatSessions", stats)
                if (ws / "workspace.json").is_file():
                    copy(ws / "workspace.json", target / "workspace.json", stats)

        empty = user_dir / "globalStorage" / "emptyWindowChatSessions"
        if empty.is_dir():
            found = True
            copy(empty, dest_dir / (prefix + EMPTY_WINDOW) / "chatSessions", stats)
    return found
