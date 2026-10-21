"""Both new sources must survive the complete backup-to-export pipeline."""

import json
import sqlite3
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from clicodelog import antigravity_client, fts, search_index, sync, sync_antigravity
from clicodelog.app import app
from clicodelog.conversation import get_conversation
from clicodelog.parsers import PARSERS
from clicodelog.utils import encode_path_id

from agent_fixtures import (CWD, SID, STAMP, antigravity_snapshot, copilot_events,
                            make_antigravity, make_copilot, write_jsonl)


@pytest.mark.parametrize("source,make", [("copilot", make_copilot), ("antigravity", make_antigravity)])
def test_complete_history_pipeline(isolated, source, make):
    path = make(isolated["root"])
    original = path.read_bytes()
    assert sync.sync_data(source, silent=True)
    assert path.read_bytes() == original
    project = search_index.projects_for_source(source)[0]
    assert (project["id"], project["name"]) == (encode_path_id(CWD), CWD)
    conv = get_conversation(project["id"], SID, source, include_results=True)
    assert conv["summaries"] == ["Fix the widget"]
    assert conv["messages"][0]["content"] == "fix the widget 日本"
    assert conv["messages"][1]["thinking"] == "Inspect then edit."
    assert conv["messages"][-1]["content"] == "Widget fixed."
    tools = [t for m in conv["messages"] for t in (m["tool_uses"] or [])]
    assert len(tools) == (1 if source == "copilot" else 2), "requests and executions are one call"
    assert "日本" in tools[0]["result"]
    row = next(search_index.iter_entries(source))
    assert row["usage"]["input"] == 12 and row["usage"]["cache_read"] == 90
    assert row["usage"]["cache_creation"] == 4
    assert fts.build_index(source)["state"] == "ready"
    client = TestClient(app)
    hits = client.get("/api/search", params={"source": source, "q": "widget"}).json()
    assert hits["results"] and hits["content_search"]
    query = "patched" if source == "copilot" else "passed"
    content_hits = client.get("/api/search", params={"source": source, "q": query}).json()
    assert content_hits["results"] and content_hits["results"][0]["matched"] == "content"
    url = f"/api/projects/{project['id']}/sessions/{SID}"
    for endpoint, fmt in (("export-raw", ""), ("export", "md"), ("export", "txt")):
        response = client.get(url + "/" + endpoint, params={"source": source, "fmt": fmt})
        assert response.status_code == 200, response.text
        if endpoint == "export-raw" and source == "antigravity":
            assert response.json() == antigravity_snapshot()
        else:
            assert "日本" in response.text
    assert get_conversation("wrong-project", SID, source).get("error")
    # Previously imported histories remain available after source files vanish.
    path.unlink()
    assert sync.sync_data(source, silent=True)
    assert get_conversation(project["id"], SID, source)["messages"]


def test_copilot_legacy_layout_growth_and_rewind(isolated):
    root = isolated["root"] / "copilot"
    path = root / (SID + ".jsonl")
    rows = copilot_events()
    write_jsonl(path, rows)
    sync.sync_data("copilot", silent=True)
    target = isolated["data"] / "copilot" / "sessions" / path.name
    before = target.stat().st_mtime_ns
    sync.sync_data("copilot", silent=True)
    assert target.stat().st_mtime_ns == before
    write_jsonl(path, rows + [{"id": "extra", "type": "user.message", "data": {"content": "more"}}])
    sync.sync_data("copilot", silent=True)
    assert not list(target.parent.glob("*.bak"))
    write_jsonl(path, rows)
    sync.sync_data("copilot", silent=True)
    assert "more" in next(target.parent.glob("*.bak")).read_text(encoding="utf-8")


def test_copilot_torn_tail_duplicates_bad_shapes_and_model_changes(tmp_path):
    path = tmp_path / "events.jsonl"
    rows = copilot_events()
    write_jsonl(path, [None, 42, {"type": "assistant.message", "data": []}, *rows, rows[4]])
    with path.open("a", encoding="utf-8") as fh:
        fh.write('{"type":"user.message","data":')
    conv = PARSERS["copilot"](path, SID)
    assert len(conv["messages"]) == 3
    assert conv["messages"][-1]["model"] == "gpt-5"
    assert conv["messages"][1]["edited_files"] == [CWD + "/widget.py"]


def test_antigravity_encrypted_only_reports_actionable_warning(isolated):
    root = isolated["root"] / "antigravity" / "conversations"
    root.mkdir(parents=True)
    (root / (SID + ".pb")).write_bytes(b"encrypted history")
    assert sync.sync_data("antigravity", silent=True)
    backup = isolated["data"] / "antigravity"
    assert next((backup / "raw").rglob("*.pb")).read_bytes() == b"encrypted history"
    sources = TestClient(app).get("/api/sources").json()["sources"]
    entry = next(s for s in sources if s["id"] == "antigravity")
    assert entry["available"] and entry["session_count"] == 0
    assert "Open Antigravity" in entry["warning"]
    make_antigravity(isolated["root"])
    sync.sync_data("antigravity", silent=True)
    assert sync_antigravity.source_warning(backup) is None


def test_antigravity_imports_local_api_and_works_offline(isolated, monkeypatch):
    payload = antigravity_snapshot()
    class FakeClient:
        def call(self, method):
            assert method == "GetAllCascadeTrajectories"
            return {"trajectorySummaries": {SID: payload["summary"]}}

        def fetch(self, sid):
            assert sid == SID
            return payload["trajectory"]
    monkeypatch.setattr(antigravity_client, "discover", lambda: [FakeClient()])
    assert sync.sync_data("antigravity", silent=True)
    monkeypatch.setattr(antigravity_client, "discover", lambda: [])
    assert get_conversation(encode_path_id(CWD), SID, "antigravity")["messages"]
    entry = next(s for s in TestClient(app).get("/api/sources").json()["sources"] if s["id"] == "antigravity")
    assert entry["available"]


def test_antigravity_rewrites_preserve_previous_snapshot(isolated):
    src = make_antigravity(isolated["root"])
    sync.sync_data("antigravity", silent=True)
    target = isolated["data"] / "antigravity" / "sessions" / (SID + ".json")
    original = target.read_bytes()
    src.write_text('{"trajectory":', encoding="utf-8")
    sync.sync_data("antigravity", silent=True)
    assert target.read_bytes() == original
    payload = antigravity_snapshot()
    payload["trajectory"]["steps"] = payload["trajectory"]["steps"][:1]
    src.write_text(json.dumps(payload), encoding="utf-8")
    sync.sync_data("antigravity", silent=True)
    assert next(target.parent.glob("*.bak")).read_bytes() == original


def test_antigravity_sqlite_raw_backup_includes_wal(isolated):
    root = isolated["root"] / "antigravity" / "conversations"
    root.mkdir(parents=True)
    src = root / (SID + ".db")
    conn = sqlite3.connect(src)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA wal_autocheckpoint=0")
    conn.execute("CREATE TABLE history (content TEXT)")
    conn.execute("INSERT INTO history VALUES ('first')")
    conn.commit()
    try:
        sync.sync_data("antigravity", silent=True)
        target = next((isolated["data"] / "antigravity" / "raw").rglob("*.db"))
        with closing(sqlite3.connect(target)) as dest:
            assert dest.execute("SELECT content FROM history").fetchone()[0] == "first"
        conn.execute("INSERT INTO history VALUES ('in WAL')")
        conn.commit()
        sync.sync_data("antigravity", silent=True)
        with closing(sqlite3.connect(target)) as dest:
            assert dest.execute("SELECT count(*) FROM history").fetchone()[0] == 2
        assert list(target.parent.glob("*.bak"))
    finally:
        conn.close()
