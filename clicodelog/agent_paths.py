"""History locations for standalone agents (resolved on each sync)."""

import os
from pathlib import Path


def copilot_sessions_dir() -> Path:
    forced = os.environ.get("CLICODELOG_COPILOT_DIR")
    if forced:
        return Path(forced).expanduser()
    home = Path(os.environ.get("COPILOT_HOME", str(Path.home() / ".copilot"))).expanduser()
    return home / "session-state"


def antigravity_roots() -> list[Path]:
    forced = os.environ.get("CLICODELOG_ANTIGRAVITY_DIRS")
    if forced:
        return [Path(p).expanduser() for p in forced.split(os.pathsep) if p.strip()]
    home = Path.home() / ".gemini"
    return [home / name for name in ("antigravity", "antigravity-ide", "antigravity-cli")]
