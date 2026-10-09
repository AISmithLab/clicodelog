"""Builders for realistic VS Code and Cursor user directories.

The shapes are taken from real files: VS Code's from Copilot Chat 0.36 sessions
(both the .json document and the .jsonl mutation log), Cursor's from the
composer store (cursorDiskKV composerData/bubbleId rows, schema as Cursor
creates it) and from Cursor 3.x agent transcripts.
"""

import json
import sqlite3
from pathlib import Path

WS_HASH = "5f77a69b146f6319996a89f055eb3875"
FOLDER = "/Users/x/my.app"
FOLDER_URI = "file:///Users/x/my.app"


# ------------------------------------------------------------------ vscode
def vscode_request(i: int, text: str, reply: str, *, ts: int = 1770339125297) -> dict:
    return {
        "requestId": f"request_{i}",
        "responseId": f"response_{i}",
        "timestamp": ts + i * 60_000,
        "modelId": "copilot/claude-sonnet-4.5",
        "message": {"text": text, "parts": [{"kind": "text", "text": text}]},
        "variableData": {"variables": []},
        "response": [
            {"kind": "mcpServersStarting", "didStartServerIds": []},
            {"kind": "thinking", "value": f"thinking about {i}", "id": "1"},
            {"value": reply + " see ", "supportThemeIcons": False},
            {"kind": "inlineReference", "inlineReference": {
                "$mid": 1, "fsPath": "/Users/x/my.app/app.py",
                "external": "file:///Users/x/my.app/app.py", "path": "/Users/x/my.app/app.py",
                "scheme": "file"}},
            {"kind": "prepareToolInvocation", "toolName": "copilot_replaceString"},
            {"kind": "toolInvocationSerialized", "toolId": "copilot_replaceString",
             "toolCallId": f"tc{i}", "invocationMessage": "Using \"Replace String in File\"",
             "isComplete": True},
            {"value": "\n```\n", "supportThemeIcons": False},
            {"kind": "codeblockUri", "uri": {"path": "/Users/x/my.app/app.py"}, "isEdit": True},
            {"kind": "textEditGroup", "uri": {"$mid": 1, "path": "/Users/x/my.app/app.py",
                                              "scheme": "file"},
             "edits": [[{"text": "x = 1", "range": {}}]], "done": True},
            {"value": "\n```\n", "supportThemeIcons": False},
        ],
        "result": {
            "timings": {"totalElapsed": 1000},
            "details": "Claude Sonnet 4.5 • 1x",
            "usage": {"promptTokens": 1000 + i, "completionTokens": 50},
            "metadata": {
                "toolCallRounds": [{"response": reply, "toolCalls": [
                    {"name": "replace_string_in_file", "id": f"toolu_{i}",
                     "arguments": json.dumps({"filePath": "/Users/x/my.app/app.py",
                                              "oldString": "x = 0", "newString": "x = 1"})}]}],
                "toolCallResults": {f"toolu_{i}": {"$mid": 20, "content": [{"$mid": 23, "value": {
                    "node": {"type": 1, "children": [
                        {"type": 2, "text": "The following files were successfully edited:"},
                        {"type": 2, "text": "\n"},
                        {"type": 2, "text": "/Users/x/my.app/app.py"}]}}}]}},
            },
        },
        "modelState": {"value": 1, "completedAt": ts + i * 60_000 + 5000},
    }


def vscode_session(sid: str, requests: list, title: str | None = "Fix the app") -> dict:
    s = {"version": 3, "sessionId": sid, "creationDate": 1770339091332,
         "lastMessageDate": 1770349084688, "requesterUsername": "me",
         "responderUsername": "GitHub Copilot", "initialLocation": "panel",
         "requests": requests}
    if title:
        s["customTitle"] = title
    return s


def vscode_mutation_log(sid: str, requests: list, title: str = "Fix the app") -> str:
    """The .jsonl form: initial empty state, then the edits VS Code appends."""
    lines = [{"kind": 0, "v": vscode_session(sid, [], title=None)}]
    for i, req in enumerate(requests):
        streamed = dict(req, response=[])
        lines.append({"kind": 2, "k": ["requests"], "v": [streamed]})
        half = req["response"][:3]
        lines.append({"kind": 2, "k": ["requests", i, "response"], "v": half})
        # A streaming rewrite: truncate back to 2 parts and push the rest.
        lines.append({"kind": 2, "k": ["requests", i, "response"], "i": 2,
                      "v": req["response"][2:]})
        lines.append({"kind": 1, "k": ["requests", i, "result"], "v": req["result"]})
        lines.append({"kind": 1, "k": ["requests", i, "modelState"], "v": req["modelState"]})
    lines.append({"kind": 1, "k": ["customTitle"], "v": title})
    lines.append({"kind": 1, "k": ["inputState", "inputText"], "v": "draft"})
    lines.append({"kind": 3, "k": ["inputState", "inputText"]})
    return "\n".join(json.dumps(x) for x in lines) + "\n"


def make_vscode(user_dir: Path) -> dict:
    """Two workspaces (JSON and JSONL sessions), an empty-window chat, an empty
    chat, and unrelated extension state that must not be copied."""
    ws = user_dir / "workspaceStorage" / WS_HASH
    (ws / "chatSessions").mkdir(parents=True)
    (ws / "workspace.json").write_text(json.dumps({"folder": FOLDER_URI}), encoding="utf-8")
    reqs = [vscode_request(0, "make x one", "Done."), vscode_request(1, "日本語 ✓ thanks", "Sure.")]
    (ws / "chatSessions" / "sess-json.json").write_text(
        json.dumps(vscode_session("sess-json", reqs)), encoding="utf-8")
    (ws / "chatSessions" / "sess-log.jsonl").write_text(
        vscode_mutation_log("sess-log", reqs), encoding="utf-8")
    (ws / "chatSessions" / "sess-empty.json").write_text(
        json.dumps(vscode_session("sess-empty", [], title=None)), encoding="utf-8")
    (ws / "state.vscdb").write_bytes(b"not chat history")

    win = user_dir / "workspaceStorage" / "aaaa1111"
    (win / "chatSessions").mkdir(parents=True)
    (win / "workspace.json").write_text(json.dumps({"folder": "file:///c%3A/Users/x/win%20proj"}), encoding="utf-8")
    (win / "chatSessions" / "sess-win.json").write_text(
        json.dumps(vscode_session("sess-win", [vscode_request(0, "hi from windows", "Hello.")])), encoding="utf-8")

    empty = user_dir / "globalStorage" / "emptyWindowChatSessions"
    empty.mkdir(parents=True)
    (empty / "sess-nowin.json").write_text(
        json.dumps(vscode_session("sess-nowin", [vscode_request(0, "no folder", "OK.")])), encoding="utf-8")
    (user_dir / "globalStorage" / "github.copilot-chat").mkdir()
    (user_dir / "globalStorage" / "github.copilot-chat" / "big.bin").write_bytes(b"x" * 1000)
    return {"ws": ws}


# ------------------------------------------------------------------ cursor
def _kv_db(path: Path, table: str, rows: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute(f"CREATE TABLE IF NOT EXISTS {table} "
                 "(key TEXT UNIQUE ON CONFLICT REPLACE, value BLOB)")
    for k, v in rows.items():
        conn.execute(f"INSERT INTO {table}(key, value) VALUES (?, ?)",
                     (k, v if isinstance(v, bytes) else json.dumps(v)))
    conn.commit()
    conn.close()


def bubble(bid: str, btype: int, text: str = "", **extra) -> dict:
    b = {"_v": 3, "type": btype, "bubbleId": bid, "text": text, "richText": "",
         "createdAt": extra.pop("createdAt", None)}
    b.update(extra)
    return b


def cursor_conversation():
    """A realistic agent chat: prompt, thinking, text, two tool bubbles, reply."""
    return [
        bubble("b1", 1, "rename 函数 in utils.py", createdAt="2026-03-01T10:00:00.000Z"),
        bubble("b2", 2, "", thinking={"text": "Need to read utils.py first", "signature": "s"},
               createdAt="2026-03-01T10:00:02.000Z"),
        bubble("b3", 2, "I'll read the file.", modelInfo={"modelName": "claude-4-sonnet"},
               tokenCount={"inputTokens": 1200, "outputTokens": 30},
               createdAt="2026-03-01T10:00:03.000Z"),
        bubble("b4", 2, "", toolFormerData={
            "tool": 5, "name": "read_file", "status": "completed", "toolCallId": "toolu_1",
            "params": json.dumps({"target_file": "src/utils.py"}),
            "result": json.dumps({"contents": "def f(): pass"})}),
        bubble("b5", 2, "", toolFormerData={
            "tool": 7, "name": "edit_file", "status": "completed",
            "rawArgs": json.dumps({"target_file": "src/utils.py", "code_edit": "def g(): pass"}),
            "result": json.dumps({"diff": "+def g"})},
            tokenCount={"inputTokens": 300, "outputTokens": 20}),
        bubble("b6", 2, "Renamed `f` to `g`.", createdAt="2026-03-01T10:00:09.000Z"),
    ]


def composer(cid: str, bubbles: list, name: str = "Rename function") -> dict:
    return {"_v": 10, "composerId": cid, "name": name, "createdAt": 1772359200000,
            "lastUpdatedAt": 1772359209000, "unifiedMode": "agent",
            "modelConfig": {"modelName": "claude-4-sonnet"},
            "fullConversationHeadersOnly": [{"bubbleId": b["bubbleId"], "type": b["type"]}
                                            for b in bubbles]}


def write_cursor_store(user_dir: Path, chats: dict, *, workspace_ids=("c-main",),
                       aichat_tabs=None, inline=None) -> None:
    """chats: {composer id: [bubbles]}. workspace_ids belong to FOLDER's workspace."""
    rows = {}
    for cid, bubbles in chats.items():
        rows[f"composerData:{cid}"] = composer(cid, bubbles)
        for b in bubbles:
            rows[f"bubbleId:{cid}:{b['bubbleId']}"] = b
    rows["unrelated:key"] = {"x": 1}
    _kv_db(user_dir / "globalStorage" / "state.vscdb", "cursorDiskKV", rows)
    _kv_db(user_dir / "globalStorage" / "state.vscdb", "ItemTable", {})

    ws = user_dir / "workspaceStorage" / WS_HASH
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "workspace.json").write_text(json.dumps({"folder": FOLDER_URI}), encoding="utf-8")
    all_composers = [{"composerId": c, "name": "x", "unifiedMode": "agent"} for c in workspace_ids]
    all_composers += list(inline or [])
    items = {"composer.composerData": {"allComposers": all_composers,
                                       "selectedComposerIds": list(workspace_ids)}}
    if aichat_tabs:
        items["workbench.panel.aichat.view.aichat.chatdata"] = {"tabs": aichat_tabs}
    _kv_db(ws / "state.vscdb", "ItemTable", items)


def transcript_lines(prompt: str) -> list:
    return [
        {"role": "user", "message": {"content": [{"type": "text", "text":
            "<timestamp>Tuesday, Jun 2, 2026, 11:20 AM (UTC+8)</timestamp>\n"
            f"<user_query>\n{prompt}\n</user_query>"}]}},
        {"role": "assistant", "message": {"content": [{"type": "text", "text": "Looking."}]}},
        {"role": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Write",
             "input": {"path": "/Users/x/my.app/new.py", "contents": "print(1)"}}]}},
        {"role": "assistant", "message": {"content": [{"type": "text", "text": "Created it."}]}},
        {"type": "turn_ended", "status": "success"},
    ]


def write_transcripts(projects_dir: Path, slug: str, sessions: dict, *, flat=(),
                      subagents=None) -> None:
    base = projects_dir / slug / "agent-transcripts"
    for sid, lines in sessions.items():
        target = base / f"{sid}.jsonl" if sid in flat else base / sid / f"{sid}.jsonl"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in lines) + "\n",
                          encoding="utf-8")
    for parent, agents in (subagents or {}).items():
        for aid, lines in agents.items():
            p = base / parent / "subagents" / f"{aid}.jsonl"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")
    (projects_dir / slug / "terminals").mkdir(parents=True, exist_ok=True)
    (projects_dir / slug / "terminals" / "1.txt").write_text("not history", encoding="utf-8")


# ------------------------------------------------------------------ cursor-agent CLI
CLI_PROMPT = ("<timestamp>Tuesday, Sep 15, 2026, 2:49 PM (UTC+2)</timestamp>\n"
              "<user_query>\nplease fix the widget\n</user_query>")


def cli_messages(prompt: str = CLI_PROMPT) -> list:
    return [
        {"role": "system", "content": "You are an AI coding assistant, powered by Cursor."},
        {"role": "user", "content": "<user_info>\nOS Version: darwin\n</user_info>"},
        b"\n\x20" + bytes(32) + b"\x12\x05binary protobuf row",
        {"role": "user", "content": [
            {"type": "text", "text": "<manually_attached_skills>\nfix-widget\n</manually_attached_skills>"},
            {"type": "text", "text": prompt}]},
        {"role": "assistant", "id": "msg_1", "content": [
            {"type": "reasoning", "text": "I will edit the file."},
            {"type": "text", "text": "Editing widget.ts."},
            {"type": "tool-call", "toolCallId": "call-1", "toolName": "Write",
             "args": {"path": "/tmp/proj/widget.ts", "contents": "fixed"}}]},
        {"role": "tool", "id": "call-1", "content": [
            {"type": "tool-result", "toolCallId": "call-1", "toolName": "Write", "result": "ok"}]},
        {"role": "assistant", "id": "msg_2", "content": [
            {"type": "tool-call", "toolCallId": "call-2", "toolName": "Shell",
             "input": {"command": "npm test"}}]},
        {"role": "tool", "content": [{"type": "tool-result", "toolCallId": "call-2",
                                      "output": {"type": "text", "value": "3 passed"}}]},
        {"role": "assistant", "id": "msg_3", "content": [{"type": "text", "text": "Widget patched."}]},
    ]


def write_cli_store(chat_dir: Path, messages: list, *, store_meta: dict | None = None,
                    checkpointed: bool = True) -> None:
    """A cursor-agent store.db. `checkpointed` removes the -wal/-shm sidecars the
    way an idle chat loses them, which plain mode=ro cannot open."""
    chat_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(chat_dir / "store.db")
    conn.execute("PRAGMA journal_mode=wal")
    conn.execute("CREATE TABLE IF NOT EXISTS blobs (id TEXT PRIMARY KEY, data BLOB)")
    conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    start = conn.execute("SELECT count(*) FROM blobs").fetchone()[0]
    for i, m in enumerate(messages, start):
        data = m if isinstance(m, bytes) else json.dumps(m, ensure_ascii=False).encode("utf-8")
        conn.execute("INSERT INTO blobs VALUES (?, ?)", (f"{i:064x}", data))
    if store_meta is not None:
        conn.execute("INSERT OR REPLACE INTO meta VALUES ('0', ?)",
                     (json.dumps(store_meta).encode("utf-8").hex(),))
    conn.commit()
    conn.close()
    if checkpointed:
        for side in ("store.db-wal", "store.db-shm"):
            (chat_dir / side).unlink(missing_ok=True)


CLI_CHAT = "59a98c57-0000-4000-8000-000000000001"
CLI_CWD = "/tmp/proj"


def make_cursor_cli(chats_dir: Path) -> Path:
    ws = chats_dir / "e0a7de0fcb37071e9b4f94368cae7ac1"
    chat = ws / CLI_CHAT
    write_cli_store(chat, cli_messages(), store_meta={
        "agentId": CLI_CHAT, "latestRootBlobId": "ab" * 32, "name": "Fix the widget",
        "mode": "agent", "createdAt": 1789483816808, "lastUsedModel": "gpt-5"})
    (chat / "meta.json").write_text(json.dumps({
        "schemaVersion": 1, "createdAtMs": 1789483816808, "updatedAtMs": 1789571433281,
        "hasConversation": True, "title": "Fix the widget", "cwd": CLI_CWD}), encoding="utf-8")
    # A sub-agent run: a store with no meta.json.
    write_cli_store(ws / "0c5cf32e-0000-4000-8000-000000000002",
                    [{"role": "user", "content": [{"type": "text", "text":
                                                   "<user_query>subagent</user_query>"}]}])
    # Opened, never prompted.
    empty = ws / "4663a5be-0000-4000-8000-000000000003"
    write_cli_store(empty, [])
    (empty / "meta.json").write_text(json.dumps({"hasConversation": False, "cwd": CLI_CWD}),
                                     encoding="utf-8")
    # meta.json that is valid JSON but not an object.
    broken = ws / "86cd7f43-0000-4000-8000-000000000004"
    write_cli_store(broken, cli_messages())
    (broken / "meta.json").write_text("null", encoding="utf-8")
    return chat
