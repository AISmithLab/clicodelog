"""Reported omissions must survive browsing, search, and readable export."""

import json

import pytest
from fastapi.testclient import TestClient

from clicodelog import fts, search_index, sync
from clicodelog.app import app
from clicodelog.conversation import get_conversation
from clicodelog.parsers import PARSERS
from clicodelog.utils import encode_path_id

from agent_fixtures import SID, STAMP, antigravity_snapshot, make_antigravity, write_jsonl


def test_antigravity_user_notification_is_visible_and_searchable(isolated):
    path = make_antigravity(isolated["root"])
    payload = antigravity_snapshot()
    payload["trajectory"]["steps"].append({
        "type": "CORTEX_STEP_TYPE_NOTIFY_USER", "metadata": {"createdAt": STAMP},
        "notifyUser": {"notificationContent": "Deployment finished: 通知 résumé", "isBlocking": True},
    })
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert sync.sync_data("antigravity", silent=True)
    pid = search_index.projects_for_source("antigravity")[0]["id"]
    conv = get_conversation(pid, SID, "antigravity")
    assert conv["messages"][-1]["role"] == "assistant"
    assert conv["messages"][-1]["content"] == "Deployment finished: 通知 résumé"
    assert fts.build_index("antigravity")["state"] == "ready"
    client = TestClient(app)
    hits = client.get("/api/search", params={"source": "antigravity", "q": "Deployment"}).json()
    assert hits["results"] and hits["results"][0]["matched"] == "content"
    for fmt in ("txt", "md"):
        response = client.get(f"/api/projects/{pid}/sessions/{SID}/export",
                              params={"source": "antigravity", "fmt": fmt})
        assert response.status_code == 200
        assert "Deployment finished: 通知 résumé" in response.text


@pytest.mark.parametrize("field", ["workspaces", "workspaceUris"])
@pytest.mark.parametrize("uri,cwd", [
    ("file:///C:/Users/Zo%C3%AB/My%20Project", "C:\\Users\\Zoë\\My Project"),
    ("file://server/share/My%20Project", "\\\\server\\share\\My Project"),
    ("file:///home/zo%C3%AB/My%20Project", "/home/zoë/My Project"),
])
def test_sidecar_workspace_metadata_drives_project_grouping(isolated, field, uri, cwd):
    path = make_antigravity(isolated["root"])
    trajectory = antigravity_snapshot()["trajectory"]
    value = [{"workspaceFolderAbsoluteUri": uri}] if field == "workspaces" else [uri]
    trajectory["metadata"] = {field: value}
    # This is a tool's working directory, not the conversation's workspace.
    trajectory["steps"][2]["runCommand"]["cwd"] = "/some/other/command-directory"
    path.write_text(json.dumps(trajectory), encoding="utf-8")
    assert sync.sync_data("antigravity", silent=True)
    projects = search_index.projects_for_source("antigravity")
    assert [(p["id"], p["name"]) for p in projects] == [(encode_path_id(cwd), cwd)]


@pytest.mark.parametrize("with_tools", [True, False])
def test_copilot_each_reasoning_block_stays_with_its_response(tmp_path, with_tools):
    rows = [
        ("assistant.reasoning", {"content": "First reasoning"}),
        ("assistant.message", {"content": "First response", "toolRequests": [
            {"toolCallId": "tool-1", "name": "shell", "arguments": {"command": "pwd"}}
        ] if with_tools else []}),
        ("assistant.reasoning", {"content": "Second reasoning"}),
        ("assistant.message", {"content": "Second response"}),
    ]
    # Subagent activity must not change which main response owns reasoning.
    events = [{"type": kind, "data": data, "timestamp": STAMP} for kind, data in rows]
    events.insert(2, {"type": "assistant.message", "agentId": "child", "data": {"content": "Child"}})
    path = tmp_path / "events.jsonl"
    write_jsonl(path, events)
    messages = PARSERS["copilot"](path, SID)["messages"]
    assert [(m["content"], m["thinking"]) for m in messages] == [
        ("First response", "First reasoning"), ("Child", None), ("Second response", "Second reasoning"),
    ]
