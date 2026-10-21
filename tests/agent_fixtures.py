"""Synthetic upstream-shaped histories; no private conversations.

Schemas: github/copilot-sdk nodejs/src/generated/session-events.ts;
jkfujinami/antigravity-client src/proto_generated/exa/{cortex_pb,language_server_pb}.
"""

import json

CWD = "/tmp/日本 project"
SID = "sample-session"
STAMP = "2026-10-20T12:00:00Z"


def copilot_events():
    data = [
        ("session.start", {"sessionId": SID, "context": {"cwd": CWD}, "startTime": STAMP,
                           "selectedModel": "claude-sonnet-4.5"}),
        ("session.title_changed", {"title": "Fix the widget"}),
        ("user.message", {"content": "fix the widget 日本"}),
        ("assistant.reasoning", {"content": "Inspect then edit.", "reasoningId": "r1"}),
        ("assistant.message", {"messageId": "a1", "content": "Editing it.", "toolRequests": [
            {"toolCallId": "t1", "name": "edit", "arguments": {"path": CWD + "/widget.py"}}]}),
        ("tool.execution_start", {"toolCallId": "t1", "toolName": "edit",
                                  "arguments": {"path": CWD + "/widget.py"}}),
        ("tool.execution_complete", {"toolCallId": "t1", "success": True,
                                     "result": {"content": "patched 日本"}}),
        ("assistant.usage", {"model": "claude-sonnet-4.5", "inputTokens": 12,
                             "outputTokens": 8, "cacheReadTokens": 90, "cacheWriteTokens": 4}),
        ("assistant.message_delta", {"deltaContent": "Should not duplicate"}),
        ("assistant.turn_end", {}),
        ("session.model_change", {"newModel": "gpt-5"}),
        ("assistant.message", {"messageId": "a2", "content": "Widget fixed.", "outputTokens": 3}),
    ]
    return [{"id": f"e{i}", "type": kind, "timestamp": STAMP, "data": payload}
            for i, (kind, payload) in enumerate(data)]


def antigravity_snapshot():
    def step(kind, key, value, usage=None, call=None):
        meta = {"createdAt": STAMP, "generatorModel": "MODEL_GEMINI_3_PRO"}
        if usage:
            meta["modelUsage"] = usage
        if call:
            meta["toolCall"] = call
        return {"type": "CORTEX_STEP_TYPE_" + kind, "metadata": meta, key: value}
    call = {"id": "t1", "name": "run_command", "argumentsJson": '{"command":"pytest"}'}
    return {"summary": {"summary": "Fix the widget", "createdTime": STAMP,
                        "workspaces": [{"workspaceFolderAbsoluteUri": "file:///tmp/日本%20project"}]},
            "trajectory": {"cascadeId": SID, "steps": [
                step("USER_INPUT", "userInput", {"items": [{"text": "fix the widget 日本"}]}),
                step("PLANNER_RESPONSE", "plannerResponse", {"modifiedResponse": "Running tests.",
                    "thinking": "Inspect then edit.", "toolCalls": [call]},
                    {"messageId": "a1", "inputTokens": "12", "outputTokens": "8",
                     "cacheReadTokens": "90", "cacheWriteTokens": "4"}),
                step("RUN_COMMAND", "runCommand", {"commandLine": "pytest", "cwd": CWD,
                    "combinedOutput": {"full": "3 passed 日本"}}, call=call),
                step("WRITE_TO_FILE", "writeToFile", {"targetFileUri": "file:///tmp/widget.py",
                                                       "codeContent": ["print('hi')"]}),
                step("CHECKPOINT", "checkpoint", {"snapshot": "internal"}),
                step("PLANNER_RESPONSE", "plannerResponse", {"response": "Widget fixed."}),
            ]}}


def write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")


def make_copilot(root):
    path = root / "copilot" / SID / "events.jsonl"
    write_jsonl(path, copilot_events())
    return path


def make_antigravity(root):
    path = root / "antigravity" / "conversations" / (SID + ".trajectory.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(antigravity_snapshot(), ensure_ascii=False), encoding="utf-8")
    return path
