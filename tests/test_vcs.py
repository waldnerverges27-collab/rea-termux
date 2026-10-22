"""Control de versiones del workspace."""
from __future__ import annotations

import shutil
import subprocess

import pytest

from rea_termux import vcs

pytestmark = pytest.mark.skipif(not shutil.which("git"), reason="git no instalado")


@pytest.fixture()
def ws(tmp_path):
    w = tmp_path / "ws"
    w.mkdir()
    (w / "a.txt").write_text("hola\n", encoding="utf-8")
    return w


def test_init_creates_repo_and_ignores_binaries(ws):
    res = vcs.init(ws)
    assert res["ok"] and res["ours"]
    assert vcs.is_repo(ws) and vcs.is_ours(ws)
    assert "*.apk" in (ws / ".gitignore").read_text(encoding="utf-8")
    assert vcs.log(ws)  # commit inicial
    # idempotente
    assert vcs.init(ws)["reason"] == "ya inicializado"


def test_refuses_to_touch_foreign_repo(ws):
    subprocess.run(["git", "init", "-q", str(ws)], check=True)
    subprocess.run(["git", "-C", str(ws), "-c", "user.name=x", "-c", "user.email=x@y",
                    "commit", "-q", "--allow-empty", "-m", "del usuario"], check=True)
    res = vcs.init(ws)
    assert res["ok"] is False and "repositorio git tuyo" in res["reason"]
    # con --adopt si se adopta
    assert vcs.init(ws, allow_existing=True)["ok"] is True


def test_snapshot_commits_only_when_needed(ws):
    vcs.init(ws)
    assert vcs.snapshot(ws, "sin cambios") is None
    (ws / "b.txt").write_text("nuevo\n", encoding="utf-8")
    c = vcs.snapshot(ws, "anade b")
    assert c and c.short() and c.subject == "anade b"
    assert "b.txt" in vcs.files_changed_since(ws, vcs.log(ws)[1].hash)


def test_big_files_are_not_committed(ws):
    vcs.init(ws)
    big = ws / "huge.apk"
    big.write_bytes(b"0" * (6 * 1024 * 1024))
    (ws / "c.txt").write_text("x", encoding="utf-8")
    c = vcs.snapshot(ws, "con binario grande")
    assert c is not None
    files = vcs.files_changed_since(ws, vcs.log(ws)[1].hash)
    assert "c.txt" in files and "huge.apk" not in files


def test_undo_restores_previous_state(ws):
    vcs.init(ws)
    (ws / "a.txt").write_text("version 2\n", encoding="utf-8")
    vcs.snapshot(ws, "turno 1")
    (ws / "a.txt").write_text("version 3\n", encoding="utf-8")
    vcs.snapshot(ws, "turno 2")

    res = vcs.undo(ws, steps=1)
    assert res["ok"]
    assert (ws / "a.txt").read_text(encoding="utf-8") == "version 2\n"


def test_undo_refuses_foreign_repo(ws):
    subprocess.run(["git", "init", "-q", str(ws)], check=True)
    assert vcs.undo(ws)["ok"] is False


def test_diff_and_restore(ws):
    vcs.init(ws)
    first = vcs.head(ws)
    (ws / "a.txt").write_text("cambiada sin commit\n", encoding="utf-8")
    assert "cambiada" in vcs.diff(ws, worktree=True)
    res = vcs.restore(ws, first, ["a.txt"])
    assert res["ok"]
    assert (ws / "a.txt").read_text(encoding="utf-8") == "hola\n"


def test_diff_text_helper(ws):
    d = vcs.diff_text(ws, "x.kt", "uno\ndos\n", "uno\ntres\n")
    assert "-dos" in d and "+tres" in d and "a/x.kt" in d
