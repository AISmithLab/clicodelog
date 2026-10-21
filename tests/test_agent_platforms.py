"""OS discovery contracts, including hosts unlike the machine running pytest."""

import json
import os
import sys
import socket
from pathlib import Path
from types import SimpleNamespace

import pytest

from clicodelog import antigravity_client as api
from clicodelog import antigravity_discovery as discovery
from clicodelog.agent_paths import antigravity_roots, copilot_sessions_dir


@pytest.mark.parametrize("platform", ["darwin", "linux", "win32"])
def test_default_agent_roots_follow_the_user_home(monkeypatch, tmp_path, platform):
    for name in ("COPILOT_HOME", "CLICODELOG_COPILOT_DIR", "CLICODELOG_ANTIGRAVITY_DIRS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(api, "sys", SimpleNamespace(platform=platform))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "Zoë 日本"))
    home = Path.home()
    assert copilot_sessions_dir() == home / ".copilot" / "session-state"
    assert antigravity_roots() == [home / ".gemini" / name for name in
                                  ("antigravity", "antigravity-ide", "antigravity-cli")]


@pytest.mark.parametrize("separator", [":", ";"])
def test_custom_root_list_uses_host_separator(monkeypatch, tmp_path, separator):
    monkeypatch.setattr(os, "pathsep", separator)
    # Use portable relative names: a simulated ':' separator must not split
    # the drive letter of the Windows machine actually running this test.
    paths = [Path("user data 日本"), Path("second")]
    monkeypatch.setenv("CLICODELOG_ANTIGRAVITY_DIRS", separator.join(map(str, paths)))
    assert antigravity_roots() == paths


def test_windows_discovery_handles_quoted_flags_and_unicode(monkeypatch, tmp_path):
    monkeypatch.delenv("CLICODELOG_ANTIGRAVITY_URL", raising=False)
    monkeypatch.setattr(api, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(api, "config_root", lambda: tmp_path)
    def run(args):
        script = args[-1]
        if "Win32_Process" in script:
            return json.dumps({"ProcessId": 123, "CommandLine":
                '"C:\\Users\\Zoë 日本\\Antigravity\\language_server_windows_x64.exe" --csrf_token "token-a"'})
        if "Get-NetTCPConnection" in script:
            # JSON stays unambiguous regardless of locale/PowerShell formatting.
            return json.dumps([{"LocalPort": 3456, "LocalAddress": "127.0.0.1"}])
        return ""
    monkeypatch.setattr(api, "_run", run)
    clients = api.discover()
    assert [(c.url, c.token) for c in clients] == [("http://127.0.0.1:3456", "token-a")]


@pytest.mark.parametrize("platform,address,url", [
    ("darwin", "[::1]", "http://[::1]:3456"),
    ("linux", "127.0.0.1", "http://127.0.0.1:3456"),
])
def test_unix_discovery_uses_listener_address(monkeypatch, tmp_path, platform, address, url):
    monkeypatch.delenv("CLICODELOG_ANTIGRAVITY_URL", raising=False)
    monkeypatch.setattr(api, "sys", SimpleNamespace(platform=platform))
    monkeypatch.setattr(api, "config_root", lambda: tmp_path)
    monkeypatch.setattr(api, "_run", lambda args: '42 /app/antigravity/language_server --csrf_token="token-a"'
                        if args[0] == "ps" else f"n{address}:3456\n")
    assert [(c.url, c.token) for c in api.discover()] == [(url, "token-a")]


def test_linux_without_lsof_matches_only_owned_listening_sockets(monkeypatch, tmp_path):
    proc = tmp_path / "proc"
    (proc / "42" / "fd").mkdir(parents=True)
    (proc / "42" / "fd" / "7").touch()
    (proc / "42" / "net").mkdir()
    header = "sl local_address rem_address st tx_queue tr retrnsmt uid timeout inode\n"
    # An owned listener, an unrelated process's listener, and an established socket.
    rows = ("0: 0100007F:0D80 00000000:0000 0A 0:0 0:0 0 1000 0 123\n"
            "1: 0100007F:0D81 00000000:0000 0A 0:0 0:0 0 1000 0 999\n"
            "2: 0100007F:0D82 00000000:0000 01 0:0 0:0 0 1000 0 123\n")
    (proc / "42" / "net" / "tcp").write_text(header + rows, encoding="utf-8")
    monkeypatch.setattr(discovery, "PROC_ROOT", proc)
    monkeypatch.setattr(discovery.os, "readlink", lambda path: "socket:[123]")
    def run(args):
        return "42 /app/antigravity/language_server --csrf_token token-a" if args[0] == "ps" else ""
    assert discovery.endpoints("linux", run, tmp_path) == [("http://127.0.0.1:3456", "token-a")]


@pytest.mark.parametrize("platform", ["darwin", "linux", "win32"])
def test_daemon_logs_follow_os_config_directory(monkeypatch, tmp_path, platform):
    from clicodelog.editors import config_root
    config = config_root(platform, {"APPDATA": str(tmp_path / "Roaming"),
                                    "XDG_CONFIG_HOME": str(tmp_path / "xdg")}, tmp_path)
    logs = config / "Antigravity" / "logs"
    logs.mkdir(parents=True)
    (logs / "language_server.log").write_text("Listening on port 3456\n", encoding="utf-8")
    (logs / "main.log").write_text('--csrf_token="token-a"\n', encoding="utf-8")
    assert discovery.endpoints(platform, lambda args: "", config) == [("http://127.0.0.1:3456", "token-a")]


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Needs native Linux /proc sockets")
def test_native_linux_proc_can_find_our_listener():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        expected = f"http://127.0.0.1:{listener.getsockname()[1]}"
        assert expected in discovery.proc_listeners(os.getpid())


@pytest.mark.skipif(not sys.platform.startswith("win"), reason="Needs native Windows PowerShell")
def test_native_windows_powershell_emits_utf8():
    from clicodelog.antigravity_discovery import powershell
    result = powershell("([string][char]0x65e5 + [string][char]0x672c) | ConvertTo-Json -Compress", api._run)
    assert json.loads(result) == "日本"


@pytest.mark.skipif(not sys.platform.startswith("win"), reason="Needs native Windows socket discovery")
def test_native_windows_can_find_our_listener():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        expected = f"http://127.0.0.1:{listener.getsockname()[1]}"
        assert expected in discovery._listeners(str(os.getpid()), "win32", api._run)
