"""Exercise actual filesystem and HTTP I/O on each CI host."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from clicodelog import antigravity_client, fts, search_index, sync
from clicodelog.conversation import get_conversation
from clicodelog.utils import encode_path_id

from agent_fixtures import SID, antigravity_snapshot, copilot_events


@pytest.mark.parametrize("source", ["copilot", "antigravity"])
def test_windows_style_utf8_bom_crlf_history_in_unicode_directory(isolated, monkeypatch, source):
    root = isolated["root"] / "Zoë 日本" / "Agent history"
    cwd = "C:\\Users\\Zoë 日本\\My Project"
    if source == "copilot":
        monkeypatch.setenv("CLICODELOG_COPILOT_DIR", str(root))
        path = root / SID / "events.jsonl"
        events = copilot_events()
        events[0]["data"]["context"]["cwd"] = cwd
        body = "\r\n".join(json.dumps(event, ensure_ascii=False) for event in events) + "\r\n"
    else:
        monkeypatch.setenv("CLICODELOG_ANTIGRAVITY_DIRS", str(root))
        path = root / "conversations" / (SID + ".trajectory.json")
        trajectory = antigravity_snapshot()["trajectory"]
        trajectory["metadata"] = {"workspaceUris": ["file:///C:/Users/Zo%C3%AB%20日本/My%20Project"]}
        body = json.dumps(trajectory, ensure_ascii=False, indent=2).replace("\n", "\r\n")
    path.parent.mkdir(parents=True)
    path.write_bytes(b"\xef\xbb\xbf" + body.encode("utf-8"))
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert sync.sync_data(source, silent=True)
    project = search_index.projects_for_source(source)[0]
    assert project["name"] == cwd
    assert get_conversation(encode_path_id(cwd), SID, source)["messages"][0]["content"] == "fix the widget 日本"
    assert fts.build_index(source)["state"] == "ready"
    assert {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()} == before


def test_antigravity_import_through_real_loopback_http(isolated, monkeypatch):
    payload = antigravity_snapshot()
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            assert self.headers.get("x-codeium-csrf-token") == "test-local-token"
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            method = self.path.rsplit("/", 1)[-1]
            requests.append((method, request))
            if method == "GetAllCascadeTrajectories":
                response = {"trajectorySummaries": {SID: payload["summary"]}}
            elif method == "GetCascadeTrajectory":
                response = {"trajectory": payload["trajectory"]}
            else:
                self.send_error(400)
                return
            data = json.dumps(response, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = antigravity_client.Client(f"http://127.0.0.1:{server.server_port}", "test-local-token")
    monkeypatch.setattr(antigravity_client, "discover", lambda: [client])
    try:
        assert sync.sync_data("antigravity", silent=True)
        project = search_index.projects_for_source("antigravity")[0]
        conv = get_conversation(project["id"], SID, "antigravity", include_results=True)
        assert conv["messages"][0]["content"] == "fix the widget 日本"
        assert "3 passed 日本" == conv["messages"][1]["tool_uses"][0]["result"]
        assert [method for method, body in requests] == ["GetAllCascadeTrajectories", "GetCascadeTrajectory"]
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
