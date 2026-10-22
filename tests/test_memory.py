"""Memoria de proyecto: persistencia, deduplicacion, digest y estado."""
from __future__ import annotations

import json

from rea_termux.memory import ProjectMemory


def test_remember_persists_and_dedups(tmp_path):
    m = ProjectMemory.load(tmp_path)
    m.set_project(name="MiApp", package_id="com.mine.app", stack="flutter",
                  source_package="com.src.app")
    f1 = m.remember("La home usa paginacion de 20", kind="decision")
    f2 = m.remember("la home usa PAGINACION de 20   ", kind="decision")
    assert f1["id"] == f2["id"]
    assert len(m.facts()) == 1

    m2 = ProjectMemory.load(tmp_path)
    assert m2.project["name"] == "MiApp"
    assert len(m2.facts()) == 1
    assert m2.facts()[0]["kind"] == "decision"


def test_todos_cycle(tmp_path):
    m = ProjectMemory.load(tmp_path)
    t = m.add_todo("implementar login", screen="login")
    assert len(m.open_todos()) == 1
    m.complete_todo(t["id"])
    assert m.open_todos() == []
    assert any(t["status"] == "done" for t in m.data["todos"])


def test_file_tracking_detects_external_change(tmp_path):
    m = ProjectMemory.load(tmp_path)
    p = tmp_path / "app/lib/main.dart"
    p.parent.mkdir(parents=True)
    p.write_text("void main(){}", encoding="utf-8")
    m.track_file("app/lib/main.dart", role="entrypoint", summary="arranque")
    assert m.file_changed("app/lib/main.dart") is False
    p.write_text("void main(){ runApp(); }", encoding="utf-8")
    assert m.file_changed("app/lib/main.dart") is True


def test_digest_contains_state(tmp_path):
    m = ProjectMemory.load(tmp_path)
    m.set_project(name="Notas", package_id="com.mine.notas", stack="flutter",
                  source_package="com.other.notas")
    m.remember("Se elimino el login con SMS por privacidad", kind="decision")
    m.add_todo("crear pantalla de ajustes", screen="ajustes")
    m.define("nota", "texto con titulo y cuerpo")
    m.set_ui({"colors": [{"hex": "#112233"}], "theme": {}}, [{"name": "home"}])
    d = m.digest()
    assert "ESTADO DEL PROYECTO" in d
    assert "Notas" in d
    assert "paginacion" not in d.lower()
    assert "login con SMS" in d
    assert "crear pantalla de ajustes" in d
    assert "nota = texto con titulo y cuerpo" in d
    assert "home" in d
    assert "sin copiar codigo" in d


def test_status_markdown_and_write(tmp_path):
    m = ProjectMemory.load(tmp_path)
    m.set_project(name="X", package_id="com.x", stack="web-pwa", source_package="com.y")
    m.remember("decision uno")
    m.add_todo("tarea uno")
    md = m.status_markdown()
    assert "decision uno" in md and "tarea uno" in md
    p = m.write_status()
    assert p.is_file() and "decision uno" in p.read_text(encoding="utf-8")


def test_events_jsonl(tmp_path):
    m = ProjectMemory.load(tmp_path)
    m.remember("algo")
    lines = (tmp_path / ".rea" / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert any("remember" in l for l in lines)
    assert all(json.loads(l) for l in lines)


def test_remember_with_key_upserts(tmp_path):
    m = ProjectMemory.load(tmp_path)
    m.remember("Proyecto generado: package com.example.viejo", kind="decision",
               key="project_generated")
    m.remember("Proyecto generado: package com.mine.nuevo", kind="decision",
               key="project_generated")
    facts = [f for f in m.facts() if f.get("key") == "project_generated"]
    assert len(facts) == 1
    assert "com.mine.nuevo" in facts[0]["text"]


def test_prune_missing_files(tmp_path):
    m = ProjectMemory.load(tmp_path)
    p1 = tmp_path / "a.kt"
    p1.write_text("x", encoding="utf-8")
    m.track_file("a.kt")
    m.track_file("fantasma.kt")
    gone = m.prune_missing()
    assert "fantasma.kt" in gone
    assert m.inventory() == ["a.kt"]
