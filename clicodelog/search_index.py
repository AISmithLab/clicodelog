"""Compatibility surface over the SQLite metadata store.

The index used to be one JSON blob held entirely in memory — 68.6 MB resident
for 15,924 sessions, loaded at startup, when a project click needs only that
project's rows. Storage moved to `metastore` (SQLite); this module keeps the
old function names so callers did not all have to change at once.

`entries()` is deliberately NOT re-exported: materialising every row is the
exact thing that cost the memory. Use `iter_entries()` to stream, or one of the
targeted queries in `metastore`.
"""

from .metastore import (  # noqa: F401
    DB_FILE,
    connect,
    count,
    entry_for_session,
    is_ready,
    iter_entries,
    path_for_session_id,
    path_project_map,
    project_session_count,
    projects_for_source,
    refresh_index,
    search_index,
    session_files,
    sessions_for_project,
    subagent_sessions,
    summaries_for,
)
from .metastore_stats import json_counter_totals, usage_by, usage_totals  # noqa: F401

__all__ = [
    "DB_FILE", "connect", "count", "entry_for_session", "is_ready", "iter_entries",
    "json_counter_totals", "path_for_session_id", "path_project_map", "project_session_count",
    "projects_for_source", "refresh_index",
    "search_index", "session_files", "sessions_for_project", "subagent_sessions",
    "summaries_for", "usage_by", "usage_totals",
]
