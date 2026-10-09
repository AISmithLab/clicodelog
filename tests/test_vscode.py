"""VS Code (GitHub Copilot Chat): backup, both on-disk formats, listing, export."""

import json

from fastapi.testclient import TestClient

from clicodelog import fts, search_index, sessions, sync
from clicodelog.app import app
from clicodelog.conversation import get_conversation
from clicodelog.parsers.vscode import parse_vscode_conversation
from clicodelog.parsers.vscode_log import load_session

from editor_fixtures import WS_HASH, make_vscode, vscode_mutation_log, vscode_request, vscode_session


def _sync(env):
    make_vscode(env["vscode_user"])
    assert sync.sync_data("vscode", silent=True)


# ------------------------------------------------------------------ formats
def test_mutation_log_replays_to_the_same_session_as_the_json_form(tmp_path):
    reqs = [vscode_request(0, "a", "b"), vscode_request(1, "c", "d")]
    (tmp_path / "s.json").write_text(json.dumps(vscode_session("s", reqs)))
    (tmp_path / "s.jsonl").write_text(vscode_mutation_log("s", reqs))
    doc, log = load_session(tmp_path / "s.json"), load_session(tmp_path / "s.jsonl")
    assert log["requests"] == doc["requests"], "set/push/push-with-truncate must replay exactly"
    assert log["customTitle"] == "Fix the app"
    assert "inputText" not in log.get("inputState", {}), "kind 3 must delete"


def test_torn_tail_of_a_mutation_log_keeps_what_replayed(tmp_path):
    p = tmp_path / "s.jsonl"
    p.write_text(vscode_mutation_log("s", [vscode_request(0, "a", "b")]) + '{"kind": 1, "k": ["cus')
    conv = parse_vscode_conversation(p, "s")
    assert [m["role"] for m in conv["messages"]] == ["user", "assistant"]


def test_edit_to_an_unknown_path_is_skipped_not_fatal(tmp_path):
    p = tmp_path / "s.jsonl"
    lines = [{"kind": 0, "v": vscode_session("s", [vscode_request(0, "a", "b")])},
             {"kind": 1, "k": ["requests", 7, "result"], "v": {}},
             {"kind": 2, "k": ["nope", "deeper"], "v": [1]},
             {"kind": 1, "k": ["customTitle"], "v": "still read"}]
    p.write_text("\n".join(json.dumps(x) for x in lines))
    conv = parse_vscode_conversation(p, "s")
    assert conv["summaries"] == ["still read"]
    assert len(conv["messages"]) == 2


def test_assistant_turn_is_fully_normalised(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps(vscode_session("s", [vscode_request(0, "make x one", "Done.")])))
    user, asst = parse_vscode_conversation(p, "s")["messages"]
    assert user == {"role": "user", "content": "make x one",
                    "timestamp": "2026-02-06T00:52:05.297Z", "uuid": "request_0"}
    assert asst["content"] == "Done. see `app.py`", "empty edit fences must be dropped"
    assert asst["thinking"] == "thinking about 0"
    assert asst["model"] == "claude-sonnet-4.5"
    assert asst["usage"]["input_tokens"] == 1000 and asst["usage"]["output_tokens"] == 50
    assert asst["edited_files"] == ["/Users/x/my.app/app.py"]
    tool = asst["tool_uses"][0]
    assert tool["name"] == "replace_string_in_file"
    assert tool["input"]["newString"] == "x = 1", "exact arguments come from toolCallRounds"
    assert "successfully edited" in tool["result"]


def test_auto_model_reports_what_actually_ran(tmp_path):
    req = vscode_request(0, "q", "a")
    req["modelId"] = "copilot/auto"
    p = tmp_path / "s.json"
    p.write_text(json.dumps(vscode_session("s", [req])))
    assert parse_vscode_conversation(p, "s")["messages"][1]["model"] == "Claude Sonnet 4.5"


def test_tool_parts_are_used_when_rounds_are_absent(tmp_path):
    req = vscode_request(0, "run it", "Running.")
    req["result"]["metadata"] = {}
    req["response"].append({"kind": "toolInvocationSerialized", "toolId": "run_in_terminal",
                            "invocationMessage": "Using \"Run in Terminal\"",
                            "toolSpecificData": {"kind": "terminal",
                                                 "commandLine": {"original": "pytest -q"}}})
    p = tmp_path / "s.json"
    p.write_text(json.dumps(vscode_session("s", [req])))
    tools = parse_vscode_conversation(p, "s")["messages"][1]["tool_uses"]
    assert [t["name"] for t in tools] == ["copilot_replaceString", "run_in_terminal"]
    assert tools[1]["input"] == {"command": "pytest -q"}


def test_non_ascii_text_survives(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps(vscode_session("s", [vscode_request(0, "日本語 ✓", "ok")]),
                            ensure_ascii=False), encoding="utf-8")
    assert parse_vscode_conversation(p, "s")["messages"][0]["content"] == "日本語 ✓"


# ------------------------------------------------------------------ backup
def test_backup_copies_only_chat_history(isolated):
    _sync(isolated)
    vs = isolated["data"] / "vscode"
    copied = sorted(str(p.relative_to(vs)).replace("\\", "/") for p in vs.rglob("*") if p.is_file())
    assert copied == sorted([
        f"{WS_HASH}/chatSessions/sess-empty.json", f"{WS_HASH}/chatSessions/sess-json.json",
        f"{WS_HASH}/chatSessions/sess-log.jsonl", f"{WS_HASH}/workspace.json",
        "_empty-window/chatSessions/sess-nowin.json",
        "aaaa1111/chatSessions/sess-win.json", "aaaa1111/workspace.json",
    ]), "extension state (state.vscdb, globalStorage/*) must never be copied"


def test_backup_is_additive(isolated):
    _sync(isolated)
    (isolated["vscode_user"] / "workspaceStorage" / WS_HASH / "chatSessions" / "sess-json.json").unlink()
    assert sync.sync_data("vscode", silent=True)
    assert (isolated["data"] / "vscode" / WS_HASH / "chatSessions" / "sess-json.json").exists()
    assert search_index.count("vscode") == 4, "a chat VS Code deleted stays listed"


def test_every_flavour_is_backed_up_without_collisions(isolated, monkeypatch, tmp_path):
    stable, insiders = tmp_path / "Code" / "User", tmp_path / "Insiders" / "User"
    make_vscode(stable)
    make_vscode(insiders)
    import os
    monkeypatch.setenv("CLICODELOG_VSCODE_USER_DIRS", os.pathsep.join([str(stable), str(insiders)]))
    assert sync.sync_data("vscode", silent=True)
    dirs = sorted(p.name for p in (isolated["data"] / "vscode").iterdir())
    assert WS_HASH in dirs and f"alt1-{WS_HASH}" in dirs
    assert search_index.count("vscode") == 8


def test_no_vscode_installed_is_not_an_error(isolated):
    assert sync.sync_data("vscode", silent=True) is False


# ------------------------------------------------------------------ app
def test_projects_sessions_and_conversation(isolated):
    _sync(isolated)
    projects = {p["id"]: p for p in search_index.projects_for_source("vscode")}
    assert projects[WS_HASH]["name"] == "/Users/x/my.app"
    assert projects["aaaa1111"]["name"] == "c:\\Users\\x\\win proj"
    assert projects["_empty-window"]["name"] == "(no folder open)"

    page = sessions.get_sessions(WS_HASH, "vscode")
    assert page["total"] == 2, "an empty chat is not listed"
    by_id = {s["id"]: s for s in page["sessions"]}
    assert by_id["sess-log"]["summary"] == "Fix the app"
    assert by_id["sess-log"]["message_count"] == 4
    assert by_id["sess-log"]["usage"]["input"] == 2001

    conv = get_conversation(WS_HASH, "sess-log", "vscode", limit=2, tail=True)
    assert conv["total_messages"] == 4 and conv["offset"] == 2
    assert conv["messages"][0]["content"] == "日本語 ✓ thanks"
    assert conv["meta"]["cwd"] == "/Users/x/my.app"


def test_full_text_search_and_file_history(isolated):
    _sync(isolated)
    assert fts.build_index("vscode")["state"] == "ready"
    hits = fts.search_content("日本語", "vscode")
    assert {h["session_id"] for h in hits} == {"sess-json", "sess-log"}
    assert hits[0]["matches"][0]["uuid"] == "request_1"
    touched = fts.sessions_touching("app.py", "vscode")
    assert {t["session_id"] for t in touched} >= {"sess-json", "sess-log"}

    # A changed file is re-indexed whole, never appended to: no duplicate rows.
    f = isolated["data"] / "vscode" / WS_HASH / "chatSessions" / "sess-log.jsonl"
    f.write_text(f.read_text() + json.dumps({"kind": 1, "k": ["customTitle"], "v": "Renamed"}) + "\n")
    fts.build_index("vscode")
    hits = [h for h in fts.search_content("日本語", "vscode") if h["session_id"] == "sess-log"]
    assert len(hits[0]["matches"]) == 1


def test_export_endpoints(isolated):
    _sync(isolated)
    client = TestClient(app)
    base = f"/api/projects/{WS_HASH}/sessions/sess-log"
    md = client.get(f"{base}/export", params={"source": "vscode", "fmt": "md"})
    assert md.status_code == 200
    assert "**Project:** `/Users/x/my.app`" in md.text
    assert "日本語 ✓ thanks" in md.text and "replace_string_in_file" in md.text
    assert "Total tokens (including cache reads): 2,101" in md.text
    txt = client.get(f"{base}/export", params={"source": "vscode"})
    assert txt.status_code == 200 and "[ASSISTANT]" in txt.text and "Model: claude-sonnet-4.5" in txt.text
    raw = client.get(f"{base}/export-raw", params={"source": "vscode"})
    assert raw.status_code == 200
    assert raw.content == (isolated["data"] / "vscode" / WS_HASH / "chatSessions"
                           / "sess-log.jsonl").read_bytes()
    assert client.get(f"/api/projects/{WS_HASH}/sessions/nope/export",
                      params={"source": "vscode"}).status_code == 404
