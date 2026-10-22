"""Pruebas del panel gráfico local y sus barreras básicas."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from rea_termux.gui import PAGE, _inside, _load_json, _safe_screen, _safe_slug
from rea_termux.cli import build_parser


def test_safe_project_and_screen_names():
    assert _safe_slug("../Mi proyecto APK") == "Mi-proyecto-APK"
    assert _safe_slug("../../") == "rea-app"
    assert _safe_screen("Inicio / perfil") == "Inicio_perfil"
    assert _safe_screen("../../") == "pantalla" or _safe_screen("../../") == ""


def test_workspace_path_guard_rejects_escape(tmp_path):
    root = tmp_path / "workspaces"
    root.mkdir()
    assert _inside(root / "ok" / "file.txt", root) == root / "ok" / "file.txt"
    with pytest.raises(ValueError):
        _inside(root / ".." / "outside.txt", root)


def test_gui_json_payload_requires_object_and_is_bounded():
    assert _load_json(b'{"name":"home"}') == {"name": "home"}
    with pytest.raises(ValueError):
        _load_json(b'[1,2]')
    with pytest.raises(ValueError):
        _load_json(b"x" * (256 * 1024 + 1))


def test_gui_parser_and_mobile_wizard_copy():
    args = build_parser().parse_args(["gui", "--port", "0", "--no-open"])
    assert args.cmd == "gui" and args.port == 0 and args.no_open
    assert "Subir y analizar" in PAGE
    assert "Preparar plan para revisar" in PAGE
    assert "Aprobar y generar proyecto" in PAGE
    assert "Capturar pantalla actual" in PAGE
    assert "fonts.googleapis.com" not in PAGE and "cdn." not in PAGE


def test_gui_server_is_loopback_token_protected_and_serves_page(tmp_path):
    config = tmp_path / "config.toml"
    config.write_text(f'workspace = "{tmp_path / "ws"}"\n', encoding="utf-8")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    proc = subprocess.Popen(
        [sys.executable, "-u", "-m", "rea_termux", "--config", str(config),
         "gui", "--port", "0", "--no-open"],
        cwd=Path(__file__).resolve().parents[1], env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    try:
        assert proc.stdout is not None
        url_line = ""
        deadline = time.time() + 12
        while time.time() < deadline:
            line = proc.stdout.readline()
            if not line and proc.poll() is not None:
                break
            if "Abre en este teléfono:" in line:
                url_line = line.strip().split("Abre en este teléfono:", 1)[1].strip()
                break
        assert url_line, "el servidor local no publicó su URL"
        match = re.fullmatch(r"http://127\.0\.0\.1:\d+/\?token=([^&]+)", url_line)
        assert match
        token = match.group(1)
        page = urllib.request.urlopen(url_line, timeout=3).read().decode("utf-8")
        assert "De un APK a un proyecto propio" in page
        request = urllib.request.Request(
            url_line.split("/?", 1)[0] + "/api/state",
            headers={"X-Rea-Token": token},
        )
        state = json.loads(urllib.request.urlopen(request, timeout=3).read())
        assert state["workspace"] is None
        denied = urllib.request.Request(url_line.split("/?", 1)[0] + "/api/state")
        with pytest.raises(urllib.error.HTTPError) as ex:
            urllib.request.urlopen(denied, timeout=3)
        assert ex.value.code == 403
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
