"""Empaquetado opcional del CLI como ejecutable Termux."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_binary_build_script_help_and_shell_syntax():
    subprocess.run(["bash", "-n", str(ROOT / "scripts" / "build-termux-binary.sh")],
                   check=True)
    result = subprocess.run(["bash", str(ROOT / "scripts" / "build-termux-binary.sh"), "--help"],
                            cwd=ROOT, capture_output=True, text=True, check=True)
    assert "dist/rea-termux" in result.stdout
    assert "Termux" in result.stdout


def test_binary_builder_refuses_non_termux_environment():
    env = os.environ.copy()
    env["PREFIX"] = "/not/termux"
    result = subprocess.run(["bash", str(ROOT / "scripts" / "build-termux-binary.sh")],
                            cwd=ROOT, env=env, capture_output=True, text=True)
    assert result.returncode == 2
    assert "dentro de Termux nativo" in result.stderr


def test_curl_installer_shell_syntax_and_termux_guard():
    script = ROOT / "scripts" / "install-termux.sh"
    subprocess.run(["bash", "-n", str(script)], check=True)
    env = os.environ.copy()
    env["PREFIX"] = "/not/termux"
    result = subprocess.run(["bash", str(script)], cwd=ROOT, env=env,
                            capture_output=True, text=True)
    assert result.returncode == 1
    assert "requiere Termux nativo" in result.stderr
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "raw.githubusercontent.com/waldnerverges27-collab/rea-termux/main/scripts/install-termux.sh | bash" in readme


def test_optional_pyinstaller_extra_and_frozen_entrypoint_exist():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    entry = (ROOT / "scripts" / "termux_frozen_entry.py").read_text(encoding="utf-8")
    assert 'freeze = ["pyinstaller>=6.20,<7"]' in pyproject
    assert "from rea_termux.cli import main" in entry
    bootstrap = (ROOT / "scripts" / "termux-bootstrap.sh").read_text(encoding="utf-8")
    assert "--binary) do_binary" in bootstrap
