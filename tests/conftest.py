import pytest

from clicodelog import conversation, fts, metastore

# Every module that binds DATA_DIR / DB_FILE at import time.
_DATA_DIR_MODULES = ("config", "metastore", "sessions", "conversation", "projects", "sync",
                     "fts", "routes.sources", "routes.sync")


def _reset_conn():
    if hasattr(metastore._local, "conn"):
        metastore._local.conn.close()
        del metastore._local.conn
    for key in ("ro", "rw"):
        if getattr(fts._local, key, None) is not None:
            getattr(fts._local, key).close()
            setattr(fts._local, key, None)


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """A whole app instance in tmp_path: its own backup dir and databases, and
    editor locations pointed at tmp_path/vscode-user, tmp_path/cursor-user and
    tmp_path/cursor-projects, tmp_path/cursor-chats. Nothing in the real home directory is touched."""
    data = tmp_path / "app" / "data"
    for mod in _DATA_DIR_MODULES:
        monkeypatch.setattr(f"clicodelog.{mod}.DATA_DIR", data, raising=False)
    monkeypatch.setattr("clicodelog.metastore.DB_FILE", tmp_path / "app" / "meta.db")
    monkeypatch.setattr("clicodelog.metastore.APP_DATA_DIR", tmp_path / "app")
    monkeypatch.setattr("clicodelog.fts.DB_FILE", tmp_path / "app" / "fts.db")
    monkeypatch.setattr("clicodelog.fts.APP_DATA_DIR", tmp_path / "app")
    monkeypatch.setenv("CLICODELOG_VSCODE_USER_DIRS", str(tmp_path / "vscode-user"))
    monkeypatch.setenv("CLICODELOG_CURSOR_USER_DIRS", str(tmp_path / "cursor-user"))
    monkeypatch.setenv("CLICODELOG_CURSOR_PROJECTS_DIR", str(tmp_path / "cursor-projects"))
    monkeypatch.setenv("CLICODELOG_CURSOR_CLI_DIR", str(tmp_path / "cursor-chats"))
    _reset_conn()
    conversation.clear_cache()
    yield {"root": tmp_path, "data": data,
           "vscode_user": tmp_path / "vscode-user",
           "cursor_user": tmp_path / "cursor-user",
           "cursor_projects": tmp_path / "cursor-projects",
           "cursor_chats": tmp_path / "cursor-chats"}
    _reset_conn()
    conversation.clear_cache()
