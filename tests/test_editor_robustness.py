"""Regression tests for the code-review findings on the editor sources."""

import json
import os
import sqlite3
import sys

import pytest

from clicodelog import conversation, fts, parsers, search_index, sessions, sync
from clicodelog.conversation import get_conversation
from clicodelog.editors import cursor_slug
from clicodelog.parsers.cursor import parse_cursor_conversation
from clicodelog.parsers.vscode import parse_vscode_conversation

from editor_fixtures import (FOLDER, WS_HASH, bubble, cursor_conversation, make_cursor_cli,
                             make_vscode, transcript_lines, vscode_request, vscode_session,
                             write_cursor_store, write_transcripts)

SLUG = cursor_slug(FOLDER)


def test_values_taken_from_other_files_refresh_on_unchanged_rows(isolated):
    """A transcript indexed before any store export named its folder kept the
    raw slug forever; sub-agents arriving later never showed."""
    write_transcripts(isolated["cursor_projects"], SLUG, {"t-1": transcript_lines("first")})
    sync.sync_data("cursor", silent=True)
    assert [p["name"] for p in search_index.projects_for_source("cursor")] == [SLUG]

    write_cursor_store(isolated["cursor_user"], {"c-main": cursor_conversation()})
    sync.sync_data("cursor", silent=True)
    names = {p["id"]: p["name"] for p in search_index.projects_for_source("cursor")}
    assert names == {SLUG: FOLDER}
    rows = {r["session_id"]: r for r in search_index.sessions_for_project("cursor", SLUG)}
    assert rows["t-1"]["project_name"] == FOLDER and rows["t-1"]["cwd"] == FOLDER

    write_transcripts(isolated["cursor_projects"], SLUG, {},
                      subagents={"c-main": {"agent-1": transcript_lines("helper")}})
    sync.sync_data("cursor", silent=True)
    page = sessions.get_sessions(SLUG, "cursor")
    assert next(s for s in page["sessions"] if s["id"] == "c-main")["subagent_count"] == 1


def test_a_file_that_failed_to_index_is_retried(isolated, monkeypatch):
    make_vscode(isolated["vscode_user"])
    sync.sync_data("vscode", silent=True)
    real = parsers.WHOLE_FILE_PARSERS["vscode"]

    def flaky(path, sid):
        if sid == "sess-log":
            raise ValueError("copied mid-write")
        return real(path, sid)

    monkeypatch.setitem(parsers.WHOLE_FILE_PARSERS, "vscode", flaky)
    fts.build_index("vscode")
    assert "sess-log" not in {h["session_id"] for h in fts.search_content("日本語", "vscode")}

    monkeypatch.setitem(parsers.WHOLE_FILE_PARSERS, "vscode", real)
    fts.build_index("vscode")
    assert "sess-log" in {h["session_id"] for h in fts.search_content("日本語", "vscode")}


def test_same_length_bubble_edit_is_backed_up(isolated):
    convo = cursor_conversation()
    write_cursor_store(isolated["cursor_user"], {"c-main": convo})
    sync.sync_data("cursor", silent=True)
    db = isolated["cursor_user"] / "globalStorage" / "state.vscdb"
    b6 = dict(convo[5], text="Renamed `f` to `h`.")           # same length as before
    conn = sqlite3.connect(db)
    conn.execute("UPDATE cursorDiskKV SET value=? WHERE key='bubbleId:c-main:b6'",
                 (json.dumps(b6),))
    conn.commit()
    conn.close()
    # An in-place UPDATE that keeps the length and leaves composerData alone is
    # invisible to the hourly check, which never reads bubble content...
    sync.sync_data("cursor", silent=True)
    assert get_conversation(SLUG, "c-main", "cursor")["messages"][-1]["content"] == \
        "Renamed `f` to `g`."
    # ...and is caught by the daily content verification.
    marker = isolated["data"] / "cursor" / ".content-verified"
    day_ago = marker.stat().st_mtime - 25 * 3600
    os.utime(marker, (day_ago, day_ago))
    sync.sync_data("cursor", silent=True)
    conversation.clear_cache()
    assert get_conversation(SLUG, "c-main", "cursor")["messages"][-1]["content"] == \
        "Renamed `f` to `h`."


def test_hourly_sync_of_an_unchanged_chat_reads_no_bubble_content(isolated, monkeypatch):
    from clicodelog import sync_cursor
    write_cursor_store(isolated["cursor_user"], {"c-main": cursor_conversation()})
    sync.sync_data("cursor", silent=True)              # first sync: full export + verify
    reads = []
    real = sync_cursor._bubbles
    monkeypatch.setattr(sync_cursor, "_bubbles", lambda *a: reads.append(a) or real(*a))
    sync.sync_data("cursor", silent=True)
    assert reads == []

    # A chat that does change is still re-read and re-exported within the hour.
    write_cursor_store(isolated["cursor_user"],
                       {"c-main": cursor_conversation() + [bubble("b9", 1, "one more")]})
    sync.sync_data("cursor", silent=True)
    assert len(reads) == 1
    assert get_conversation(SLUG, "c-main", "cursor")["messages"][-1]["content"] == "one more"


def test_one_odd_record_does_not_drop_the_chat(tmp_path):
    good, odd = vscode_request(0, "keep me", "kept"), vscode_request(1, "odd", "odd")
    odd["response"].append({"kind": "inlineReference",
                            "inlineReference": {"location": "not-a-dict"}})
    odd["result"] = {"metadata": {"toolCallRounds": "not-a-list"}}
    p = tmp_path / "s.json"
    p.write_text(json.dumps(vscode_session("s", [good, odd, "junk"])), encoding="utf-8")
    contents = [m["content"] for m in parse_vscode_conversation(p, "s")["messages"]]
    assert "keep me" in contents and "kept see `app.py`" in contents

    records = [{"type": "cursor-backup", "kind": "composer", "workspace": FOLDER},
               {"type": "composer", "data": {"name": "x"}},
               {"type": "bubble", "data": bubble("b1", 1, "still here")},
               {"type": "bubble", "data": dict(bubble("b2", 2, "odd"), timingInfo="x",
                                               createdAt=None, toolFormerData={"name": "t",
                                                                               "params": 5})},
               {"type": "bubble", "data": bubble("b3", 2, "and this")}]
    p = tmp_path / "c.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
    contents = [m["content"] for m in parse_cursor_conversation(p, "c")["messages"]]
    assert contents[0] == "still here" and contents[-1] == "and this"


@pytest.mark.parametrize("sid", ["*", "a*", "[c]-main", "?-main"])
def test_session_id_is_not_a_glob_pattern(isolated, sid):
    write_cursor_store(isolated["cursor_user"], {"c-main": cursor_conversation()})
    sync.sync_data("cursor", silent=True)
    assert get_conversation(SLUG, sid, "cursor") == {"error": "Session not found"}


@pytest.mark.skipif(sys.platform.startswith("win") or os.geteuid() == 0,
                    reason="needs POSIX permissions and a non-root user")
def test_an_unreadable_cli_directory_does_not_abort_the_sync(isolated):
    make_cursor_cli(isolated["cursor_chats"])
    locked = isolated["cursor_chats"] / "0000-locked"
    locked.mkdir()
    locked.chmod(0)
    try:
        assert sync.sync_data("cursor", silent=True)
        assert search_index.count("cursor") == 1, "the readable chat is still backed up"
    finally:
        locked.chmod(0o755)


def test_two_rewinds_in_one_second_keep_both_copies(isolated):
    db = isolated["cursor_user"] / "globalStorage" / "state.vscdb"

    def rewind_to(bubbles):
        conn = sqlite3.connect(db)
        conn.execute("DELETE FROM cursorDiskKV WHERE key LIKE 'bubbleId:c-main:%'")
        conn.commit()
        conn.close()
        write_cursor_store(isolated["cursor_user"], {"c-main": bubbles})
        sync.sync_data("cursor", silent=True)

    write_cursor_store(isolated["cursor_user"], {"c-main": cursor_conversation()})
    sync.sync_data("cursor", silent=True)
    rewind_to(cursor_conversation()[:3])
    rewind_to(cursor_conversation()[:1])
    kept = sorted((isolated["data"] / "cursor" / "composers" / WS_HASH).glob("*.bak"))
    assert len(kept) == 2, [k.name for k in kept]


def test_pre_index_listing_works_under_a_path_named_subagents(tmp_path, monkeypatch):
    root = tmp_path / "subagents" / "data"
    ws = root / "vscode" / WS_HASH / "chatSessions"
    ws.mkdir(parents=True)
    (ws / "s.json").write_text(json.dumps(vscode_session("s", [vscode_request(0, "q", "a")])),
                               encoding="utf-8")
    monkeypatch.setattr("clicodelog.sessions.DATA_DIR", root)
    assert [s["id"] for s in sessions._scan_project(WS_HASH, "vscode")] == ["s"]


# ------------------------------------------------------------- PR #1 review
def test_a_chat_kept_as_json_and_jsonl_is_listed_once(isolated):
    make_vscode(isolated["vscode_user"])
    sync.sync_data("vscode", silent=True)
    chats = isolated["data"] / "vscode" / WS_HASH / "chatSessions"
    old = chats / "sess-json.json"
    (chats / "sess-json.jsonl").write_text(
        json.dumps({"kind": 0, "v": json.loads(old.read_text(encoding="utf-8"))}) + "\n"
        + json.dumps({"kind": 1, "k": ["customTitle"], "v": "migrated"}) + "\n",
        encoding="utf-8")
    os.utime(old, (1, 1))                                   # the .json is the stale one
    search_index.refresh_index("vscode")
    rows = [s for s in sessions.get_sessions(WS_HASH, "vscode")["sessions"] if s["id"] == "sess-json"]
    assert len(rows) == 1 and rows[0]["summary"] == "migrated"
    assert get_conversation(WS_HASH, "sess-json", "vscode")["summaries"] == ["migrated"]


def test_transcript_is_listed_when_the_store_copy_is_empty(isolated):
    write_cursor_store(isolated["cursor_user"], {"c-main": []})     # composer, no bubbles
    write_transcripts(isolated["cursor_projects"], SLUG, {"c-main": transcript_lines("real chat")})
    sync.sync_data("cursor", silent=True)
    page = sessions.get_sessions(SLUG, "cursor")
    assert [(s["id"], s["summary"]) for s in page["sessions"]] == [("c-main", "real chat")]
    fts.build_index("cursor")
    assert [h["session_id"] for h in fts.search_content("real chat", "cursor")] == ["c-main"]

    # Once the store copy has messages, it takes over, and nothing is listed twice.
    write_cursor_store(isolated["cursor_user"], {"c-main": cursor_conversation()})
    sync.sync_data("cursor", silent=True)
    page = sessions.get_sessions(SLUG, "cursor")
    assert [(s["id"], s["summary"]) for s in page["sessions"]] == [("c-main", "Rename function")]


def test_read_mode_follows_the_journal_mode(tmp_path, monkeypatch):
    from clicodelog import cursor_store
    uris = []
    real = sqlite3.connect
    monkeypatch.setattr(cursor_store.sqlite3, "connect",
                        lambda target, **kw: uris.append(target) or real(target, **kw))
    rollback = tmp_path / "rollback.db"
    c = real(rollback)
    c.execute("CREATE TABLE t(x)")
    c.commit()
    c.close()
    wal = tmp_path / "wal.db"
    c = real(wal)
    c.execute("PRAGMA journal_mode=wal")
    c.execute("CREATE TABLE t(x)")
    c.commit()
    c.close()                                               # checkpointed: no -wal left
    for db in (rollback, wal):
        cursor_store.open_readonly(db).close()
    assert uris[0].endswith("?mode=ro"), "a rollback-mode file needs locking, never immutable"
    assert uris[1].endswith("immutable=1"), "an idle WAL file is read without creating sidecars"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["rollback.db", "wal.db"]


def test_vscode_rewrite_that_drops_turns_keeps_the_old_copy(isolated):
    make_vscode(isolated["vscode_user"])
    sync.sync_data("vscode", silent=True)
    src = isolated["vscode_user"] / "workspaceStorage" / WS_HASH / "chatSessions" / "sess-json.json"
    session = json.loads(src.read_text(encoding="utf-8"))
    session["requests"] = session["requests"][:1]           # the user removed a turn
    src.write_text(json.dumps(session), encoding="utf-8")
    sync.sync_data("vscode", silent=True)
    chats = isolated["data"] / "vscode" / WS_HASH / "chatSessions"
    kept = list(chats.glob("sess-json.superseded-*.bak"))
    assert len(kept) == 1
    assert len(json.loads(kept[0].read_text(encoding="utf-8"))["requests"]) == 2
    assert sessions.get_sessions(WS_HASH, "vscode")["total"] == 2, "the .bak is not listed"

    # Growth (a new turn) loses nothing, so it keeps no copy.
    session["requests"] = session["requests"] + [vscode_request(5, "more", "ok")]
    src.write_text(json.dumps(session), encoding="utf-8")
    sync.sync_data("vscode", silent=True)
    assert len(list(chats.glob("*.bak"))) == 1


def test_search_hits_pick_up_a_project_name_learned_later(isolated):
    write_transcripts(isolated["cursor_projects"], SLUG, {"t-1": transcript_lines("findme")})
    sync.sync_data("cursor", silent=True)
    fts.build_index("cursor")
    assert fts.search_content("findme", "cursor")[0]["project_name"] == SLUG
    write_cursor_store(isolated["cursor_user"], {"c-main": cursor_conversation()})
    sync.sync_data("cursor", silent=True)
    fts.build_index("cursor")
    assert fts.search_content("findme", "cursor")[0]["project_name"] == FOLDER


def test_folding_a_bubble_keeps_every_token_counter():
    from clicodelog.parsers.cursor import _append
    messages = [{"role": "assistant", "content": "x", "usage": {
        "input_tokens": 5000, "cache_read_input_tokens": 40000, "output_tokens": 10}}]
    _append(messages, {"role": "assistant", "content": "", "tool_uses": [{"name": "t"}],
                       "usage": {"output_tokens": 20}})
    assert messages[0]["usage"] == {"input_tokens": 5000, "cache_read_input_tokens": 40000,
                                    "output_tokens": 30}


def test_nested_subagents_are_counted_once_and_consistently(isolated):
    write_transcripts(isolated["cursor_projects"], SLUG, {"t-1": transcript_lines("parent")},
                      subagents={"t-1": {"a": transcript_lines("x")}})
    nested = (isolated["cursor_projects"] / SLUG / "agent-transcripts" / "t-1" / "subagents"
              / "wf" / "b.jsonl")
    nested.parent.mkdir(parents=True)
    nested.write_text(json.dumps(transcript_lines("y")[0]) + "\n", encoding="utf-8")
    for _ in range(2):                                      # stable across refreshes
        sync.sync_data("cursor", silent=True)
        row = sessions.get_sessions(SLUG, "cursor")["sessions"][0]
        assert row["subagent_count"] == 2
        assert len(sessions.get_subagent_sessions(SLUG, "t-1", "cursor")) == 2


def test_vscode_never_serves_another_workspaces_copy(isolated):
    make_vscode(isolated["vscode_user"])
    sync.sync_data("vscode", silent=True)
    other = isolated["data"] / "vscode" / "insiders-x" / "chatSessions"
    other.mkdir(parents=True)
    (other / "only-there.json").write_text(
        json.dumps(vscode_session("only-there", [vscode_request(0, "q", "a")])), encoding="utf-8")
    search_index.refresh_index("vscode")
    assert get_conversation(WS_HASH, "only-there", "vscode") == {"error": "Session not found"}
    assert get_conversation("insiders-x", "only-there", "vscode")["messages"]
