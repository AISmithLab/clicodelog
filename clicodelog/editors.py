"""Where VS Code-family editors keep their data, on every platform.

VS Code and Cursor are both Electron apps built on the same workbench, so they
share one layout under a per-OS "user data" directory:

    macOS    ~/Library/Application Support/<App>/User
    Windows  %APPDATA%\\<App>\\User
    Linux    $XDG_CONFIG_HOME/<App>/User   (default ~/.config/<App>/User)

Inside it, `workspaceStorage/<hash>/` holds per-workspace state (with a
`workspace.json` naming the folder) and `globalStorage/` holds the rest.

Paths and URIs recorded in that data come from whatever machine wrote them, not
the one reading them, so URI decoding here never consults the host OS: a Windows
`file:///c%3A/src` decodes to `c:\\src` even when read on a Mac.
"""

import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

# Environment overrides, os.pathsep-separated. Useful for portable installs,
# unusual profiles, and tests.
ENV_VSCODE = "CLICODELOG_VSCODE_USER_DIRS"
ENV_CURSOR = "CLICODELOG_CURSOR_USER_DIRS"
ENV_CURSOR_PROJECTS = "CLICODELOG_CURSOR_PROJECTS_DIR"
ENV_CURSOR_CLI = "CLICODELOG_CURSOR_CLI_DIR"

# (app folder name, prefix used for its workspaces in the backup). Stable VS
# Code gets no prefix; the others are prefixed so their workspace hashes can
# never collide with each other.
VSCODE_FLAVOURS = (("Code", ""), ("Code - Insiders", "insiders-"), ("VSCodium", "vscodium-"))
CURSOR_FLAVOURS = (("Cursor", ""),)


def config_root(platform: str | None = None, env: dict | None = None,
                home: Path | None = None) -> Path:
    """The per-user application config directory for this OS."""
    platform = platform or sys.platform
    env = os.environ if env is None else env
    home = home or Path.home()
    if platform == "darwin":
        return home / "Library" / "Application Support"
    if platform.startswith("win") or platform == "cygwin":
        appdata = env.get("APPDATA")
        return Path(appdata) if appdata else home / "AppData" / "Roaming"
    xdg = env.get("XDG_CONFIG_HOME")
    return Path(xdg) if xdg else home / ".config"


def _override(var: str) -> list[Path] | None:
    raw = os.environ.get(var)
    if not raw:
        return None
    return [Path(p).expanduser() for p in raw.split(os.pathsep) if p.strip()]


def user_dirs(flavours, env_var: str) -> list[tuple[Path, str]]:
    """Every candidate `<App>/User` directory, with its backup prefix."""
    forced = _override(env_var)
    if forced is not None:
        return [(p, "" if i == 0 else f"alt{i}-") for i, p in enumerate(forced)]
    root = config_root()
    return [(root / app / "User", prefix) for app, prefix in flavours]


def vscode_user_dirs() -> list[tuple[Path, str]]:
    return user_dirs(VSCODE_FLAVOURS, ENV_VSCODE)


def cursor_user_dirs() -> list[tuple[Path, str]]:
    return user_dirs(CURSOR_FLAVOURS, ENV_CURSOR)


def cursor_home(platform: str | None = None, env: dict | None = None,
                home: Path | None = None) -> Path:
    """Cursor's own dot-directory, which holds agent transcripts and the CLI
    agent's chats. ~/.cursor on every OS (%USERPROFILE%\\.cursor on Windows);
    Cursor honours CURSOR_CONFIG_DIR, and on Linux $XDG_CONFIG_HOME/cursor when
    that already exists."""
    platform = platform or sys.platform
    env = os.environ if env is None else env
    home = home or Path.home()
    if env.get("CURSOR_CONFIG_DIR"):
        return Path(env["CURSOR_CONFIG_DIR"]).expanduser()
    if not (platform == "darwin" or platform.startswith("win")) and env.get("XDG_CONFIG_HOME"):
        xdg = Path(env["XDG_CONFIG_HOME"]) / "cursor"
        if xdg.is_dir():
            return xdg
    return home / ".cursor"


def cursor_projects_dir() -> Path:
    """Where Cursor writes agent transcripts (<home>/projects/<slug>/agent-transcripts)."""
    forced = _override(ENV_CURSOR_PROJECTS)
    return forced[0] if forced else cursor_home() / "projects"


def cursor_cli_chats_dir() -> Path:
    """Where the cursor-agent CLI keeps chats (<home>/chats/<hash>/<id>/store.db)."""
    forced = _override(ENV_CURSOR_CLI)
    return forced[0] if forced else cursor_home() / "chats"


def first_existing(paths: list[Path]) -> Path:
    for p in paths:
        if p.exists():
            return p
    return paths[0]


# ------------------------------------------------------------------ uris
_DRIVE = re.compile(r"^/?([A-Za-z]):(.*)$")


def uri_to_path(uri) -> str:
    """Decode a workbench URI into a displayable path.

    file:///Users/x/p             -> /Users/x/p
    file:///c%3A/Users/x/p        -> c:\\Users\\x\\p
    file://server/share/p         -> \\\\server\\share\\p
    vscode-remote://ssh-remote%2Bbox/home/x/p -> ssh-remote+box:/home/x/p
    """
    if isinstance(uri, dict):              # a serialised URI object
        uri = uri.get("external") or uri.get("fsPath") or uri.get("path") or ""
    if not isinstance(uri, str) or not uri:
        return ""
    if "://" not in uri:
        return uri                         # already a path
    parts = urlsplit(uri)
    path = unquote(parts.path)
    if parts.scheme == "file":
        if parts.netloc:                   # UNC share
            return "\\\\" + parts.netloc + path.replace("/", "\\")
        m = _DRIVE.match(path)
        if m:
            return f"{m.group(1)}:{m.group(2) or '/'}".replace("/", "\\")
        return path
    host = unquote(parts.netloc)
    return f"{host}:{path}" if host else path


def workspace_folder(ws_dir: Path) -> str:
    """The folder (or .code-workspace file) a workspaceStorage entry belongs to."""
    try:
        with open(ws_dir / "workspace.json", "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return ""
    if not isinstance(data, dict):
        return ""
    return uri_to_path(data.get("folder") or data.get("workspace") or "")


_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
             *(f"LPT{i}" for i in range(1, 10))}


def safe_name(value: str, fallback: str = "unknown") -> str:
    """A string that is safe as one path component on every OS — including
    Windows, which refuses device names such as CON or NUL.txt."""
    cleaned = _UNSAFE.sub("_", str(value or "")).strip(".")[:150] or fallback
    if cleaned.split(".")[0].upper() in _RESERVED:
        cleaned = "_" + cleaned
    return cleaned


def cursor_slug(folder: str) -> str:
    """Cursor's ~/.cursor/projects/<slug> name for a workspace folder.

    Path separators, dots and other punctuation all collapse to "-", so like
    Claude Code's project directories this is one-way: only ever compare slugs,
    never try to decode one back to a path.
    """
    s = re.sub(r"[^A-Za-z0-9]+", "-", folder or "").strip("-")
    return s or "_no-workspace"
