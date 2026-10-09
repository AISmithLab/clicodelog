"""Regression tests for the code-review findings on the editor sources."""

import json
import os
import sqlite3
import sys

import pytest

from clicodelog import fts, parsers, search_index, sessions, sync
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
    sync.sync_data("cursor", silent=True)
    conv = get_conversation(SLUG, "c-main", "cursor")
    assert conv["messages"][-1]["content"] == "Renamed `f` to `h`."


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
