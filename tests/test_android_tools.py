"""Integracion acotada con herramientas Android instaladas/locales."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

from rea_termux import android_tools, preflight


def fake_tool(path: Path, name: str, body: str):
    path.write_text("#!/usr/bin/env python3\n" + body, encoding="utf-8")
    path.chmod(0o755)
    return str(path)


def test_android_tool_process_uses_read_only_checks_and_apktool_manifest(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    apk = tmp_path / "demo.apk"
    apk.write_bytes(b"FAKE APK bytes; tool fakes do not execute this")
    before = hashlib.sha256(apk.read_bytes()).hexdigest()
    paths = {}
    paths["aapt"] = fake_tool(bindir / "aapt", "aapt", '''
import sys
if sys.argv[1:] == ["version"]: print("Android aapt 1.0")
elif sys.argv[1:3] == ["dump", "badging"]: print("package: name='com.example.a' versionCode='2' versionName='2.0'")
''')
    paths["apktool"] = fake_tool(bindir / "apktool", "apktool", '''
import pathlib,sys
if sys.argv[1:] == ["--version"]: print("2.9.3")
elif sys.argv[1] == "d":
  out=pathlib.Path(sys.argv[sys.argv.index("--output")+1]); out.mkdir(parents=True,exist_ok=True)
  (out/"AndroidManifest.xml").write_text('<manifest package="com.example.a"/>')
  print("decoded")
''')
    paths["apksigner"] = fake_tool(bindir / "apksigner", "apksigner", '''
import sys
if sys.argv[1:] == ["version"]: print("0.9")
else: print("Verified using v2 scheme; signer cert SHA-256 digest: 00aa")
''')
    paths["zipalign"] = fake_tool(bindir / "zipalign", "zipalign", '''
import sys
if "-h" in sys.argv: print("zipalign help")
else: print("APK not aligned",file=sys.stderr); sys.exit(1)
''')
    monkeypatch.setattr(android_tools.shutil, "which", lambda name: paths.get(name))

    report = android_tools.process_apk(apk, tmp_path / "analysis")
    result_paths = android_tools.write_report(tmp_path / "analysis", report)
    assert report["read_only"] is True
    assert report["aapt_metadata"]["package"] == "com.example.a"
    assert Path(report["artifacts"]["apktool_manifest"]).read_text().startswith("<manifest")
    assert any(s["tool"] == "apksigner" and s["ok"] for s in report["steps"])
    assert any(s["tool"] == "zipalign" and not s["ok"] for s in report["steps"])
    assert all(p.is_file() for p in result_paths.values())
    assert hashlib.sha256(apk.read_bytes()).hexdigest() == before


def test_android_tools_missing_are_reported_without_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(android_tools.shutil, "which", lambda _name: None)
    apk = tmp_path / "x.apk"
    apk.write_bytes(b"not really a zip")
    report = android_tools.process_apk(apk, tmp_path / "analysis")
    assert report["steps"] == []
    assert len(report["limitations"]) >= 3


def test_apktool_output_symlink_is_rejected(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    tool = fake_tool(bindir / "apktool", "apktool", 'print("2.9.3")\n')
    monkeypatch.setattr(android_tools.shutil, "which", lambda name: tool if name == "apktool" else None)
    analysis = tmp_path / "analysis"
    (analysis / "apktool").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    apk = tmp_path / "x.apk"
    apk.write_bytes(b"fake")
    digest = hashlib.sha256(apk.read_bytes()).hexdigest()[:12]
    (analysis / "apktool" / digest).symlink_to(outside, target_is_directory=True)
    report = android_tools.process_apk(apk, analysis)
    step = next(s for s in report["steps"] if s["tool"] == "apktool")
    assert not step["ok"] and "symlink" in step["error"]
    assert list(outside.iterdir()) == []


def test_sdk_platform_is_detected_for_compilation(tmp_path, monkeypatch):
    sdk = tmp_path / "sdk"
    platform = sdk / "platforms" / "android-35"
    platform.mkdir(parents=True)
    (platform / "android.jar").write_bytes(b"android framework")
    monkeypatch.setenv("ANDROID_HOME", str(sdk))
    monkeypatch.delenv("ANDROID_SDK_ROOT", raising=False)
    report = android_tools._sdk_inventory()
    assert report["available"] and report["platforms"] == ["android-35"]
    check = preflight.android_sdk_check()
    assert check.ok and "android-35" in check.detail


def test_preflight_lists_android_sdk_tooling(monkeypatch):
    installed = {"apktool", "aapt", "apksigner", "zipalign"}
    monkeypatch.setattr(preflight.shutil, "which", lambda name: f"/bin/{name}" if name in installed else None)
    checks = {c.name: c for c in preflight.android_tool_checks()}
    assert checks["APKTool"].ok
    assert checks["Android aapt"].ok
    assert not checks["Android aapt2"].ok
    assert checks["Android apksigner"].ok
    assert checks["Android zipalign"].ok
    assert all(c.optional for c in checks.values())
