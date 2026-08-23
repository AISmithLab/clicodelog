"""Per-source contract tests.

The check that matters: given a source directory with realistic files, the
pipeline must yield at least one session. Gemini failed exactly this for months
— six call sites globbed "chats/session-*.json" while the files on disk were
"*.jsonl", and the format had changed from one JSON object to JSON Lines. The
UI showed an empty project list, which looks identical to never having used the
tool, so nothing ever surfaced it.
"""

import json

import pytest

from clicodelog import search_index, sessions
from clicodelog.scan import scan_session

CLAUDE_LINES = [
    {"type": "summary", "summary": "Fix the CORS hole"},
    {"type": "user", "timestamp": "2026-08-01T10:00:00Z", "cwd": "/Users/x/my_app",
     "uuid": "u1", "message": {"role": "user", "content": "delete the middleware"}},
    {"type": "assistant", "timestamp": "2026-08-01T10:00:05Z", "uuid": "a1",
     "message": {"role": "assistant", "model": "claude-opus-4-8",
                 "usage": {"input_tokens": 10, "output_tokens": 20,
                           "cache_read_input_tokens": 5000,
                           "cache_creation_input_tokens": 100},
                 "content": [{"type": "text", "text": "Removing it now."},
                             {"type": "tool_use", "name": "Edit",
                              "input": {"file_path": "/Users/x/my_app/app.py"}}]}},
]

CODEX_LINES = [
    {"type": "session_meta", "timestamp": "2026-08-01T10:00:00Z",
     "payload": {"cwd": "/Users/x/proj"}},
    {"type": "response_item", "timestamp": "2026-08-01T10:00:01Z",
     "payload": {"role": "user", "content": [{"type": "input_text", "text": "hello"}]}},
    {"type": "response_item", "timestamp": "2026-08-01T10:00:02Z",
     "payload": {"role": "assistant", "content": [{"type": "output_text", "text": "hi"}]}},
    {"type": "event_msg", "timestamp": "2026-08-01T10:00:03Z",
     "payload": {"type": "token_count",
                 "info": {"total_token_usage": {"input_tokens": 100, "output_tokens": 50,
                                                "cached_input_tokens": 900}}}},
]

# Current Gemini CLI shape: header line, then $set mutations / bare records.
GEMINI_LINES = [
    {"sessionId": "s1", "projectHash": "abc123", "kind": "main",
     "startTime": "2026-08-01T10:00:00Z", "lastUpdated": "2026-08-01T10:05:00Z"},
    {"$set": {"messages": [
        {"id": "m1", "timestamp": "2026-08-01T10:00:01Z", "type": "user",
         "content": [{"text": "why is this slow"}]},
        {"id": "m2", "timestamp": "2026-08-01T10:00:02Z", "type": "gemini",
         "content": [{"text": "because it re-parses"}], "model": "gemini-2.0"},
    ]}},
    {"id": "m3", "timestamp": "2026-08-01T10:00:03Z", "type": "user",
     "content": [{"text": "fix it"}]},
]


def _write_jsonl(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")


@pytest.fixture
def fake_data(tmp_path, monkeypatch):
    """A data dir laid out like the real backup, for all three sources."""
    root = tmp_path / "data"
    _write_jsonl(root / "claude-code" / "-Users-x-my-app" / "sess-1.jsonl", CLAUDE_LINES)
    _write_jsonl(root / "codex" / "2026" / "08" / "rollout-1.jsonl", CODEX_LINES)
    _write_jsonl(root / "gemini" / "my_app" / "chats" / "session-2026-08-01.jsonl",
                 GEMINI_LINES)

    monkeypatch.setattr("clicodelog.config.DATA_DIR", root)
    monkeypatch.setattr("clicodelog.search_index.DATA_DIR", root)
    monkeypatch.setattr("clicodelog.sessions.DATA_DIR", root)
    monkeypatch.setattr("clicodelog.conversation.DATA_DIR", root)
    monkeypatch.setattr("clicodelog.projects.DATA_DIR", root)
    monkeypatch.setattr("clicodelog.search_index.INDEX_FILE", tmp_path / "index.json")
    search_index._index = {}
    search_index._meta = {}
    search_index._loaded = True
    yield root
    search_index._index = {}
    search_index._meta = {}
    search_index._loaded = False


@pytest.mark.parametrize("source_id", ["claude-code", "codex", "gemini"])
def test_source_yields_at_least_one_session(fake_data, source_id):
    """The contract every source must meet, and the one Gemini silently broke."""
    result = search_index.refresh_index(source_id)
    assert result[source_id]["sessions"] >= 1, (
        f"{source_id}: files are on disk but nothing was indexed — "
        f"the log format has probably changed"
    )

    projects = search_index.projects_for_source(source_id)
    assert projects, f"{source_id}: indexed sessions produced no projects"

    listing = sessions.get_sessions(projects[0]["id"], source_id)
    assert listing, f"{source_id}: project has sessions in the index but lists none"
    assert listing[0]["message_count"] >= 1


def test_gemini_reads_jsonl_not_json(fake_data):
    """Regression guard: .jsonl extension AND JSON Lines content."""
    f = next((fake_data / "gemini").rglob("chats/session-*.jsonl"))
    info = scan_session(f, "gemini")
    assert info is not None
    assert info["message_count"] == 3          # 2 from $set + 1 appended
    assert info["gemini_hash"] == "abc123"


def test_claude_token_usage_includes_cache_fields(fake_data):
    """Summing only input+output under-reported real usage by ~116x."""
    f = next((fake_data / "claude-code").rglob("*.jsonl"))
    info = scan_session(f, "claude-code")
    u = info["usage"]
    assert u["input"] == 10 and u["output"] == 20
    assert u["cache_read"] == 5000, "cache reads must be counted"
    assert u["cache_creation"] == 100, "cache writes must be counted"
    assert sum(u.values()) == 5130


def test_codex_token_usage_is_captured(fake_data):
    """Codex token_count events used to be discarded entirely."""
    f = next((fake_data / "codex").rglob("*.jsonl"))
    info = scan_session(f, "codex")
    assert info["usage"]["input"] == 100
    assert info["usage"]["cache_read"] == 900


def test_edited_files_are_recorded(fake_data):
    f = next((fake_data / "claude-code").rglob("*.jsonl"))
    info = scan_session(f, "claude-code")
    assert "/Users/x/my_app/app.py" in info["files_touched"]


def test_project_name_comes_from_cwd_not_directory(fake_data):
    """Claude Code collapses "/", "_" and "-" all into "-", so the directory
    name cannot be decoded back to a path. The recorded cwd is authoritative."""
    search_index.refresh_index("claude-code")
    projects = search_index.projects_for_source("claude-code")
    assert projects[0]["name"] == "/Users/x/my_app"
    assert "my/app" not in projects[0]["name"]


def test_torn_final_line_does_not_break_a_session(tmp_path):
    """Sync copies files while the agent is still writing them."""
    p = tmp_path / "torn.jsonl"
    good = "\n".join(json.dumps(r) for r in CLAUDE_LINES)
    p.write_text(good + '\n{"type": "assistant", "message": {"cont')
    info = scan_session(p, "claude-code")
    assert info is not None
    assert info["message_count"] == 2


def test_non_dict_line_is_skipped(tmp_path):
    p = tmp_path / "odd.jsonl"
    p.write_text("null\n42\n" + json.dumps(CLAUDE_LINES[1]) + "\n")
    info = scan_session(p, "claude-code")
    assert info is not None and info["message_count"] == 1
