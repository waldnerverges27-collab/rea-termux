"""Captura de UI (parseo + wireframe), paleta PNG y generacion de diseno."""
from __future__ import annotations

import struct
import zlib

import pytest

from rea_termux import pngpalette, uicapture
from rea_termux.design import (build_design_md, compose_screen, flutter_screen,
                               improvements, screen_plan, semantic_type, write_ui)
from rea_termux.interview import Answers

SAMPLE_XML = """<?xml version='1.0' encoding='UTF-8'?>
<hierarchy rotation="0">
  <node index="0" class="android.widget.FrameLayout" bounds="[0,0][1080,1920]">
    <node index="0" class="android.widget.TextView" text="Bienvenido" bounds="[48,120][600,200]" resource-id="com.demo:id/title"/>
    <node index="1" class="android.widget.EditText" text="" resource-id="com.demo:id/email" bounds="[48,240][1032,340]" clickable="true"/>
    <node index="2" class="android.widget.Button" text="Entrar" bounds="[48,380][1032,430]" clickable="true"/>
    <node index="3" class="android.widget.Button" text="Ayuda" bounds="[48,450][1032,500]" clickable="true"/>
    <node index="4" class="android.widget.ImageView" text="" bounds="[48,520][1032,760]"/>
    <node index="5" class="androidx.recyclerview.widget.RecyclerView" bounds="[0,800][1080,1700]" scrollable="true"/>
  </node>
</hierarchy>
"""


def test_parse_hierarchy_and_types():
    root = uicapture.parse_hierarchy(SAMPLE_XML)
    assert root is not None
    nodes = uicapture.interactive_nodes(root)
    texts = [n.text for n in nodes if n.text]
    assert "Bienvenido" in texts and "Entrar" in texts
    edit = next(n for n in nodes if n.editable)
    assert edit.w == 984 and edit.h == 100
    assert semantic_type("android.widget.Button") == "button"
    assert semantic_type("android.widget.EditText") == "input"
    assert semantic_type("androidx.recyclerview.widget.RecyclerView") == "list"
    assert semantic_type("com.foo.CustomThing") == "container"


def test_wireframe_svg():
    root = uicapture.parse_hierarchy(SAMPLE_XML)
    svg = uicapture.wireframe_svg(root, 1080, 1920)
    assert svg.startswith("<svg")
    assert "Entrar" in svg
    assert svg.count("<rect") >= 3


def test_screen_plan_and_improvements():
    root = uicapture.parse_hierarchy(SAMPLE_XML)
    spec = {"name": "login", "screen": {"width": 1080, "height": 1920},
            "nodes": [n.to_dict() for n in uicapture.spec_nodes(root)],
            "theme": {"primary": "#112233"}}
    plan = screen_plan(spec)
    types = {c["type"] for c in plan["components"]}
    assert "button" in types and "input" in types
    imps = improvements(plan)
    assert any("48dp" in i for i in imps)          # boton de 50px = ~17dp
    assert any("Imagen sin descripcion" in i for i in imps)
    assert any("botones compiten" in i for i in imps)


def _png(path, width, height, pixel_fn):
    raw = b""
    for y in range(height):
        raw += b"\x00" + b"".join(bytes(pixel_fn(x, y)) for x in range(width))
    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
                     + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
    return path


def test_png_palette_and_theme(tmp_path):
    # fondo blanco con una franja azul intensa
    p = _png(tmp_path / "s.png", 40, 40,
             lambda x, y: (61, 90, 254) if 10 <= y < 20 else (255, 255, 255))
    w, h, px = pngpalette.decode(p)
    assert (w, h) == (40, 40) and len(px) == 1600
    cols = pngpalette.palette(p, top=4)
    hexes = [c["hex"] for c in cols]
    assert "#ffffff" in hexes and "#3d5afe" in hexes
    theme = pngpalette.suggest_theme(cols)
    assert theme["surface"] == "#ffffff"
    assert theme["primary"] == "#3d5afe"
    assert theme["on_surface"] == "#111111"
    assert pngpalette.contrast((0, 0, 0), (255, 255, 255)) > 20


def test_design_md_and_screen_code():
    root = uicapture.parse_hierarchy(SAMPLE_XML)
    plan = screen_plan({"name": "login", "nodes": [n.to_dict() for n in
                                                   uicapture.spec_nodes(root)]})
    md = build_design_md([plan], {"colors": [{"hex": "#ffffff", "share": .6},
                                             {"hex": "#112233", "share": .2}],
                                  "theme": {"primary": "#112233", "surface": "#ffffff"}},
                         Answers(app_name="N", package_id="com.mine.n"))
    assert "# Sistema de diseno" in md and "login" in md

    fl = flutter_screen(plan)
    assert "class LoginScreen" in fl and "TextField" in fl and "FilledButton" in fl
    kt = compose_screen(plan, "com.mine.n")
    assert "package com.mine.n.ui.screens" in kt and "OutlinedTextField" in kt
    assert "fun LoginScreen()" in kt


def test_write_ui_per_stack(tmp_path):
    plan = {"name": "home", "title": "Inicio",
            "components": [{"type": "text", "label": "Hola", "bounds": [0, 0, 10, 10],
                            "clickable": False, "editable": False}]}
    for stack, expect in [("flutter", "lib/screens/home.dart"),
                          ("android-kotlin", "app/src/main/java/com/mine/app/ui/screens/Home.kt"),
                          ("web-pwa", "src/screens/Home.tsx")]:
        a = Answers(app_name="A", package_id="com.mine.app", stack=stack)
        out = tmp_path / stack
        files = write_ui([plan], a, out)
        assert any(str(f).endswith(expect) for f in files), (stack, files)


def test_capture_uses_rea_png_and_keeps_adb_hierarchy(tmp_path, monkeypatch):
    shot = _png(tmp_path / "rea.png", 2, 2, lambda x, y: (20, 80, 140))
    calls = []

    def fake_adb(*args, timeout=60, binary=False):
        calls.append(args)
        if "wm" in args:
            return "Physical size: 1080x1920"
        if "uiautomator" in args:
            return "UI hierarchy dumped"
        if "cat" in args:
            return SAMPLE_XML
        raise AssertionError(f"no deberia capturar screenshot local: {args}")

    monkeypatch.setattr(uicapture, "adb_path", lambda: "/fake/adb")
    monkeypatch.setattr(uicapture, "devices", lambda: ["device-1"])
    monkeypatch.setattr(uicapture, "adb", fake_adb)
    res = uicapture.capture(tmp_path, "home", serial="device-1",
                           screenshot_bytes=shot.read_bytes(), screenshot_source="REA: test")
    assert res["ok"] and res["screenshot_source"] == "REA: test"
    assert (tmp_path / "ui" / "home.png").read_bytes() == shot.read_bytes()
    assert not any("screencap" in call for call in calls)


def test_list_screens_ignores_tokens(tmp_path):
    ui = tmp_path / "ui"
    ui.mkdir()
    (ui / "login.json").write_text('{"name": "login", "nodes": []}', encoding="utf-8")
    (ui / "tokens.json").write_text('{"colors": [], "theme": {}}', encoding="utf-8")
    (ui / "roto.json").write_text("{no json", encoding="utf-8")
    assert uicapture.list_screens(tmp_path) == ["login"]
