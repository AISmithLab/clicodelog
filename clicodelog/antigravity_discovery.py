"""OS-specific local history endpoint discovery; no third-party dependencies."""

import json
import os
import re
from pathlib import Path

PROC_ROOT = Path("/proc")


def flag(command, name):
    match = re.search(r"--" + name + r'''(?:=|\s+)(?:"([^"]*)"|'([^']*)'|([^\s"']+))''', command)
    value = next((v for v in match.groups() if v is not None), "") if match else ""
    return value if not value.startswith("--") else ""


def powershell(script, run):
    # Windows PowerShell 5.1 otherwise uses the user's console code page.
    prefix = "$ErrorActionPreference='Stop'; [Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false); "
    return run(["powershell", "-NoProfile", "-NonInteractive", "-Command", prefix + script])


def _json_records(raw):
    try:
        value = json.loads(raw.lstrip("\ufeff"))
    except ValueError:
        return []
    if not isinstance(value, list):
        value = [value]
    return [v for v in value if isinstance(v, dict)]


def _processes(platform, run):
    if platform.startswith("win"):
        rows = _json_records(powershell(
            "Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'language_server*' } | "
            "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress", run))
        return [(str(v.get("ProcessId", "")), v.get("CommandLine")) for v in rows]
    return [p.split(None, 1) for p in run(["ps", "ax", "-o", "pid=,command="]).splitlines()
            if len(p.split(None, 1)) == 2]


def _addresses(address, port):
    if not str(port).isascii() or not str(port).isdigit() or not 0 < int(port) <= 65535:
        return []
    address = address.strip("[]")
    if address in ("::", "::1"):
        hosts = ["[::1]"]
    elif address in ("*", ""):
        hosts = ["127.0.0.1", "[::1]"]
    elif address in ("0.0.0.0", "127.0.0.1"):
        hosts = ["127.0.0.1"]
    else:
        return []
    return [f"http://{host}:{int(port)}" for host in hosts]


def proc_listeners(pid):
    """Linux fallback when lsof is absent: match only this process's socket inodes."""
    root = PROC_ROOT / str(pid)
    inodes = set()
    try:
        for fd in (root / "fd").iterdir():
            try:
                link = os.readlink(fd)
                if link.startswith("socket:[") and link.endswith("]"):
                    inodes.add(link[8:-1])
            except OSError:
                continue
    except OSError:
        return []
    listeners = []
    for table in ("tcp", "tcp6"):
        try:
            rows = (root / "net" / table).read_text(encoding="utf-8").splitlines()[1:]
        except OSError:
            continue
        for row in rows:
            columns = row.split()
            if len(columns) < 10 or columns[3] != "0A" or columns[9] not in inodes:
                continue
            try:
                address, port = columns[1].split(":")
                if table == "tcp":
                    host = {"00000000": "0.0.0.0", "0100007F": "127.0.0.1"}.get(address)
                else:
                    host = {"0" * 32: "::", "0" * 24 + "01000000": "::1"}.get(address)
                if host:
                    listeners.extend(_addresses(host, int(port, 16)))
            except ValueError:
                continue
    return listeners


def _listeners(pid, platform, run):
    if platform.startswith("win"):
        rows = _json_records(powershell(
            f"Get-NetTCPConnection -OwningProcess {int(pid)} -State Listen | "
            "Select-Object LocalAddress,LocalPort | ConvertTo-Json -Compress", run))
        return [url for row in rows for url in
                _addresses(str(row.get("LocalAddress", "")), row.get("LocalPort", ""))]
    sockets = run(["lsof", "-nP", "-a", "-p", str(pid), "-iTCP", "-sTCP:LISTEN", "-Fn"])
    urls = []
    for line in sockets.splitlines():
        if line.startswith("n") and ":" in line:
            address, port = line[1:].rsplit(":", 1)
            urls.extend(_addresses(address, port))
    if not urls and platform.startswith("linux"):
        urls = proc_listeners(pid)
    return urls


def endpoints(platform, run, config_dir):
    out, seen = [], set()
    def add(url, token):
        if (url, token) not in seen:
            out.append((url, token))
            seen.add((url, token))
    for pid, command in _processes(platform, run):
        if (not pid.isascii() or not pid.isdigit() or not isinstance(command, str)
                or "language_server" not in command.lower() or "antigravity" not in command.lower()):
            continue
        token = flag(command, "csrf_token")
        for url in _addresses("127.0.0.1", flag(command, "port")) + _listeners(pid, platform, run):
            add(url, token)
    # Current IDE daemon logs live under the OS's application config directory.
    logs = config_dir / "Antigravity" / "logs"
    try:
        server = (logs / "language_server.log").read_text(encoding="utf-8", errors="replace")
        main = (logs / "main.log").read_text(encoding="utf-8", errors="replace")
        ports = re.findall(r"(?:Listening|listening).*?(?:port\s+|:)(\d+)", server)
        token = next((flag(line, "csrf_token") for line in reversed(main.splitlines())
                      if flag(line, "csrf_token")), "")
        if ports and token:
            for url in _addresses("127.0.0.1", ports[-1]):
                add(url, token)
    except OSError:
        pass
    return out
