"""Sesion conversacional: memoria inyectada, persistencia y no-olvido."""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from rea_termux.config import Config, ProviderCfg
from rea_termux.memory import ProjectMemory
from rea_termux.providers import Router
from rea_termux.session import DEV_SYSTEM, Session, build_session


def make_router(script, calls):
    state = {"i": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        i = min(state["i"], len(script) - 1)
        state["i"] += 1
        return httpx.Response(200, json=script[i])

    p = ProviderCfg(name="fake", base_url="https://fake.test/v1", model="m",
                    api_key_env="FAKE_KEY")
    return Router([p], client=httpx.Client(transport=httpx.MockTransport(handler)))


def call(name, args, cid="c1"):
    return {"choices": [{"message": {"role": "assistant", "content": "",
                                     "tool_calls": [{"id": cid, "type": "function",
                                                     "function": {"name": name,
                                                                  "arguments": json.dumps(args)}}]}}],
            "model": "m"}


def text(t):
    return {"choices": [{"message": {"role": "assistant", "content": t}}], "model": "m"}


@pytest.fixture()
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_KEY", "sk-x")
    c = Config()
    c.agent.max_turns = 6
    c.workspace = str(tmp_path / "ws")
    c.mcp.max_tools = 8
    return c


@pytest.fixture()
def ws(tmp_path):
    w = tmp_path / "ws"
    w.mkdir()
    ProjectMemory.load(w).set_project(name="Notas", package_id="com.mine.notas",
                                      stack="flutter", source_package="com.src.notas")
    return w


def make_session(cfg, ws, router):
    return Session(cfg=cfg, router=router, ws=ws, memory=ProjectMemory.load(ws)).bind()


def test_memory_is_injected_in_every_turn(cfg, ws):
    calls = []
    router = make_router([text("hecho 1"), text("hecho 2")], calls)
    ProjectMemory.load(ws).remember("La app usa modo oscuro por defecto", kind="decision")
    s = make_session(cfg, ws, router)

    s.turn("empecemos")
    s.turn("sigue")

    for req in calls:
        system = req["messages"][0]
        assert system["role"] == "system"
        assert "ESTADO DEL PROYECTO" in system["content"]
        assert "modo oscuro por defecto" in system["content"]
        assert "com.mine.notas" in system["content"]
    assert len(calls) == 2


def test_tools_include_files_and_memory(cfg, ws):
    calls = []
    s = make_session(cfg, ws, make_router([text("ok")], calls))
    names = {t["function"]["name"] for t in s.tools()}
    assert {"write_file", "edit_file", "run_command", "remember", "add_todo",
            "project_status", "capture_screen"} <= names


def test_write_file_and_remember_persist(cfg, ws):
    calls = []
    router = make_router([
        call("write_file", {"path": "app/lib/main.dart", "content": "void main(){}",
                            "summary": "arranque"}),
        call("remember", {"text": "El punto de entrada es main.dart", "kind": "decision"}, "c2"),
        call("add_todo", {"text": "crear login", "screen": "login"}, "c3"),
        text("Listo: cree el arranque y lo anote."),
    ], calls)
    s = make_session(cfg, ws, router)
    out = s.turn("crea el arranque")

    assert "Listo" in out
    assert (ws / "app/lib/main.dart").read_text(encoding="utf-8") == "void main(){}"
    mem = ProjectMemory.load(ws)
    assert any("El punto de entrada" in f["text"] for f in mem.facts())
    assert any(t["text"] == "crear login" for t in mem.open_todos())
    assert "app/lib/main.dart" in mem.inventory()
    # el estado escrito en disco refleja el trabajo
    assert "El punto de entrada" in (ws / "PROJECT_STATUS.md").read_text(encoding="utf-8")


def test_edit_file_requires_exact_match(cfg, ws):
    (ws / "a.txt").write_text("hola mundo", encoding="utf-8")
    calls = []
    router = make_router([
        call("edit_file", {"path": "a.txt", "old_text": "no existe", "new_text": "x"}),
        text("no pude"),
    ], calls)
    make_session(cfg, ws, router).turn("edita")
    tool_msg = [m for m in calls[1]["messages"] if m["role"] == "tool"][-1]
    assert "no aparece literalmente" in tool_msg["content"]
    assert (ws / "a.txt").read_text(encoding="utf-8") == "hola mundo"


def test_jail_blocks_outside_workspace(cfg, ws, tmp_path):
    calls = []
    router = make_router([
        call("read_file", {"path": "/etc/passwd"}),
        text("bloqueado"),
    ], calls)
    make_session(cfg, ws, router).turn("lee /etc/passwd")
    tool_msg = [m for m in calls[1]["messages"] if m["role"] == "tool"][-1]
    assert "fuera del workspace" in tool_msg["content"]


def test_session_resumes_from_disk(cfg, ws):
    calls = []
    s1 = make_session(cfg, ws, make_router([text("primera respuesta")], calls))
    s1.bind()
    s1.turn("hola")
    sid = s1.session_id

    # nueva instancia, misma sesion: recupera historial y memoria
    s2 = build_session(cfg, make_router([text("segunda")], []), ws, session_id=sid,
                       with_mcp=False)
    assert s2.session_id == sid
    assert any(m.get("role") == "user" and m.get("content") == "hola" for m in s2.messages)
    assert "ESTADO DEL PROYECTO" in s2._system("continuar")


def test_latest_session_detected(cfg, ws):
    s = make_session(cfg, ws, make_router([text("x")], []))
    s.turn("una")
    assert Session.latest_id(ws) == s.session_id


def test_dev_system_prompt_has_ip_limits():
    assert "NO copies codigo" in DEV_SYSTEM
    assert "remember" in DEV_SYSTEM
    assert "ESTADO DEL PROYECTO" in DEV_SYSTEM


def test_tool_errors_do_not_kill_turn(cfg, ws):
    calls = []
    router = make_router([
        call("herramienta_inexistente", {}),
        text("sigo vivo"),
    ], calls)
    out = make_session(cfg, ws, router).turn("prueba")
    assert out == "sigo vivo"


# ---------------------------------------------------------------- git / dry-run
import shutil
import subprocess

GIT = shutil.which("git")


@pytest.mark.skipif(not GIT, reason="git no instalado")
def test_turn_creates_commit_and_undo_reverts(cfg, ws):
    from rea_termux import vcs

    calls = []
    router = make_router([
        call("write_file", {"path": "app/lib/x.dart", "content": "v1",
                            "summary": "primera version"}),
        text("escribi v1"),
        call("write_file", {"path": "app/lib/x.dart", "content": "v2",
                            "summary": "segunda version"}, "c2"),
        text("escribi v2"),
    ], calls)
    s = make_session(cfg, ws, router)
    s.turn("crea x")
    assert (ws / "app/lib/x.dart").read_text(encoding="utf-8") == "v1"
    commits = vcs.log(ws)
    assert commits and "escribi v1" in commits[0].subject
    body = subprocess.run(["git", "-C", str(ws), "log", "-1", "--format=%b"],
                          capture_output=True, text=True).stdout
    assert "pedido: crea x" in body

    s.turn("actualiza x")
    assert (ws / "app/lib/x.dart").read_text(encoding="utf-8") == "v2"
    assert len(vcs.log(ws)) >= 3  # inicial + 2 turnos

    res = s.undo(1)
    assert res["ok"]
    assert (ws / "app/lib/x.dart").read_text(encoding="utf-8") == "v1"


def test_dry_run_proposes_without_writing(cfg, ws):
    calls = []
    router = make_router([
        call("write_file", {"path": "app/lib/y.dart", "content": "no debe existir",
                            "summary": "propuesta"}),
        text("propuesta lista"),
    ], calls)
    s = make_session(cfg, ws, router)
    s.dry_run = True
    s.devtools.dry_run = True
    s.turn("crea y")

    assert not (ws / "app/lib/y.dart").exists()
    assert s.devtools.pending and s.devtools.pending[0]["path"] == "app/lib/y.dart"
    tool_msg = [m for m in calls[1]["messages"] if m["role"] == "tool"][-1]
    assert "PROPUESTA (dry-run" in tool_msg["content"]
    assert "app/lib/y.dart" in (ws / ".rea" / "pending.json").read_text(encoding="utf-8")

    # aplicar despues
    applied = s.devtools.apply_pending()
    assert applied == ["app/lib/y.dart"]
    assert (ws / "app/lib/y.dart").read_text(encoding="utf-8") == "no debe existir"


def test_dry_run_edit_shows_diff(cfg, ws):
    p = ws / "app/lib/z.dart"
    p.parent.mkdir(parents=True)
    p.write_text("linea uno\nlinea dos\n", encoding="utf-8")
    calls = []
    router = make_router([
        call("edit_file", {"path": "app/lib/z.dart", "old_text": "linea dos",
                           "new_text": "linea DOS mejorada"}),
        text("listo"),
    ], calls)
    s = make_session(cfg, ws, router)
    s.dry_run = True
    s.devtools.dry_run = True
    s.turn("mejora z")
    assert "linea dos" in p.read_text(encoding="utf-8")   # intacto
    diff = s.devtools.pending[0]["diff"]
    assert "-linea dos" in diff and "+linea DOS mejorada" in diff


def test_undo_command_in_repl(cfg, ws, capsys):
    from rea_termux import vcs

    s = make_session(cfg, ws, make_router([text("x")], []))
    (ws / "f.txt").write_text("a", encoding="utf-8")
    vcs.snapshot(ws, "turno manual")
    s.command("/undo 1")
    out = capsys.readouterr().out
    assert "deshecho" in out or "no se pudo" in out
    s.command("/help")
    assert "/undo" in capsys.readouterr().out


@pytest.mark.skipif(not GIT, reason="git no instalado")
def test_repl_diff_log_pending(cfg, ws, capsys):
    from rea_termux import vcs

    s = make_session(cfg, ws, make_router([text("x")], []))
    (ws / "n.txt").write_text("uno\n", encoding="utf-8")
    vcs.snapshot(ws, "primer cambio")
    (ws / "n.txt").write_text("dos\n", encoding="utf-8")

    s.command("/log")
    assert "primer cambio" in capsys.readouterr().out

    s.command("/diff")
    assert "dos" in capsys.readouterr().out

    # pending vacio
    s.command("/pending")
    assert "sin propuestas" in capsys.readouterr().out

    # se anade una propuesta a mano y /apply la escribe
    s.devtools.dry_run = True
    s.devtools.dispatch("write_file", {"path": "n2.txt", "content": "tres",
                                       "summary": "propuesta"})
    s.command("/pending")
    assert "n2.txt" in capsys.readouterr().out
    s.command("/apply")
    assert (ws / "n2.txt").read_text(encoding="utf-8") == "tres"


def test_vcs_disabled_when_git_missing(cfg, ws, monkeypatch):
    from rea_termux import vcs

    monkeypatch.setattr(vcs, "available", lambda: False)
    s = Session(cfg=cfg, router=make_router([text("x")], []), ws=ws,
                memory=ProjectMemory.load(ws))
    s.bind()
    assert s.use_vcs is False          # no rompe la sesion


# ------------------------------------------------------------ verificacion
def test_verify_build_tool_available_and_reports(cfg, tmp_path):
    ws = tmp_path / "p"
    (ws / "lib").mkdir(parents=True)
    (ws / "pubspec.yaml").write_text("name: x\n", encoding="utf-8")
    (ws / "lib/main.dart").write_text("void main() {\n", encoding="utf-8")
    s = make_session(cfg, ws, make_router([text("x")], []))
    names = {t["function"]["name"] for t in s.tools()}
    assert "verify_build" in names
    out = s.devtools.dispatch("verify_build", {})
    assert "error" in out and "braces" in out
    assert (ws / ".rea" / "verify.json").is_file()


def test_repl_verify_command(cfg, tmp_path, capsys):
    ws = tmp_path / "p"
    (ws / "lib").mkdir(parents=True)
    (ws / "pubspec.yaml").write_text("name: x\n", encoding="utf-8")
    (ws / "lib/main.dart").write_text("void main() {}\n", encoding="utf-8")
    s = make_session(cfg, ws, make_router([text("x")], []))
    s.command("/verify")
    out = capsys.readouterr().out
    assert "OK" in out
