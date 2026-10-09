"""Cursor: export from the SQLite store, agent transcripts, dedupe, export."""

import hashlib
import json

from fastapi.testclient import TestClient

from clicodelog import fts, search_index, sessions, sync
from clicodelog.app import app
from clicodelog.conversation import get_conversation
from clicodelog.editors import cursor_slug
from clicodelog.parsers.cursor import parse_cursor_conversation, parse_prompt_time

from editor_fixtures import (FOLDER, WS_HASH, bubble, cursor_conversation, transcript_lines,
                             write_cursor_store, write_transcripts)

SLUG = cursor_slug(FOLDER)          # "Users-x-my-app"


def _export(env, cid="c-main"):
    hits = list((env["data"] / "cursor" / "composers").glob(f"*/{cid}.jsonl"))
    assert len(hits) == 1, hits
    return hits[0]


def _sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


# ------------------------------------------------------------------ store
def test_store_export_is_read_only_and_complete(isolated):
    write_cursor_store(isolated["cursor_user"], {"c-main": cursor_conversation()})
    db = isolated["cursor_user"] / "globalStorage" / "state.vscdb"
    before = {p.name: _sha(p) for p in isolated["cursor_user"].rglob("*") if p.is_file()}
    assert sync.sync_data("cursor", silent=True)
    after = {p.name: _sha(p) for p in isolated["cursor_user"].rglob("*") if p.is_file()}
    assert after == before, "Cursor's files must never be written, nor sidecars created"
    assert db.exists()

    path = _export(isolated)
    assert path.parent.name == WS_HASH
    recs = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert recs[0]["type"] == "cursor-backup" and recs[0]["workspace"] == FOLDER
    assert recs[1]["type"] == "composer"
    assert [r["data"]["bubbleId"] for r in recs[2:]] == ["b1", "b2", "b3", "b4", "b5", "b6"]


def test_store_chat_is_parsed_into_turns(isolated):
    write_cursor_store(isolated["cursor_user"], {"c-main": cursor_conversation()})
    sync.sync_data("cursor", silent=True)
    conv = parse_cursor_conversation(_export(isolated), "c-main")
    msgs = conv["messages"]
    assert conv["summaries"] == ["Rename function"]
    assert [m["role"] for m in msgs] == ["user", "assistant", "assistant", "assistant"]
    assert msgs[0]["content"] == "rename 函数 in utils.py"

    # Thinking-only and tool-only bubbles fold into the assistant turn.
    think, reply, final = msgs[1], msgs[2], msgs[3]
    assert final["content"] == "Renamed `f` to `g`."
    assert think["thinking"] == "Need to read utils.py first" and think["content"] == ""
    assert reply["content"] == "I'll read the file."
    assert [t["name"] for t in reply["tool_uses"]] == ["read_file", "edit_file"]
    assert reply["tool_uses"][0]["input"] == {"target_file": "src/utils.py"}
    assert "def f(): pass" in reply["tool_uses"][0]["result"]
    assert reply["edited_files"] == ["src/utils.py"]
    assert reply["usage"]["input_tokens"] == 1500 and reply["usage"]["output_tokens"] == 50
    assert reply["model"] == "claude-4-sonnet"
    # Untimed bubbles inherit the previous time rather than showing none.
    assert all(m["timestamp"] for m in msgs)


def test_last_text_bubble_is_its_own_turn(isolated):
    write_cursor_store(isolated["cursor_user"], {"c-main": cursor_conversation()})
    sync.sync_data("cursor", silent=True)
    conv = get_conversation(cursor_slug(FOLDER), "c-main", "cursor")
    assert conv["total_messages"] == 4
    assert conv["messages"][-1]["content"] == "Renamed `f` to `g`."


def test_unchanged_chat_is_not_rewritten(isolated):
    write_cursor_store(isolated["cursor_user"], {"c-main": cursor_conversation()})
    sync.sync_data("cursor", silent=True)
    path = _export(isolated)
    stamp = path.stat().st_mtime_ns
    sync.sync_data("cursor", silent=True)
    assert path.stat().st_mtime_ns == stamp


def test_grown_chat_is_rewritten_in_place(isolated):
    convo = cursor_conversation()
    write_cursor_store(isolated["cursor_user"], {"c-main": convo})
    sync.sync_data("cursor", silent=True)
    write_cursor_store(isolated["cursor_user"],
                       {"c-main": convo + [bubble("b7", 1, "and the tests?")]})
    sync.sync_data("cursor", silent=True)
    assert "and the tests?" in _export(isolated).read_text(encoding="utf-8")
    assert not list(_export(isolated).parent.glob("*.bak"))


def test_rewound_chat_keeps_the_lost_messages(isolated):
    """Editing an earlier prompt makes Cursor drop everything after it. The
    backup's whole point is to keep what the source no longer has."""
    convo = cursor_conversation()
    write_cursor_store(isolated["cursor_user"], {"c-main": convo})
    sync.sync_data("cursor", silent=True)
    import sqlite3
    db = isolated["cursor_user"] / "globalStorage" / "state.vscdb"
    conn = sqlite3.connect(db)
    conn.execute("DELETE FROM cursorDiskKV WHERE key LIKE 'bubbleId:c-main:%'")
    conn.commit()
    conn.close()
    write_cursor_store(isolated["cursor_user"], {"c-main": [bubble("b1", 1, "rename it")]})
    sync.sync_data("cursor", silent=True)

    kept = list(_export(isolated).parent.glob("c-main.superseded-*.bak"))
    assert len(kept) == 1 and "Renamed `f` to `g`." in kept[0].read_text(encoding="utf-8")
    assert search_index.count("cursor") == 1, "the .bak is preserved, not listed"


def test_chat_without_a_workspace_is_listed(isolated):
    write_cursor_store(isolated["cursor_user"], {"c-main": cursor_conversation(),
                                                 "c-free": [bubble("x1", 1, "global q")]})
    sync.sync_data("cursor", silent=True)
    names = {p["id"]: p["name"] for p in search_index.projects_for_source("cursor")}
    assert names == {SLUG: FOLDER, "_no-workspace": "(no workspace)"}


def test_legacy_inline_and_aichat_formats(isolated):
    inline = {"composerId": "c-old", "name": "Old composer", "createdAt": 1700000000000,
              "conversation": [bubble("o1", 1, "old question"), bubble("o2", 2, "old answer")]}
    tab = {"tabId": "tab/1", "chatTitle": "Ancient chat", "lastSendTime": 1690000000000,
           "bubbles": [{"type": "user", "id": "u", "text": "first ever"},
                       {"type": "ai", "id": "a", "rawText": "hello", "modelType": "gpt-4"}]}
    write_cursor_store(isolated["cursor_user"], {}, workspace_ids=(), inline=[inline],
                       aichat_tabs=[tab])
    sync.sync_data("cursor", silent=True)
    page = sessions.get_sessions(SLUG, "cursor")
    by_summary = {s["summary"]: s for s in page["sessions"]}
    assert set(by_summary) == {"Old composer", "Ancient chat"}
    ancient = get_conversation(SLUG, by_summary["Ancient chat"]["id"], "cursor")
    assert [m["content"] for m in ancient["messages"]] == ["first ever", "hello"]
    assert ancient["messages"][1]["model"] == "gpt-4"
    assert "/" not in by_summary["Ancient chat"]["id"], "ids must be safe path components"


def test_unreadable_database_does_not_break_sync(isolated):
    db = isolated["cursor_user"] / "globalStorage" / "state.vscdb"
    db.parent.mkdir(parents=True)
    db.write_bytes(b"this is not sqlite")
    assert sync.sync_data("cursor", silent=True)
    assert search_index.count("cursor") == 0


# ------------------------------------------------------------------ transcripts
def test_prompt_timestamp_is_parsed_to_utc():
    assert parse_prompt_time("Tuesday, Jun 2, 2026, 11:20 AM (UTC+8)") == "2026-06-02T03:20:00Z"
    assert parse_prompt_time("Friday, Dec 12, 2025, 12:05 AM (UTC-5:30)") == "2025-12-12T05:35:00Z"
    assert parse_prompt_time("Monday, Mar 3, 2026, 4:07 PM") == "2026-03-03T16:07:00Z"
    assert parse_prompt_time("garbage") is None


def test_transcripts_are_backed_up_and_listed(isolated):
    write_transcripts(isolated["cursor_projects"], SLUG,
                      {"t-nested": transcript_lines("add new.py"),
                       "t-flat": transcript_lines("flat layout")}, flat=("t-flat",),
                      subagents={"t-nested": {"agent-1": transcript_lines("sub task")}})
    assert sync.sync_data("cursor", silent=True)
    backed = isolated["data"] / "cursor" / "transcripts" / SLUG
    assert (backed / "t-nested" / "t-nested.jsonl").exists()
    assert not (backed / "terminals").exists(), "only agent-transcripts are copied"

    page = sessions.get_sessions(SLUG, "cursor")
    by_id = {s["id"]: s for s in page["sessions"]}
    assert set(by_id) == {"t-nested", "t-flat"}, "sub-agents are not top-level sessions"
    assert by_id["t-nested"]["summary"] == "add new.py"
    assert by_id["t-nested"]["subagent_count"] == 1
    subs = sessions.get_subagent_sessions(SLUG, "t-nested", "cursor")
    assert [s["id"] for s in subs] == ["agent-1"]

    conv = get_conversation(SLUG, "t-nested", "cursor")
    user, looking, done = conv["messages"]
    assert user["content"] == "add new.py" and user["timestamp"] == "2026-06-02T03:20:00Z"
    assert (looking["content"], done["content"]) == ("Looking.", "Created it.")
    assert looking["timestamp"] == done["timestamp"] == user["timestamp"], \
        "untimed assistant lines take the prompt's time"
    tools = [t for m in conv["messages"] for t in (m.get("tool_uses") or [])]
    assert tools[0]["name"] == "Write" and tools[0]["input"]["path"] == "/Users/x/my.app/new.py"
    assert all(m["uuid"] for m in conv["messages"])
    assert get_conversation(SLUG, "agent-1", "cursor")["messages"], "sub-agent opens"


def test_store_and_transcripts_merge_into_one_project_without_duplicates(isolated):
    write_cursor_store(isolated["cursor_user"], {"c-main": cursor_conversation()})
    write_transcripts(isolated["cursor_projects"], SLUG,
                      {"c-main": transcript_lines("same chat"), "t-only": transcript_lines("x")},
                      subagents={"c-main": {"agent-9": transcript_lines("helper")}})
    sync.sync_data("cursor", silent=True)

    projects = search_index.projects_for_source("cursor")
    assert [(p["id"], p["name"]) for p in projects] == [(SLUG, FOLDER)]
    page = sessions.get_sessions(SLUG, "cursor")
    ids = sorted(s["id"] for s in page["sessions"])
    assert ids == ["c-main", "t-only"], "a chat in both is listed once"
    main = next(s for s in page["sessions"] if s["id"] == "c-main")
    assert main["summary"] == "Rename function", "the richer store copy wins"
    assert main["subagent_count"] == 1
    assert [s["id"] for s in sessions.get_subagent_sessions(SLUG, "c-main", "cursor")] == ["agent-9"]


# ------------------------------------------------------------------ search + export
def test_full_text_search(isolated):
    write_cursor_store(isolated["cursor_user"], {"c-main": cursor_conversation()})
    write_transcripts(isolated["cursor_projects"], SLUG, {"t-1": transcript_lines("flat layout")})
    sync.sync_data("cursor", silent=True)
    assert fts.build_index("cursor")["state"] == "ready"
    assert [h["session_id"] for h in fts.search_content("函数", "cursor")] == ["c-main"]
    assert [h["session_id"] for h in fts.search_content("layout", "cursor")] == ["t-1"]
    assert {t["session_id"] for t in fts.sessions_touching("utils.py", "cursor")} == {"c-main"}


def test_export_endpoints(isolated):
    write_cursor_store(isolated["cursor_user"], {"c-main": cursor_conversation()})
    sync.sync_data("cursor", silent=True)
    client = TestClient(app)
    base = f"/api/projects/{SLUG}/sessions/c-main"
    md = client.get(f"{base}/export", params={"source": "cursor", "fmt": "md"})
    assert md.status_code == 200
    assert f"**Project:** `{FOLDER}`" in md.text
    for needle in ("rename 函数 in utils.py", "Tool: <code>read_file</code>",
                   "<summary>Thinking</summary>", "Renamed `f` to `g`.",
                   "Total tokens (including cache reads): 1,550"):
        assert needle in md.text, needle
    raw = client.get(f"{base}/export-raw", params={"source": "cursor"})
    assert raw.status_code == 200 and raw.content == _export(isolated).read_bytes()
    listing = client.get("/api/sources").json()["sources"]
    cursor = next(s for s in listing if s["id"] == "cursor")
    assert cursor["available"] and cursor["session_count"] == 1 and not cursor["warning"]
