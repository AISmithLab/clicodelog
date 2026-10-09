import os
from pathlib import Path

from .editors import cursor_projects_dir, cursor_user_dirs, first_existing, vscode_user_dirs

PACKAGE_DIR = Path(__file__).parent

# CLICODELOG_HOME moves the app's own state (backup, indexes, bookmarks) without
# moving where the agents' logs are read from — e.g. to run a second instance
# beside the one you use, or to keep the backup on another disk.
APP_DATA_DIR = (Path(os.environ["CLICODELOG_HOME"]).expanduser()
                if os.environ.get("CLICODELOG_HOME") else Path.home() / ".clicodelog")
DATA_DIR = APP_DATA_DIR / "data"
PROJECT_META_FILE = APP_DATA_DIR / "project_meta.json"

SYNC_INTERVAL = 3600  # seconds

# `source_dir` is what the UI reports as the source's location. Sources with a
# `syncer` are not a single directory tree: they name a module in this package
# whose sync() gathers from `source_dirs` (every candidate location on this OS).
SOURCES = {
    "claude-code": {
        "name": "Claude Code",
        "source_dir": Path.home() / ".claude" / "projects",
        "data_subdir": "claude-code",
    },
    "codex": {
        "name": "OpenAI Codex",
        "source_dir": Path.home() / ".codex" / "sessions",
        "data_subdir": "codex",
    },
    "gemini": {
        "name": "Google Gemini",
        "source_dir": Path.home() / ".gemini" / "tmp",
        "data_subdir": "gemini",
    },
    "cursor": {
        "name": "Cursor",
        "source_dirs": [d for d, _ in cursor_user_dirs()] + [cursor_projects_dir()],
        "data_subdir": "cursor",
        "syncer": "sync_cursor",
    },
    "vscode": {
        "name": "VS Code (Copilot Chat)",
        "source_dirs": [d for d, _ in vscode_user_dirs()],
        "data_subdir": "vscode",
        "syncer": "sync_vscode",
    },
}

for _cfg in SOURCES.values():
    if "source_dirs" in _cfg:
        _cfg["source_dir"] = first_existing(_cfg["source_dirs"])
