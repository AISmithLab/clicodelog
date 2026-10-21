"""History API discovery, authentication, and incomplete-read handling."""

import io
import json
from types import SimpleNamespace

import pytest

from clicodelog import antigravity_client as api
from clicodelog.agent_paths import antigravity_roots, copilot_sessions_dir


@pytest.mark.parametrize("url", ["https://127.0.0.1:1234", "http://example.com:1234",
                               "http://127.0.0.1:1234/path", "http://user@localhost:1234",
                               "http://localhost:1234/?token=secret", "file:///tmp/history"])
def test_endpoint_must_be_local(url):
    with pytest.raises(ValueError):
        api.Client(url)


def test_json_transport_keeps_token_in_header_only():
    client = api.Client("http://127.0.0.1:1234", "private-token")
    class Opener:
        def open(self, request, timeout):
            assert request.full_url.endswith(api.SERVICE + "GetAllCascadeTrajectories")
            assert request.get_header("X-codeium-csrf-token") == "private-token"
            assert "private-token" not in request.full_url
            assert json.loads(request.data) == {}
            return io.BytesIO(b'{"trajectorySummaries":{}}')
    client.opener = Opener()
    assert client.call("GetAllCascadeTrajectories") == {"trajectorySummaries": {}}
    with pytest.raises(ValueError):
        client.call("SendUserCascadeMessage")


def test_paged_trajectory_is_complete(monkeypatch):
    client = api.Client("http://localhost:1234")
    def call(method, payload):
        if method == "GetCascadeTrajectory":
            return {"trajectory": {"steps": [{"id": 0}]}, "numTotalSteps": 3}
        assert method == "GetCascadeTrajectorySteps"
        return {"steps": [{"id": payload["stepOffset"]}]}
    monkeypatch.setattr(client, "call", call)
    assert client.fetch("id")["steps"] == [{"id": i} for i in range(3)]


def test_failed_page_does_not_return_partial_history(monkeypatch):
    client = api.Client("http://localhost:1234")
    monkeypatch.setattr(client, "call", lambda method, payload:
                        {"trajectory": {"steps": [{"id": 0}]}, "numTotalSteps": 3}
                        if method == "GetCascadeTrajectory" else {"steps": []})
    with pytest.raises(ValueError, match="Incomplete"):
        client.fetch("id")


def test_closed_history_is_loaded_without_sending_messages(monkeypatch):
    client = api.Client("http://localhost:1234")
    methods = []
    def call(method, payload):
        methods.append(method)
        if len(methods) == 1:
            raise OSError("not loaded")
        return {"trajectory": {"steps": [{"userInput": {"query": "hi"}}]}}
    monkeypatch.setattr(client, "call", call)
    assert client.fetch("id")["steps"]
    assert methods == ["GetCascadeTrajectory", "LoadTrajectory", "GetCascadeTrajectory"]


def test_discovery_pairs_process_token_and_ports(monkeypatch, tmp_path):
    monkeypatch.delenv("CLICODELOG_ANTIGRAVITY_URL", raising=False)
    monkeypatch.setattr(api, "config_root", lambda: tmp_path)
    monkeypatch.setattr(api, "sys", SimpleNamespace(platform="darwin"))
    def run(args):
        if args[0] == "ps":
            return ("10 /Applications/Antigravity/language_server --csrf_token token-a\n"
                    "11 /Applications/Other/language_server --csrf_token other\n"
                    "12 /Applications/Antigravity/language_server --csrf_token=token-b --port=4567")
        return "n127.0.0.1:3456\n" if args[4] == "10" else ""
    monkeypatch.setattr(api, "_run", run)
    clients = api.discover()
    assert [(c.url, c.token) for c in clients] == [("http://127.0.0.1:3456", "token-a"),
                                                           ("http://127.0.0.1:4567", "token-b")]


def test_agent_path_overrides(monkeypatch, tmp_path):
    monkeypatch.delenv("CLICODELOG_COPILOT_DIR", raising=False)
    monkeypatch.setenv("COPILOT_HOME", str(tmp_path))
    assert copilot_sessions_dir() == tmp_path / "session-state"
    monkeypatch.setenv("CLICODELOG_COPILOT_DIR", str(tmp_path / "custom"))
    assert copilot_sessions_dir() == tmp_path / "custom"
    monkeypatch.setenv("CLICODELOG_ANTIGRAVITY_DIRS", str(tmp_path / "ag"))
    assert antigravity_roots() == [tmp_path / "ag"]
