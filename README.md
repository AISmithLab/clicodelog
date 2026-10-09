<div align="center">
  <div align="center">
<img width="220px" src="https://raw.githubusercontent.com/monk1337/clicodelog/refs/heads/main/screenshots/logo.png">
</div>

<div align="center">

<h3>Your AI coding agent just edited 30 files across 6 directories.<br>What actually happened?</h3>

<p>
Browse every session from Claude Code, OpenAI Codex, Gemini CLI, Cursor and
VS Code (GitHub Copilot Chat) — the thinking, the tool calls, the file changes,
the token costs. On macOS, Windows and Linux.
All in one local interface. Nothing leaves your machine.
</p>

</div>

<p>
  <a href="#features">Features</a> •
  <a href="#supported-tools">Supported Tools</a> •
  <a href="#installation">Installation</a> •
  <a href="#usage">Usage</a> •
  <a href="#screenshots">Screenshots</a>
</p>

<p>
  <img src="https://img.shields.io/badge/Python-3.10+-blue.svg" alt="Python 3.10+" />
  <img src="https://img.shields.io/badge/FastAPI-0.104+-00c7b7.svg" alt="FastAPI" />
  <img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License MIT" />
  <a href="http://makeapullrequest.com">
    <img src="https://img.shields.io/badge/PRs-welcome-brightgreen.svg?style=flat-square" alt="PRs Welcome" />
  </a>
</p>
</div>

<!-- <div align="center">
<table>
<tr>
<td align="center">
<img src="screenshots/claude.png" width="80" alt="Claude Code"><br>
<sub><b>Claude Code</b></sub>
</td>
<td align="center">
<img src="screenshots/codex.png" width="80" alt="OpenAI Codex"><br>
<sub><b>OpenAI Codex</b></sub>
</td>
<td align="center">
<img src="screenshots/gemini.png" width="80" alt="Gemini CLI"><br>
<sub><b>Gemini CLI</b></sub>
</td>
</tr>
</table>
</div> -->

---

| Light Mode | Dark Mode |
|------------|-----------|
| ![Light Mode](screenshots/light.png) | ![Dark Mode](screenshots/dark.png) |


---

## ✨ What's New

A big round of performance, search and correctness work. Highlights:

<table>
<tr>
<td>🧩</td>
<td><b>Cursor and VS Code support</b><br>Cursor chats and agent transcripts and VS Code GitHub Copilot Chat sessions are backed up, browsable, searchable and exportable, the same as the CLI agents. Each editor is found in its standard location on macOS, Windows and Linux.</td>
</tr>
<tr>
<td>🔎</td>
<td><b>Full-text search inside conversations</b><br>Search what was actually said and done, not just session titles — powered by SQLite FTS5 with ranked results, highlighted snippets, and a click straight to the matching message. Opt-in, and it shows the index size before building.</td>
</tr>
<tr>
<td>⚡</td>
<td><b>Everything got dramatically faster</b><br>Opening a large project went from <b>15.5s → 0.25s</b>. A search that used to scan the whole corpus for 15.8s now answers in <b>11ms</b>. One slow request no longer freezes the entire app.</td>
</tr>
<tr>
<td>🪶</td>
<td><b>Loads only what's on screen</b><br>Sessions and messages are fetched on demand instead of all at once, so memory stays around <b>22MB</b> no matter how big your history gets. Opening a 966MB session used to need 604MB of RAM — now it's 68MB.</td>
</tr>
<tr>
<td>📊</td>
<td><b>Usage analytics</b><br>Token spend by day, project, model and tool. Now counts <b>cache reads and cache writes</b> — the old totals left them out, under-reporting real usage by over 100x.</td>
</tr>
<tr>
<td>🗂️</td>
<td><b>File-edit archaeology</b><br>"When did an agent last touch <code>auth.py</code>?" Search every session for a file and jump to the exact edit.</td>
</tr>
<tr>
<td>▶️</td>
<td><b>Jump back into a session</b><br>One click copies <code>cd &lt;project&gt; &amp;&amp; claude --resume &lt;id&gt;</code> so you can pick up where you left off, plus a copy-to-open-in-editor command.</td>
</tr>
<tr>
<td>📝</td>
<td><b>Markdown export</b><br>Export a conversation as Markdown — fenced code blocks, collapsible thinking and tool calls — ready to paste into an issue or PR.</td>
</tr>
<tr>
<td>♊</td>
<td><b>Gemini CLI works again</b><br>Gemini changed its log format and the source had gone quietly empty. Fully re-supported — and the app now warns you when a source has files it can't read, instead of just showing nothing.</td>
</tr>
<tr>
<td>🔗</td>
<td><b>Shareable links and a working back button</b><br>Every project and session has its own URL. Refresh keeps your place, and browser back/forward behave.</td>
</tr>
<tr>
<td>🔒</td>
<td><b>Security fixes</b><br>Removed a permissive CORS policy that let any website you visited read your local conversation history. Startup no longer force-kills unrelated processes holding the port, and binding to a non-loopback <code>--host</code> now warns loudly.</td>
</tr>
<tr>
<td>🛟</td>
<td><b>Your data is safer</b><br>Bookmarks and project settings are written atomically with backups, and a corrupt file is quarantined rather than silently reset to empty. Sync never removes a backup before its replacement is safely in place.</td>
</tr>
</table>

## Installation

clicodelog is installed from source. It needs Python 3.10+ and works the same on
macOS, Windows and Linux.

```bash
git clone https://github.com/monk1337/clicodelog.git
cd clicodelog
uv tool install -e .       # puts the `clicodelog` command on your PATH
# or, inside a virtualenv:
pip install -e .
```

The install is editable, so `git pull` is all an upgrade takes. Without
installing anything, `uv run python -m clicodelog.cli` runs it straight from the
checkout.

---

## Usage

After installation, simply run:

```bash
clicodelog
```

The app will:
- Auto-kill any process on port **6126** (if occupied)
- Sync data from all AI coding agent sources
- Start a web server at **http://localhost:6126**

### Command Options

```bash
clicodelog --help               # Show all options
clicodelog --port 8080          # Use custom port
clicodelog --host 0.0.0.0       # Bind to all interfaces
clicodelog --no-sync            # Skip initial data sync
clicodelog --debug              # Run in debug mode
```

## Why Developers Use It

<table>
<tr>
<td>🧠</td>
<td><b>See the full chain of reasoning</b><br>Read the agent's thinking blocks, tool calls, and decisions in a clean conversation view. No more squinting at raw JSONL.</td>
</tr>
<tr>
<td>🔍</td>
<td><b>Track what actually ran</b><br>Every file read, shell command, and edit — laid out clearly so you can audit what the agent did before you commit.</td>
</tr>
<tr>
<td>📊</td>
<td><b>Spot wasted tokens</b><br>Session-level token stats (input, output, cached) so you can see which runs were efficient and which went in circles.</td>
</tr>
<tr>
<td>🔀</td>
<td><b>All your agents, one place</b><br>Claude Code, Codex, Gemini CLI, Cursor and VS Code Copilot Chat side by side. Same interface, same workflow, no context switching.</td>
</tr>
<tr>
<td>🔒</td>
<td><b>Nothing leaves your machine</b><br>Fully local. Reads from your existing log directories, syncs to a local backup. No cloud, no accounts, no telemetry.</td>
</tr>
</table>

---

## Supported Tools

| Tool | Source Directory | Status |
|------|------------------|--------|
| **Claude Code** | `~/.claude/projects/` | ✅ Supported |
| **OpenAI Codex** | `~/.codex/sessions/` | ✅ Supported |
| **Gemini CLI** | `~/.gemini/tmp/` | ✅ Supported |
| **Cursor** (IDE + `cursor-agent` CLI) | Cursor's user dir (see below), `~/.cursor/projects/*/agent-transcripts/`, `~/.cursor/chats/` | ✅ Supported |
| **VS Code** (GitHub Copilot Chat) | VS Code's user dir (see below) | ✅ Supported |

Cursor and VS Code keep their data in the editor's per-OS user directory:

| OS | VS Code | Cursor |
|----|---------|--------|
| macOS | `~/Library/Application Support/Code/User` | `~/Library/Application Support/Cursor/User` |
| Windows | `%APPDATA%\Code\User` | `%APPDATA%\Cursor\User` |
| Linux | `~/.config/Code/User` (or `$XDG_CONFIG_HOME`) | `~/.config/Cursor/User` |

VS Code Insiders (`Code - Insiders`) and VSCodium are picked up as well. For a
portable install or an unusual profile location, point clicodelog at it with
`CLICODELOG_VSCODE_USER_DIRS`, `CLICODELOG_CURSOR_USER_DIRS` (each a list
separated by `:`, or `;` on Windows), `CLICODELOG_CURSOR_PROJECTS_DIR` or
`CLICODELOG_CURSOR_CLI_DIR`. Cursor's own `CURSOR_CONFIG_DIR` is honoured too.

`CLICODELOG_HOME` moves clicodelog's own data (backup, indexes, bookmarks; by
default `~/.clicodelog`), e.g. to run a second instance next to your usual one.

### Claude Code

- Sessions organized by project directory
- Displays summaries, messages, thinking blocks, and tool usage
- Shows model metadata and token usage

### OpenAI Codex

- Sessions organized by date (`YYYY/MM/DD/`)
- Groups sessions by working directory (cwd) as projects
- Displays messages, function calls, and reasoning blocks
- Filters out system prompts for cleaner inspection

### Gemini CLI

- Sessions stored as JSON files in `{hash}/chats/session-*.json`
- Groups sessions by project hash
- Displays messages, thoughts (thinking), and tool calls
- Shows token usage (input, output, cached)

### Cursor

Cursor keeps history in three places, and all of them are backed up:

- **The chat store** — `state.vscdb` (SQLite) in Cursor's user dir. This is the
  complete record: timestamps, model, token counts, thinking and tool output.
  It's read through a read-only connection (Cursor's database is never written),
  and each chat is saved as its own file in the backup. The older inline-composer
  and "aichat" formats are read too.
- **Agent transcripts** — the JSONL files Cursor writes under
  `~/.cursor/projects/<project>/agent-transcripts/`, sub-agents included. These
  are copied as they are.
- **The `cursor-agent` CLI** — one SQLite `store.db` per chat under
  `~/.cursor/chats/<workspace>/<chat>/`, with a `meta.json` naming its folder.
  Each chat is exported read-only, binary rows included, so the backup stays
  complete. Prompts, replies, reasoning, tool calls and their output are shown.
  Sub-agent runs are backed up but not listed.

A chat that appears in both is listed once, from the store, because it has more
detail. Chats are grouped by workspace folder. If you edit an earlier prompt,
Cursor drops every message after it; when that happens the previous copy is kept
next to the new one as `<id>.superseded-<time>.bak`.

### VS Code (GitHub Copilot Chat)

- Reads `workspaceStorage/<hash>/chatSessions/*.json` (older format) and
  `*.jsonl` (the current mutation-log format), plus chats from windows with no
  folder open
- Only chat files are copied; the rest of the editor's state is skipped
- Groups chats by workspace folder, including Remote-SSH and WSL folders
- Displays messages, thinking, tool calls with their exact arguments and
  output, file edits, model and token usage

---

### CLI Options

```bash
clicodelog --help               # Show help message
clicodelog --version            # Show version
clicodelog --port 8080          # Run on custom port (default: 6126)
clicodelog --host 0.0.0.0       # Bind to all interfaces (default: 127.0.0.1)
clicodelog --no-sync            # Skip initial data sync
clicodelog --debug              # Run in debug mode
```

**Note:** The app automatically kills any process running on the specified port before starting.

---

## How It Works

- **Startup sync** — Copies logs from source directories into `~/.clicodelog/data/`
- **Background sync** — Automatically refreshes every hour
- **Manual sync** — Trigger a sync for the active source via UI
- **Source switching** — Switch between Claude Code, Codex, Gemini CLI, Cursor and VS Code

---

## Data Storage

```
data/
├── claude-code/          # Claude Code backup
│   ├── -Users-project1/
│   │   ├── session1.jsonl
│   │   └── session2.jsonl
│   └── -Users-project2/
├── codex/                # OpenAI Codex backup
│   └── 2026/
│       └── 01/
│           ├── 16/
│           │   └── rollout-xxx.jsonl
│           └── 17/
├── gemini/               # Gemini CLI backup
│   ├── {project-hash-1}/
│   │   └── chats/
│   │       ├── session-2026-01-17T12-57-xxx.json
│   │       └── session-2026-01-17T13-04-xxx.json
│   └── {project-hash-2}/
├── cursor/               # Cursor backup
│   ├── composers/
│   │   └── {workspace-hash}/
│   │       └── {chat-id}.jsonl      # one chat exported from state.vscdb
│   ├── cli/
│   │   └── {workspace-hash}/
│   │       └── {chat-id}.jsonl      # one cursor-agent CLI chat
│   └── transcripts/
│       └── {project-slug}/
│           └── {id}/{id}.jsonl      # agent transcript (+ subagents/)
└── vscode/               # VS Code Copilot Chat backup
    └── {workspace-hash}/
        ├── workspace.json
        └── chatSessions/
            └── {session-id}.jsonl
```

---

## Controls

| Control | Action |
|---------|--------|
| Source dropdown | Switch between supported tools |
| 📥 Export | Download current session as .txt |
| 🔄 Sync | Manually refresh logs from source |
| ☀️ / 🌙 Theme | Toggle light/dark mode |

---

## Screenshots

| Light Mode | Dark Mode |
|------------|-----------|
| ![Light Mode](screenshots/light.png) | ![Dark Mode](screenshots/dark.png) |

---

## Project Structure

```
clicodelog/
├── cli.py              # Entry point
├── app.py              # FastAPI app, routes, cache policy
├── config.py           # SOURCES, paths, constants
├── sync.py             # Additive clone-backup of each source
├── scan.py             # One-pass session metadata extraction
├── metastore.py        # SQLite metadata store (listings, projects)
├── fts.py              # SQLite FTS5 full-text search
├── window.py           # Windowed reads of huge sessions
├── conversation.py     # Conversation loading + pagination
├── storage.py          # Atomic writes, disk checks
├── editors.py          # Per-OS editor locations, URI decoding
├── sync_cursor.py      # Cursor: store export + transcript copy
├── sync_vscode.py      # VS Code: copies chatSessions only
├── parsers/            # claude, codex, gemini, cursor, vscode
├── routes/             # projects, search, export, sync, sources, stats
├── static/             # Vanilla JS + CSS (no build step)
└── templates/          # index.html, view.html

~/.clicodelog/          # Created at runtime, never in the repo
├── data/               # Your synced logs (the durable backup)
├── meta.db             # Session metadata index
├── fts.db              # Full-text index (optional)
└── bookmarks.json      # Your bookmarks
```

---

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/sources` | GET | List available sources |
| `/api/sources/<id>` | POST | Set active source |
| `/api/projects?source=` | GET | List projects |
| `/api/projects/<id>/sessions?source=&limit=&offset=` | GET | List sessions (paged) |
| `/api/projects/<id>/sessions/<id>?source=&limit=&offset=` | GET | Fetch a window of a session |
| `/api/projects/<id>/sessions/<id>/subagents?source=` | GET | List sub-agent sessions |
| `/api/projects/<id>/sessions/<id>/export?fmt=txt\|md` | GET | Export as text or Markdown |
| `/api/projects/<id>/sessions/<id>/export-raw` | GET | Stream the exact source file |
| `/api/search?q=&source=&project=&role=&after=&before=` | GET | Search titles and message bodies |
| `/api/search/status?source=` | GET | Full-text index state and size estimate |
| `/api/search/build?source=&tool_cap=` | POST | Build the full-text index |
| `/api/stats?source=&group=day\|project\|model\|tool` | GET | Token usage analytics |
| `/api/files?path=&source=` | GET | Which sessions edited a file |
| `/api/bookmarks` | GET/POST | List or add bookmarks |
| `/api/sync?source=` | POST | Trigger sync |
| `/api/status?source=` | GET | Sync status and free disk |

---

## Requirements

- Python 3.10+
- FastAPI
- Uvicorn
- Jinja2

That's the whole runtime dependency list. Search, analytics and the metadata
store all run on the `sqlite3` module in the standard library — no extra
services, no database to install.

---

## Adding New Sources

To add support for another CLI-based AI tool, start with `config.py`:

```python
SOURCES = {
    "claude-code": {
        "name": "Claude Code",
        "source_dir": Path.home() / ".claude" / "projects",
        "data_subdir": "claude-code"
    },
    "codex": {
        "name": "OpenAI Codex",
        "source_dir": Path.home() / ".codex" / "sessions",
        "data_subdir": "codex"
    },
    "gemini": {
        "name": "Gemini CLI",
        "source_dir": Path.home() / ".gemini" / "tmp",
        "data_subdir": "gemini"
    },
    # Add new tool here
}
```

Then wire up the rest:

1. Add a reader branch in `scan.py` (metadata extraction).
2. Add an entry-shape branch in `metastore._row_for` (how projects are keyed).
3. Add a parser in `parsers/` and export it from `parsers/__init__.py`.
4. Add a text extractor to `fts._EXTRACT` so the source is searchable.
5. Add the source to the parametrised contract test in `tests/test_sources.py` —
   it asserts a source with files on disk yields at least one session, which is
   exactly the check that would have caught Gemini going silently empty.

```
 @misc{clicodelog2026,                                                                                                                                                      
    title        = {clicodelog: Unified Session Inspector for CLI AI Coding Agents},
    author       = {Pal, Ankit},                                                                                                                                             
    year         = {2026},
    howpublished = {\url{https://github.com/monk1337/clicodelog}},                                                                                                           
    note         = {Local web UI for browsing Claude Code, OpenAI Codex, Gemini CLI, Cursor and VS Code Copilot Chat session logs - thinking blocks, tool calls, file edits, and token costs}             
  }       
```
---

## License

MIT

---

<div align="center">
<sub>Built for inspecting what AI coding agents actually did.</sub>
</div>

```


