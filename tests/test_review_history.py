"""Persisted SDK/trajectory variants must remain visible through the app."""

import json

import pytest
from fastapi.testclient import TestClient

from clicodelog import fts, search_index, sync
from clicodelog.app import app
from clicodelog.conversation import get_conversation
from clicodelog.utils import encode_path_id

from agent_fixtures import (SID, STAMP, antigravity_snapshot, copilot_events,
                            make_antigravity, write_jsonl)


def test_copilot_context_changes_move_the_indexed_session(isolated):
    path = isolated["root"] / "copilot" / SID / "events.jsonl"
    events = copilot_events()
    write_jsonl(path, events)
    assert sync.sync_data("copilot", silent=True)
    previous = search_index.projects_for_source("copilot")[0]["id"]
    cwd = "C:\\Users\\Zoë\\new project"
    events.append({"id": "context-change", "type": "session.context_changed", "timestamp": STAMP,
                   "data": {"cwd": cwd, "gitRoot": cwd}})
    write_jsonl(path, events)
    assert sync.sync_data("copilot", silent=True)
    projects = search_index.projects_for_source("copilot")
    assert [(p["id"], p["name"]) for p in projects] == [(encode_path_id(cwd), cwd)]
    assert get_conversation(previous, SID, "copilot").get("error")
    assert get_conversation(encode_path_id(cwd), SID, "copilot")["meta"]["cwd"] == cwd


@pytest.mark.parametrize("inline", [None, "Second analysis"])
def test_copilot_preserves_distinct_completed_reasoning_blocks(isolated, inline):
    path = isolated["root"] / "copilot" / SID / "events.jsonl"
    response = {"content": "Done"}
    if inline:
        response["reasoningText"] = inline
    rows = [
        ("assistant.reasoning", {"reasoningId": "r1", "content": "First investigation"}),
        ("assistant.reasoning", {"reasoningId": "r2", "content": "Second analysis"}),
        ("assistant.reasoning", {"reasoningId": "r2", "content": "Second analysis"}),
        ("assistant.message", response),
    ]
    write_jsonl(path, [{"id": f"e{i}", "type": kind, "timestamp": STAMP, "data": data}
                       for i, (kind, data) in enumerate(rows)])
    assert sync.sync_data("copilot", silent=True)
    pid = search_index.projects_for_source("copilot")[0]["id"]
    messages = get_conversation(pid, SID, "copilot")["messages"]
    assert len(messages) == 1
    assert messages[0]["thinking"] == "First investigation\n\nSecond analysis"
    assert fts.build_index("copilot")["state"] == "ready"
    client = TestClient(app)
    hits = client.get("/api/search", params={"source": "copilot", "q": "investigation"}).json()
    assert hits["results"]
    for fmt in ("txt", "md"):
        response = client.get(f"/api/projects/{pid}/sessions/{SID}/export",
                              params={"source": "copilot", "fmt": fmt})
        assert response.status_code == 200
        assert "First investigation" in response.text and "Second analysis" in response.text


@pytest.mark.parametrize("linked", [True, False])
def test_antigravity_shell_execution_is_searchable_and_exportable(isolated, linked):
    path = make_antigravity(isolated["root"])
    payload = antigravity_snapshot()
    call = {"id": "shell-1", "name": "shell_exec", "argumentsJson": '{"command":"uname"}'}
    shell = {"type": "CORTEX_STEP_TYPE_SHELL_EXEC", "metadata": {"createdAt": STAMP},
             "shellExec": {"command": "uname", "exitCode": 0, "output": "recorded-shell-output 日本"}}
    if linked:
        payload["trajectory"]["steps"][1]["plannerResponse"]["toolCalls"] = [call]
        shell["metadata"]["toolCall"] = call
        steps = payload["trajectory"]["steps"][:2] + [shell]
    else:
        steps = [shell]
    payload["trajectory"]["steps"] = steps
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert sync.sync_data("antigravity", silent=True)
    pid = search_index.projects_for_source("antigravity")[0]["id"]
    conv = get_conversation(pid, SID, "antigravity", include_results=True)
    tools = [tool for msg in conv["messages"] for tool in msg["tool_uses"] or []]
    assert len(tools) == 1
    assert tools[0]["input"]["command"] == "uname"
    assert tools[0]["result"] == "recorded-shell-output 日本"
    assert fts.build_index("antigravity")["state"] == "ready"
    client = TestClient(app)
    hits = client.get("/api/search", params={"source": "antigravity", "q": "recorded-shell-output"}).json()
    assert hits["results"]
    for fmt in ("txt", "md"):
        response = client.get(f"/api/projects/{pid}/sessions/{SID}/export",
                              params={"source": "antigravity", "fmt": fmt})
        assert response.status_code == 200 and "recorded-shell-output 日本" in response.text
