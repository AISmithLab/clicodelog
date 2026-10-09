"""Cross-platform behaviour: editor locations, URI decoding, text encodings."""

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from clicodelog import editors
from clicodelog.editors import config_root, cursor_slug, safe_name, uri_to_path
from clicodelog.utils import is_safe_id

HOME = Path("/home/u")


@pytest.mark.parametrize("platform, env, expected", [
    ("darwin", {}, HOME / "Library" / "Application Support"),
    ("win32", {"APPDATA": r"C:\Users\u\AppData\Roaming"}, Path(r"C:\Users\u\AppData\Roaming")),
    ("win32", {}, HOME / "AppData" / "Roaming"),
    ("cygwin", {"APPDATA": "/c/appdata"}, Path("/c/appdata")),
    ("linux", {}, HOME / ".config"),
    ("linux", {"XDG_CONFIG_HOME": "/xdg"}, Path("/xdg")),
    ("freebsd13", {}, HOME / ".config"),
])
def test_config_root_per_os(platform, env, expected):
    assert config_root(platform, env, HOME) == expected


def test_every_flavour_is_a_candidate(monkeypatch):
    monkeypatch.delenv(editors.ENV_VSCODE, raising=False)
    monkeypatch.delenv(editors.ENV_CURSOR, raising=False)
    root = config_root()
    assert [d for d, _ in editors.vscode_user_dirs()] == [
        root / "Code" / "User", root / "Code - Insiders" / "User", root / "VSCodium" / "User"]
    assert [d for d, _ in editors.cursor_user_dirs()] == [root / "Cursor" / "User"]
    prefixes = [p for _, p in editors.vscode_user_dirs()]
    assert len(set(prefixes)) == len(prefixes), "flavours must not collide in the backup"


def test_cursor_transcripts_live_in_the_home_dir_everywhere(monkeypatch):
    monkeypatch.delenv(editors.ENV_CURSOR_PROJECTS, raising=False)
    assert editors.cursor_projects_dir() == Path.home() / ".cursor" / "projects"


def test_override_accepts_a_path_list(monkeypatch, tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    monkeypatch.setenv(editors.ENV_VSCODE, os.pathsep.join([str(a), str(b)]))
    assert editors.vscode_user_dirs() == [(a, ""), (b, "alt1-")]


@pytest.mark.parametrize("uri, expected", [
    ("file:///Users/x/my%20proj", "/Users/x/my proj"),
    ("file:///c%3A/Users/x/proj", "c:\\Users\\x\\proj"),
    ("file:///C:/Users/x/proj", "C:\\Users\\x\\proj"),
    ("file:///d%3A", "d:\\"),
    ("file://server/share/proj", "\\\\server\\share\\proj"),
    ("vscode-remote://ssh-remote%2Bbox/home/x/p", "ssh-remote+box:/home/x/p"),
    ("vscode-remote://wsl%2BUbuntu/home/x", "wsl+Ubuntu:/home/x"),
    ({"$mid": 1, "path": "/a/b", "scheme": "file"}, "/a/b"),
    ({"external": "file:///c%3A/w", "path": "/c:/w"}, "c:\\w"),
    ("/already/a/path", "/already/a/path"),
    ("", ""), (None, ""), (42, ""),
])
def test_uri_to_path(uri, expected):
    assert uri_to_path(uri) == expected


@pytest.mark.parametrize("raw", ["tab/1", "a:b", "..", "c:\\x", "con", "NUL.txt", "x" * 400, "", "日本"])
def test_safe_name_is_one_safe_component(raw):
    name = safe_name(raw)
    assert is_safe_id(name)
    assert len(name) <= 151 and not set(name) & set('<>:"/\\|?*')
    assert name.split(".")[0].upper() not in {"CON", "PRN", "AUX", "NUL"}


def test_cursor_slug_matches_for_the_same_folder():
    assert cursor_slug("/Users/x/my.app") == "Users-x-my-app"
    assert cursor_slug("c:\\Users\\x\\my app") == "c-Users-x-my-app"
    assert cursor_slug("") == "_no-workspace"


def test_no_text_file_is_opened_with_the_locale_encoding(tmp_path):
    """On Windows the locale encoding is cp1252: a file opened without an
    explicit encoding mangles every non-Latin character (or raises on write).
    Run the whole read/write pipeline with EncodingWarning turned into an error.
    """
    if sys.version_info < (3, 10):
        pytest.skip("EncodingWarning needs Python 3.10")
    tests_dir = Path(__file__).parent
    script = textwrap.dedent(f"""
        import sys, json
        sys.path.insert(0, {str(tests_dir)!r})
        from pathlib import Path
        from editor_fixtures import make_vscode, write_cursor_store, write_transcripts, \\
            cursor_conversation, transcript_lines
        root = Path({str(tmp_path)!r})
        make_vscode(root / "vs")
        write_cursor_store(root / "cu", {{"c-main": cursor_conversation()}})
        write_transcripts(root / "cp", "Users-x-my-app", {{"t": transcript_lines("日本")}})
        from clicodelog import config, metastore, sync, storage, fts
        for m in (config, metastore, sync, fts):
            m.DATA_DIR = root / "data"
        metastore.DB_FILE = root / "meta.db"
        fts.DB_FILE = root / "fts.db"
        for sid in ("vscode", "cursor"):
            assert sync.sync_data(sid, silent=True), sid
            assert fts.build_index(sid)["state"] == "ready"
        assert metastore.count("vscode") == 4 and metastore.count("cursor") == 2
        storage.write_json(root / "b.json", [{{"note": "日本語"}}])
        assert storage.load_json(root / "b.json", None) == [{{"note": "日本語"}}]
        print("PIPELINE-OK")
    """)
    env = dict(os.environ,
               CLICODELOG_VSCODE_USER_DIRS=str(tmp_path / "vs"),
               CLICODELOG_CURSOR_USER_DIRS=str(tmp_path / "cu"),
               CLICODELOG_CURSOR_PROJECTS_DIR=str(tmp_path / "cp"),
               HOME=str(tmp_path / "home"), USERPROFILE=str(tmp_path / "home"),
               PYTHONWARNDEFAULTENCODING="1")
    r = subprocess.run([sys.executable, "-W", "error::EncodingWarning", "-c", script],
                       env=env, capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert r.returncode == 0, r.stderr[-3000:]
    assert "PIPELINE-OK" in r.stdout
