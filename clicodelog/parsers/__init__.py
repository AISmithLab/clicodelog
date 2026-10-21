from .claude import parse_claude_conversation
from .codex import parse_codex_conversation
from .cursor import parse_cursor_conversation
from .gemini import parse_gemini_conversation
from .vscode import parse_vscode_conversation
from .copilot import parse_copilot_conversation
from .antigravity import parse_antigravity_conversation

# Sources whose files are not one-record-per-line logs (a VS Code mutation log,
# a Cursor export whose bubbles need their composer). Metadata scanning and
# full-text indexing read these through the parser instead of line by line.
WHOLE_FILE_PARSERS = {
    "cursor": parse_cursor_conversation,
    "vscode": parse_vscode_conversation,
    "copilot": parse_copilot_conversation,
    "antigravity": parse_antigravity_conversation,
}

PARSERS = {
    "claude-code": parse_claude_conversation,
    "codex": parse_codex_conversation,
    "gemini": parse_gemini_conversation,
    **WHOLE_FILE_PARSERS,
}

__all__ = [
    "PARSERS",
    "WHOLE_FILE_PARSERS",
    "parse_claude_conversation",
    "parse_codex_conversation",
    "parse_cursor_conversation",
    "parse_gemini_conversation",
    "parse_vscode_conversation",
    "parse_copilot_conversation",
    "parse_antigravity_conversation",
]
