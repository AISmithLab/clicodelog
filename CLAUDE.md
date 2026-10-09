# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install from source (editable, changes take effect immediately)
uv pip install -e .

# Run the app
clicodelog
# or without installing:
uv run python -m clicodelog.cli

# Common flags
clicodelog --port 8080      # custom port (default: 6126)
clicodelog --no-sync        # skip initial sync (faster startup for dev)
clicodelog --debug          # debug logging to console as well as file

# Tests
uv run python -m pytest tests/ -q

# Build for distribution
uv build                    # produces dist/*.whl and dist/*.tar.gz
```

**Never test against port 6126** — that is the user's running instance. Use a
different port (`--port 6231`).

## Architecture

**clicodelog** is a local-first web app that backs up AI agent logs to
`~/.clicodelog/data/` and serves them via a FastAPI browser UI.

There are three layers, and keeping them straight is the main thing:

1. **The backup** (`sync.py`) — an additive, never-deleting copy of each source
   tool's log directory, made with APFS `clonefile` so it costs almost no disk.
   This is the durable source of truth. It deliberately keeps files the source
   tool has pruned, so it must never delete and never replace a file
   non-atomically.
2. **The metadata index** (`search_index.py`) — one small record per session,
   in `~/.clicodelog/search_index.json`. Derived state, rebuildable at any time.
   **Session and project listings are served from here, not from the files.**
3. **The content index** (`fts.py`) — SQLite FTS5 over message bodies in
   `~/.clicodelog/fts.db`. Also derived. Optional: it is only built on request,
   and refuses to build if it would not fit on disk.

### File map

```
clicodelog/
├── cli.py            # argparse entry point -> server.run_server()
├── app.py            # FastAPI setup, cache policy, template routes
├── config.py         # SOURCES dict, DATA_DIR, APP_DATA_DIR, constants
├── logging_setup.py  # rotating file log at ~/.clicodelog/clicodelog.log
├── storage.py        # atomic JSON read/write, quarantine, disk checks
├── scan.py           # ONE-PASS session metadata extraction (all 3 sources)
├── sync.py           # additive clone-backup, background + initial sync
├── sync_vscode.py    # VS Code: copies chatSessions/ + workspace.json only
├── sync_cursor.py    # Cursor: exports chats from state.vscdb, copies transcripts
├── sync_cursor_cli.py # Cursor CLI: exports ~/.cursor/chats/*/*/store.db
├── cursor_store.py   # read-only access to Cursor's SQLite databases
├── editors.py        # per-OS editor user dirs, URI -> path, safe names, slugs
├── editor_rows.py    # editor sources: session files + project keys
├── scan_parsed.py    # metadata for sources read through their parser
├── fts_extract.py    # FTS text extraction per source (line-based + whole-file)
├── search_index.py   # metadata index; backs listings, projects and search
├── metastore_stats.py # token-usage analytics queries over the metadata store
├── fts.py            # SQLite FTS5 content index, query sanitizer
├── sessions.py       # session listings (from the index)
├── projects.py       # project listings (from the index)
├── conversation.py   # conversation loading, pagination, LRU cache
├── bookmarks.py      # user-authored bookmarks (atomic, locked)
├── metadata.py       # custom project names/tags (atomic, locked)
├── utils.py          # path ids, id validation, per-source helpers
├── server.py         # port handling, startup, uvicorn
├── parsers/          # claude, codex, gemini, cursor (+ cursor_cli), vscode (+ vscode_log)
├── routes/           # projects, search, export, sync, sources, bookmarks, stats
├── static/css/*.css
├── static/js/*.js    # vanilla, no build step; vendor/ holds marked+purify+hljs
└── templates/        # index.html, view.html
```

The root `app.py` and `requirements.txt` are legacy Flask artifacts — ignore them.

## Rules that exist because something broke

- **`package-data` must include `static/js/vendor/*.js`.** That glob does not
  recurse; without the explicit vendor line every published wheel ships with no
  markdown renderer, no sanitizer and no syntax highlighting, and fails
  *silently* because `renderMarkdownInto` falls back to `textContent`.
  `tests/test_packaging.py` guards this.
- **Route handlers that touch the filesystem must be `def`, not `async def`.**
  FastAPI runs sync handlers in a threadpool and async ones on the event loop.
  Declaring them async made a single slow request freeze the entire server
  (measured: `/api/status` went from 2.5 ms to 12.29 s).
- **Never write user data non-atomically.** Use `storage.write_json`. Bookmarks
  and project metadata are the only irreplaceable data in the app; a corrupt
  file gets quarantined, never treated as empty.
- **Never unlink before copying in sync.** Use temp + `os.replace`. If the copy
  fails after an unlink, and the source tool already pruned that file, the
  backup was the only remaining copy.
- **Never pass user text to FTS5 `MATCH` raw.** Use `fts.build_match()`. FTS5's
  query grammar is not its tokenizer: `search.py` raises a syntax error, and 8
  of 11 realistic developer queries crash without quoting.
- **The FTS tokenizer is `tokenchars '_.-'`, not `'_./-'`.** Including `/` makes
  `routes/search.py` a single token, so typing `search.py` finds nothing.
- **Token counts must include `cache_read_input_tokens` and
  `cache_creation_input_tokens`.** Use `totalTokens()` in JS. Summing only
  input+output reported 0.9% of real usage.
- **Claude Code project directory names cannot be decoded.** It collapses `/`,
  `_` and `-` all into `-`. Treat the directory name as an opaque id and take
  the display name from the session's recorded `cwd`.
- **Validate route ids with `is_safe_id` / `safe_child`** before joining them
  into a filesystem path.
- **Always pass `encoding="utf-8"` to `open()` / `read_text()` / `write_text()`.**
  Windows defaults to cp1252, which mangles non-Latin text or fails to write
  it. `tests/test_platform.py` runs the pipeline with `EncodingWarning` as an
  error to catch this.
- **Never open Cursor's SQLite files (`state.vscdb`, CLI `store.db`) except via
  `cursor_store.open_readonly`.** Even `mode=ro` creates `-wal`/`-shm` sidecars
  in Cursor's directory; it uses `immutable=1` when no `-wal` exists and
  `mode=ro` only when one does.
- **Markdown export must keep content inside its block.** Tool output is fenced
  with `_fence()` (longer than any backtick run in it) and message text goes
  through `_closed()`. A fixed ``` fence broke 93 of 153 real Copilot exports.
- **Editor locations are per-OS.** Resolve them through `editors.py`, never
  hard-code `~/Library/Application Support`.

## Adding a source

1. Add an entry to `SOURCES` in `config.py`. A source that is not one plain
   directory tree (like Cursor's SQLite store) names a `syncer` module with
   `sync(dest_dir, stats, copy)` and `source_roots()`.
2. Add a reader branch in `scan.py` and an entry-shape branch in
   `metastore._row_for`. A source whose files only make sense parsed whole
   (VS Code, Cursor) goes in `WHOLE_FILE_PARSERS` instead, and scanning and
   indexing then use its parser.
3. Add a parser module in `parsers/` and export it from `parsers/__init__.py`.
4. Add an extractor to `fts_extract.EXTRACT` (whole-file sources need none).
5. Add the source to the parametrised contract test in `tests/test_sources.py`.

Per-source behaviour is still spread across those call sites rather than living
behind one adapter interface; consolidating it into a `SourceAdapter` is the
obvious next refactor.

## File decomposition rules

**Max file size: ~300 lines.** When a file approaches this, split it before
adding more. This applies to Python, JS and CSS.

## Frontend

`static/js/` is vanilla JS with no build step, loaded as ordered script tags.
State lives in globals declared in `state.js`. All DOM construction uses
`createElement`/`textContent` — never `innerHTML` with dynamic data. The one
exception is sanitized markdown, which goes through DOMPurify.
