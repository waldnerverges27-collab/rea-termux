"""Verificacion y auto-reparacion: parsers, chequeo estatico y bucle."""
from __future__ import annotations

import json
import textwrap

import pytest

from rea_termux import verify as v


# ------------------------------------------------------------------- parsers
def test_parse_dart():
    out = """\
Analyzing app...
   error • Undefined name 'foo' • lib/main.dart:12:5 • undefined_identifier
   warning • Unused import • lib/x.dart:3:1 • unused_import
    info • Prefer const • lib/y.dart:9:9 • prefer_const_constructors
"""
    found = v.parse_dart(out)
    assert len(found) == 3
    e = found[0]
    assert (e.file, e.line, e.col, e.code) == ("lib/main.dart", 12, 5, "undefined_identifier")
    assert e.severity == "error"
    assert found[1].severity == "warning"


def test_parse_tsc():
    out = "src/api.ts(4,11): error TS2304: Cannot find name 'foo'.\n"
    found = v.parse_tsc(out)
    assert found and found[0].code == "TS2304"
    assert found[0].file == "src/api.ts" and found[0].line == 4


def test_parse_kotlin_and_gradle():
    out = "e: file:///p/App.kt:10:7 Unresolved reference: Text\nw: file:///p/b.kt:2:1 unused\n" \
          "> Task :app:compileDebugKotlin FAILED"
    found = v.parse_kotlin(out)
    sev = {(f.line, f.severity) for f in found}
    assert (10, "error") in sev and (2, "warning") in sev
    assert any("FAILED" in f.message for f in found)


def test_parse_generic_fallback():
    found = v.parse_generic("lib/x.dart:7:3: algo raro\n")
    assert found[0].file == "lib/x.dart" and found[0].line == 7
    # parse_output cae al generico si el parser del tool no saca nada
    assert v.parse_output("dart", "lib/x.dart:7:3: algo raro\n")


# ------------------------------------------------------------ estatico real
def _proj(tmp_path, files: dict[str, str]):
    for rel, content in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(content), encoding="utf-8")
    return tmp_path


def test_static_ok(tmp_path):
    root = _proj(tmp_path, {
        "app/flutter/pubspec.yaml": "name: x\n",
        "app/flutter/lib/main.dart": """
            void main() {
              final s = 'hola } no cuenta';
              // tampoco } esto
              print(s);
            }
        """,
    })
    res = v.verify(root / "app/flutter", use_toolchain=False)
    assert res.ok, res.summary()
    assert res.tool == "static"
    assert any("estatico" in n for n in res.notes)


def test_static_detects_unbalanced_braces(tmp_path):
    root = _proj(tmp_path, {
        "app/flutter/pubspec.yaml": "name: x\n",
        "app/flutter/lib/main.dart": "void main() {\n  print('x');\n",
    })
    res = v.verify(root / "app/flutter", use_toolchain=False)
    assert not res.ok
    assert any(f.code == "braces" for f in res.errors)


def test_static_detects_placeholders_and_empty(tmp_path):
    root = _proj(tmp_path, {
        "pubspec.yaml": "name: x\n",
        "lib/main.dart": "void main(){} // __APP_NAME__\n",
        "lib/vacio.dart": "   \n",
    })
    res = v.verify(root, use_toolchain=False)
    codes = {f.code for f in res.findings}
    assert "placeholder" in codes and "empty" in codes
    assert not res.ok


def test_static_package_mismatch_is_warning(tmp_path):
    root = _proj(tmp_path, {
        "app/build.gradle.kts": "plugins {}\n",
        "src/main/java/com/other/App.kt":
            "package com.mine.app\n\nfun main() {}\n",
    })
    res = v.verify(root, use_toolchain=False)
    assert res.ok  # solo aviso
    assert any(f.code == "package-path" for f in res.warnings)


def test_detect_stack(tmp_path):
    assert v.detect_stack(_proj(tmp_path / "a", {"pubspec.yaml": "name: x"})) == "flutter"
    assert v.detect_stack(_proj(tmp_path / "b", {"app/build.gradle.kts": "x"})) == "android-kotlin"
    assert v.detect_stack(_proj(tmp_path / "c", {"package.json": "{}"})) == "web-pwa"
    assert v.detect_stack(tmp_path / "nada") == "generic"


def test_verify_uses_toolchain_when_present(tmp_path, monkeypatch):
    root = _proj(tmp_path, {"pubspec.yaml": "name: x", "lib/main.dart": "void main() {}\n"})
    calls = {}

    monkeypatch.setattr(v.shutil, "which",
                        lambda name: "/usr/bin/flutter" if name == "flutter" else None)

    def fake_run(cmd, cwd, timeout):
        calls["cmd"] = cmd
        return 1, "   error • Undefined name 'x' • lib/main.dart:1:1 • undefined_identifier\n", 0.3

    monkeypatch.setattr(v, "_run", fake_run)
    res = v.verify(root)
    assert calls["cmd"][:2] == ["flutter", "analyze"]
    assert res.tool == "flutter" and not res.ok
    assert res.errors[0].code == "undefined_identifier"


def test_verify_falls_back_when_toolchain_missing(tmp_path, monkeypatch):
    root = _proj(tmp_path, {"pubspec.yaml": "name: x", "lib/main.dart": "void main() {}\n"})
    monkeypatch.setattr(v.shutil, "which", lambda name: None)
    res = v.verify(root)
    assert res.tool == "static" and res.ok
    assert any("no disponible" in n for n in res.notes)


# ----------------------------------------------------------------- bucle
class FakeSession:
    def __init__(self, root, fixes):
        from rea_termux.memory import ProjectMemory

        self.ws = root
        self.memory = ProjectMemory.load(root)
        self.echo = lambda _m: None
        self.prompts = []
        self.fixes = fixes          # lista de callables(root) por turno
        self.devtools = type("D", (), {"pending": []})()

    def turn(self, text, **_kw):
        self.prompts.append(text)
        if self.fixes:
            self.fixes.pop(0)(self.ws)


def test_self_heal_fixes_and_stops(tmp_path):
    root = _proj(tmp_path, {"pubspec.yaml": "name: x",
                            "lib/main.dart": "void main() {\n"})

    def fix(r):
        (r / "lib/main.dart").write_text("void main() {}\n", encoding="utf-8")

    s = FakeSession(root, [fix])
    res = v.SelfHeal(s, max_rounds=3).run()
    assert res["ok"] and res["rounds"] == 1
    assert "La verificacion del proyecto FALLO" in s.prompts[0]
    assert "bloque(s) sin cerrar" in s.prompts[0]
    assert any("Verificacion superada" in f["text"] for f in s.memory.facts())


def test_self_heal_gives_up_and_records_todos(tmp_path):
    root = _proj(tmp_path, {"pubspec.yaml": "name: x",
                            "lib/main.dart": "void main() {\n"})
    s = FakeSession(root, [])
    res = v.SelfHeal(s, max_rounds=2).run()
    assert res["ok"] is False and res["rounds"] == 2
    assert len(s.prompts) == 2
    assert any("[risk]" in f["text"] or f["kind"] == "risk" for f in s.memory.facts())
    assert len(s.memory.data["todos"]) >= 1


def test_save_report(tmp_path):
    res = v.verify(_proj(tmp_path, {"package.json": "{}", "a.ts": "const x = 1;\n"}),
                   use_toolchain=False)
    p = v.save_report(tmp_path, res)
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["latest"]["tool"] == res.tool
