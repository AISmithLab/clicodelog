"""cursor-agent CLI chats: read-only export, parsing, listing, dedupe, export."""

import base64
import hashlib
import json

from fastapi.testclient import TestClient

from clicodelog import fts, search_index, sessions, sync
from clicodelog.app import app
from clicodelog.conversation import get_conversation
from clicodelog.editors import cursor_home, cursor_slug

from editor_fixtures import (CLI_CHAT, CLI_CWD, cli_messages, make_cursor_cli, transcript_lines,
                             write_cli_store, write_transcripts)

SLUG = cursor_slug(CLI_CWD)          # "tmp-proj"


def _export(env):
    hits = list((env["data"] / "cursor" / "cli").glob(f"*/{CLI_CHAT}.jsonl"))
    assert len(hits) == 1, hits
    return hits[0]


def _tree(chats):
    return {str(p.relative_to(chats)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(chats.rglob("*")) if p.is_file()}


def test_export_is_read_only_and_keeps_every_row(isolated):
    make_cursor_cli(isolated["cursor_chats"])
    before = _tree(isolated["cursor_chats"])
    assert sync.sync_data("cursor", silent=True)
    assert _tree(isolated["cursor_chats"]) == before, \
        "no file may be written, created or touched under ~/.cursor/chats"

    recs = [json.loads(x) for x in _export(isolated).read_text(encoding="utf-8").splitlines()]
    head, rows = recs[0], recs[1:]
    assert head["kind"] == "cli" and head["workspace"] == CLI_CWD
    assert head["storeMeta"]["0"]["lastUsedModel"] == "gpt-5", "hex-encoded meta is decoded"
    assert len(rows) == len(cli_messages()), "binary rows are kept, not dropped"
    binary = [r for r in rows if "b64" in r]
    assert len(binary) == 1 and base64.b64decode(binary[0]["b64"]).endswith(b"protobuf row")


def test_checkpointed_store_without_shm_is_readable(isolated):
    """An idle chat's -wal/-shm are gone; plain mode=ro cannot open that."""
    chat = make_cursor_cli(isolated["cursor_chats"])
    assert not (chat / "store.db-shm").exists()
    sync.sync_data("cursor", silent=True)
    assert _export(isolated).exists()


def test_chat_is_parsed_into_turns(isolated):
    make_cursor_cli(isolated["cursor_chats"])
    sync.sync_data("cursor", silent=True)
    conv = get_conversation(SLUG, CLI_CHAT, "cursor", include_results=True)
    msgs = conv["messages"]
    assert conv["summaries"] == ["Fix the widget"]
    assert [(m["role"], m["content"]) for m in msgs] == [
        ("user", "please fix the widget"),
        ("assistant", "Editing widget.ts."),
        ("assistant", "Widget patched."),
    ], "system prompt, environment preamble and injected skills are not turns"
    first = msgs[1]
    assert first["thinking"] == "I will edit the file."
    assert [(t["name"], t["result"]) for t in first["tool_uses"]] == [
        ("Write", "ok"), ("Shell", "3 passed")], "tool-only replies fold in; v4 and v5 shapes"
    assert first["tool_uses"][1]["input"] == {"command": "npm test"}
    assert first["edited_files"] == ["/tmp/proj/widget.ts"]
    assert first["model"] == "gpt-5"
    assert {m["timestamp"] for m in msgs} == {"2026-09-15T12:49:00Z"}, \
        "replies carry their prompt's time"
    assert conv["meta"]["cwd"] == CLI_CWD


def test_listing_skips_subagents_empty_and_broken_chats(isolated):
    make_cursor_cli(isolated["cursor_chats"])
    sync.sync_data("cursor", silent=True)
    projects = search_index.projects_for_source("cursor")
    assert [(p["id"], p["name"]) for p in projects] == [(SLUG, CLI_CWD)]
    page = sessions.get_sessions(SLUG, "cursor")
    assert [s["id"] for s in page["sessions"]] == [CLI_CHAT]
    s = page["sessions"][0]
    assert s["summary"] == "Fix the widget" and s["message_count"] == 3
    sub = list((isolated["data"] / "cursor" / "cli").glob("*/_subagents/*.jsonl"))
    assert len(sub) == 1, "a sub-agent run is backed up even though it is not listed"


def test_unchanged_chat_is_not_rewritten_and_growth_is_picked_up(isolated):
    chat = make_cursor_cli(isolated["cursor_chats"])
    sync.sync_data("cursor", silent=True)
    stamp = _export(isolated).stat().st_mtime_ns
    sync.sync_data("cursor", silent=True)
    assert _export(isolated).stat().st_mtime_ns == stamp

    write_cli_store(chat, [{"role": "user", "content": [{"type": "text", "text":
                     "<user_query>now add a test</user_query>"}]}])
    sync.sync_data("cursor", silent=True)
    conv = get_conversation(SLUG, CLI_CHAT, "cursor")
    assert conv["messages"][-1]["content"] == "now add a test"
    assert not list(_export(isolated).parent.glob("*.bak")), "growth loses nothing"


def test_live_store_with_a_wal_is_read_without_the_immutable_shortcut(isolated):
    """With a -wal present, immutable=1 would silently skip its writes."""
    chat = make_cursor_cli(isolated["cursor_chats"])
    import sqlite3
    holder = sqlite3.connect(chat / "store.db")          # the CLI, still running
    holder.execute("PRAGMA journal_mode=wal")
    holder.execute("PRAGMA wal_autocheckpoint=0")
    holder.execute("INSERT INTO blobs VALUES (?, ?)", ("f" * 64, json.dumps(
        {"role": "user", "content": [{"type": "text", "text": "<user_query>in the wal</user_query>"}]})))
    holder.commit()
    try:
        assert (chat / "store.db-wal").exists()
        sync.sync_data("cursor", silent=True)
        conv = get_conversation(SLUG, CLI_CHAT, "cursor")
        assert conv["messages"][-1]["content"] == "in the wal"
    finally:
        holder.close()


def test_cli_chat_wins_over_its_transcript(isolated):
    make_cursor_cli(isolated["cursor_chats"])
    write_transcripts(isolated["cursor_projects"], SLUG,
                      {CLI_CHAT: transcript_lines("lossy copy"), "t-2": transcript_lines("other")})
    sync.sync_data("cursor", silent=True)
    page = sessions.get_sessions(SLUG, "cursor")
    assert sorted(s["id"] for s in page["sessions"]) == sorted([CLI_CHAT, "t-2"])
    assert next(s for s in page["sessions"] if s["id"] == CLI_CHAT)["summary"] == "Fix the widget"
    assert [p["name"] for p in search_index.projects_for_source("cursor")] == [CLI_CWD], \
        "the transcript's project takes its folder from the CLI chat"


def test_search_and_export(isolated):
    make_cursor_cli(isolated["cursor_chats"])
    sync.sync_data("cursor", silent=True)
    assert fts.build_index("cursor")["state"] == "ready"
    assert [h["session_id"] for h in fts.search_content("widget", "cursor")] == [CLI_CHAT]
    assert {t["session_id"] for t in fts.sessions_touching("widget.ts", "cursor")} == {CLI_CHAT}

    client = TestClient(app)
    md = client.get(f"/api/projects/{SLUG}/sessions/{CLI_CHAT}/export",
                    params={"source": "cursor", "fmt": "md"})
    assert md.status_code == 200
    for needle in ("please fix the widget", "Tool: <code>Shell</code>", "3 passed",
                   "<summary>Thinking</summary>", "Widget patched."):
        assert needle in md.text, needle


def test_cursor_home_per_os(tmp_path):
    home = tmp_path
    assert cursor_home("darwin", {}, home) == home / ".cursor"
    assert cursor_home("win32", {}, home) == home / ".cursor"
    assert cursor_home("linux", {}, home) == home / ".cursor"
    assert cursor_home("linux", {"CURSOR_CONFIG_DIR": "/cfg"}, home).as_posix().endswith("cfg")
    xdg = tmp_path / "xdg"
    assert cursor_home("linux", {"XDG_CONFIG_HOME": str(xdg)}, home) == home / ".cursor", \
        "XDG only when Cursor actually uses it"
    (xdg / "cursor").mkdir(parents=True)
    assert cursor_home("linux", {"XDG_CONFIG_HOME": str(xdg)}, home) == xdg / "cursor"
    assert cursor_home("darwin", {"XDG_CONFIG_HOME": str(xdg)}, home) == home / ".cursor"
