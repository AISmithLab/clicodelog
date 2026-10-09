"""Where the editor sources' session files are, and which project each is in.

Backup layout (see sync_vscode.py / sync_cursor.py):

    vscode/<workspace>/chatSessions/<id>.json[l]       project = <workspace>
    cursor/composers/<workspace>/<id>.jsonl            project = slug of its folder
    cursor/cli/<workspace>/<id>.jsonl                  (CLI agent chats) likewise
    cursor/transcripts/<slug>/<id>/<id>.jsonl          project = <slug>
    cursor/transcripts/<slug>/<id>.jsonl               (older flat layout)
    cursor/transcripts/<slug>/<id>/subagents/*.jsonl   sub-agents of <id>

Cursor projects are keyed by the same slug Cursor uses for ~/.cursor/projects,
so a workspace's store chats and its agent transcripts land in one project. A
chat present in both is listed once, from the store, which is the richer copy.
"""

import json
from pathlib import Path

from .editors import cursor_slug
from .sync_vscode import EMPTY_WINDOW

NO_WORKSPACE = "_no-workspace"


def session_files(source_id: str, data_dir: Path):
    """Yield (file, project_dir) for each session of an editor source."""
    if source_id == "vscode":
        for ws in sorted(p for p in data_dir.iterdir() if p.is_dir()):
            chats = ws / "chatSessions"
            if chats.is_dir():
                for f in sorted(chats.iterdir()):
                    if f.suffix in (".json", ".jsonl") and f.is_file():
                        yield f, ws
        return

    stored = set()
    for kind in ("composers", "cli"):
        if (data_dir / kind).is_dir():
            # */*.jsonl leaves out cli/<ws>/_subagents/, kept but not listed.
            for f in sorted((data_dir / kind).glob("*/*.jsonl")):
                if f.stem not in stored:
                    stored.add(f.stem)
                    yield f, f.parent
    transcripts = data_dir / "transcripts"
    if transcripts.is_dir():
        for slug in sorted(p for p in transcripts.iterdir() if p.is_dir()):
            for f in sorted(slug.rglob("*.jsonl")):
                if f.stem in stored and "subagents" not in f.relative_to(slug).parts:
                    continue                      # the store's copy is listed instead
                yield f, slug


def _header(path: Path) -> dict:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            head = json.loads(fh.readline() or "null")
        return head if isinstance(head, dict) else {}
    except (OSError, ValueError):
        return {}


def slug_folders(data_dir: Path) -> dict:
    """slug -> workspace folder, learned from the store exports' headers.

    Transcript slugs cannot be decoded (punctuation all collapses to "-"), but a
    folder can be encoded and compared.
    """
    out = {}
    for f in [*(data_dir / "composers").glob("*/*.jsonl"), *(data_dir / "cli").glob("*/*.jsonl")]:
        folder = _header(f).get("workspace")
        if folder:
            out.setdefault(cursor_slug(folder), folder)
    return out


def subagent_counts(data_dir: Path) -> dict:
    """session id -> number of sub-agent transcripts, from one walk.

    A store chat's sub-agents live with its transcript, in another directory,
    so this is computed once per refresh rather than globbed per chat.
    """
    out: dict = {}
    for f in (data_dir / "transcripts").glob("*/*/subagents/*.jsonl"):
        parent = f.parent.parent.name
        out[parent] = out.get(parent, 0) + 1
    return out


def project_for(source_id: str, f: Path, project_dir: Path, info: dict,
                folders: dict, subs: dict) -> tuple:
    """(project_id, project_name, parent_session, subagent_count_override)."""
    if source_id == "vscode":
        name = info["cwd"] or ("(no folder open)" if project_dir.name.endswith(EMPTY_WINDOW)
                               else project_dir.name)
        return project_dir.name, name, None, None

    if project_dir.parent.name in ("composers", "cli"):
        cwd = info["cwd"]
        pid = cursor_slug(cwd) if cwd else NO_WORKSPACE
        return pid, cwd or "(no workspace)", None, subs.get(f.stem, 0)

    rel = f.relative_to(project_dir).parts
    parent = rel[rel.index("subagents") - 1] if "subagents" in rel[1:] else None
    folder = folders.get(project_dir.name)
    if folder and not info["cwd"]:
        info["cwd"] = folder
    return project_dir.name, folder or project_dir.name, parent, None


def refresh_derived(conn, data_dir: Path, folders: dict, subs: dict) -> None:
    """Re-apply the values a Cursor row takes from OTHER files.

    The index only rescans a file whose own size/mtime changed. A transcript
    indexed before any store export named its folder would otherwise keep the
    raw slug as its project name forever, and a chat whose sub-agents arrived
    later would never show them.
    """
    prefix = str(data_dir / "transcripts")
    for slug, folder in folders.items():
        conn.execute(
            "UPDATE sessions SET project_name=?, cwd=CASE WHEN cwd IS NULL OR cwd='' "
            "THEN ? ELSE cwd END WHERE source='cursor' AND project_id=? "
            "AND substr(path, 1, ?)=? AND project_name IS NOT ?",
            (folder, folder, slug, len(prefix), prefix, folder))
    stale = [(subs.get(r[0], 0), r[1]) for r in conn.execute(
        "SELECT session_id, path, subagent_count FROM sessions "
        "WHERE source='cursor' AND parent_session IS NULL")
        if (r[2] or 0) != subs.get(r[0], 0)]
    conn.executemany("UPDATE sessions SET subagent_count=? WHERE path=?", stale)
