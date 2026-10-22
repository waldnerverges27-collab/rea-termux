"""Pruebas de config, policy, entrevista, scaffold, apkmeta y reporte."""
from __future__ import annotations

import json
import struct
import zipfile
from pathlib import Path

import pytest

from rea_termux import config as cfgmod
from rea_termux.apkmeta import AXML, read_apk_meta
from rea_termux.interview import Answers, build_plan_markdown, interview
from rea_termux.policy import (PolicyError, check_declaration, safe_identifier,
                               trademark_guard, warnings_for)
from rea_termux.report import build_analysis
from rea_termux.scaffold import write_project


# ------------------------------------------------------------------ config
def test_write_and_load_config(tmp_path, monkeypatch):
    p = tmp_path / "config.toml"
    cfgmod.write_default(p, force=True)
    monkeypatch.setenv("NINEROUTER_API_KEY", "sk-9")
    cfg = cfgmod.load(p)
    assert len(cfg.providers) == 2
    assert [p_.name for p_ in cfg.provider_chain()] == ["9router", "omniroute"]
    assert cfg.provider_chain()[0].resolve_key() == "sk-9"


def test_provider_endpoint_and_missing_key(tmp_path, monkeypatch):
    p = tmp_path / "c.toml"
    p.write_text(
        '[[providers]]\nname="x"\nbase_url="https://api/x/v1/"\nmodel="m"\n'
        'api_key_env="NOPE_KEY"\n', encoding="utf-8")
    monkeypatch.delenv("NOPE_KEY", raising=False)
    cfg = cfgmod.load(p)
    pr = cfg.providers[0]
    assert pr.endpoint() == "https://api/x/v1/chat/completions"
    assert pr.resolve_key() is None


# ------------------------------------------------------------------ policy
def test_policy_requires_attestation():
    cfg = cfgmod.Config()
    with pytest.raises(PolicyError):
        check_declaration(cfg, "com.example.mine", need_clone=True)


def test_branded_apps_allowed_but_warned_and_renamed():
    cfg = cfgmod.Config()
    cfg.policy.attested = True
    # clonar funcionalidad de una marca: permitido, con aviso
    warns = check_declaration(cfg, "com.instagram.android", need_clone=True)
    assert warns and "marca vigilada" in warns[0]
    assert trademark_guard(cfg, "com.google.android.youtube") == "com.google"
    assert trademark_guard(cfg, "com.mine.app") is None
    # la identidad se sustituye por una propia
    pkg, _name = safe_identifier("com.instagram.android", "Instagram Clone", cfg)
    assert pkg.startswith("com.example.") and "instagram" not in pkg
    # sin marca: se respeta el identificador elegido por el usuario
    assert safe_identifier("com.mine.app", "MiApp", cfg,
                           current_id="com.mine.miapp")[0] == "com.mine.miapp"


def test_warnings_for_plain_app_is_empty():
    cfg = cfgmod.Config()
    assert warnings_for(cfg, "com.mine.app") == []


# --------------------------------------------------------------- entrevista
def test_interview_defaults_from_answers_dict():
    a = interview({"app_name": "Notas", "screens": ["home", "detail"]},
                  asker=None)
    assert isinstance(a, Answers)
    assert a.screens == ["home", "detail"]


def test_interview_scripted(monkeypatch):
    answers = iter(["MiNotas", "com.mine.notas", "notas propias",
                    "modo oscuro; sync", "2", "home, detalle", "1", "s", "es",
                    "publicidad", "nada"])
    a = interview({}, asker=lambda _q: next(answers, ""))
    assert a.app_name == "MiNotas"
    assert a.stack == "flutter"
    assert a.screens == ["home", "detalle"]
    assert a.keep_offline is True


# ------------------------------------------------------------------ scaffold
@pytest.mark.parametrize("stack", ["android-kotlin", "flutter", "react-native", "web-pwa"])
def test_scaffold_all_stacks(tmp_path, stack):
    a = Answers(app_name="Mi App", package_id="com.mine.app",
                screens=["home", "perfil"], stack=stack, backend="mock")
    files = write_project(a, tmp_path / stack, source_package="com.src.app")
    assert files
    names = [f.name for f in files]
    assert "NEXT_STEPS.md" in names
    blob = "\n".join(f.read_text(encoding="utf-8") for f in files if f.is_file())
    assert "com.mine.app" in blob or "com_mine_app" in blob
    assert "com.src.app" in blob


def test_scaffold_does_not_reuse_source_package_as_target(tmp_path):
    a = Answers(app_name="X", package_id="com.mine.x", stack="android-kotlin")
    write_project(a, tmp_path, source_package="com.other.app")
    manifest = next(tmp_path.rglob("AndroidManifest.xml")).read_text(encoding="utf-8")
    assert "com.mine.x" not in manifest  # namespace vive en build.gradle
    gradle = (tmp_path / "app/build.gradle.kts").read_text(encoding="utf-8")
    assert 'namespace = "com.mine.x"' in gradle


# ------------------------------------------------------------------- apkmeta
def _axml_with_package(pkg: str) -> bytes:
    """Construye un AXML minimo valido: string pool + <manifest package="pkg">."""
    strings = ["package", "manifest", pkg]
    data = b""
    offsets = []
    for st in strings:
        raw = st.encode("utf-16-le")
        offsets.append(len(data))
        data += struct.pack("<H", len(st)) + raw + b"\x00\x00"

    offs_blob = b"".join(struct.pack("<I", o) for o in offsets)
    strings_start = 28 + len(offs_blob)
    sp_body = struct.pack("<IIIII", len(strings), 0, 0, strings_start, 0)
    sp_body += offs_blob + data
    sp_chunk = struct.pack("<HHI", 0x0001, 28, 8 + len(sp_body)) + sp_body

    # <manifest> con un atributo: package (name_idx=0, raw_idx=2, tipo string)
    attr_size = 20
    elem = struct.pack("<II", 1, 0xFFFFFFFF)                    # lineNumber, comment
    elem += struct.pack("<II", 0xFFFFFFFF, 1)                   # ns, name=manifest
    elem += struct.pack("<HHHHHH", 20, attr_size, 1, 0, 0, 0)   # attrStart, size, count...
    elem += struct.pack("<III", 0xFFFFFFFF, 0, 2)               # ns, name=package, raw=2
    elem += struct.pack("<HBBI", 8, 0, 0x03, 2)                 # size, res0, TYPE_STRING, data
    elem_chunk = struct.pack("<HHI", 0x0102, 16, 8 + len(elem)) + elem

    body = sp_chunk + elem_chunk
    return struct.pack("<HHI", 0x0003, 8, 8 + len(body)) + body


def test_axml_package_parse():
    raw = _axml_with_package("com.example.axml")
    ax = AXML(raw)
    ax.parse()
    assert "com.example.axml" in ax.strings
    assert ax.manifest_package() == "com.example.axml"


def test_apk_meta_from_zip(tmp_path):
    apk = tmp_path / "demo.apk"
    with zipfile.ZipFile(apk, "w") as zf:
        zf.writestr("AndroidManifest.xml", _axml_with_package("com.demo.zip"))
        zf.writestr("classes.dex", b"dex")
    meta = read_apk_meta(apk)
    assert meta["package"] == "com.demo.zip"
    assert meta["has_native_libs"] is False


def test_apk_meta_invalid_file(tmp_path):
    bad = tmp_path / "x.apk"
    bad.write_bytes(b"no soy un zip")
    meta = read_apk_meta(bad)
    assert meta["package"] is None and meta["error"]


# ------------------------------------------------------------------- reporte
MANIFEST_XML = """<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    android:versionCode="7" android:versionName="1.2" package="com.demo.app">
    <uses-sdk android:minSdkVersion="24" android:targetSdkVersion="34"/>
    <uses-permission android:name="android.permission.INTERNET"/>
    <uses-permission android:name="android.permission.CAMERA"/>
    <uses-permission android:name="android.permission.READ_SMS"/>
    <application android:name="com.demo.app.App" android:theme="@style/T">
        <activity android:name="com.demo.app.MainActivity" android:exported="true">
            <intent-filter>
                <action android:name="android.intent.action.MAIN"/>
                <category android:name="android.intent.category.LAUNCHER"/>
            </intent-filter>
        </activity>
        <activity android:name="com.demo.app.DetailActivity" android:exported="false">
            <intent-filter>
                <action android:name="android.intent.action.VIEW"/>
                <category android:name="android.intent.category.DEFAULT"/>
                <data android:scheme="https" android:host="demo.test" android:pathPrefix="/item"/>
            </intent-filter>
        </activity>
        <service android:name="com.demo.app.SyncService" android:exported="false"/>
        <receiver android:name="com.demo.app.BootReceiver" android:exported="true"/>
    </application>
</manifest>
"""


def _rea_envelope(package="com.demo.app", endpoint="https://api.demo.test/v1"):
    return {
        "ok": True,
        "data": {
            "evidence_id": "ev_abc123",
            "provider": {"id": "jadx", "name": "JADX headless", "version": "0.7.1"},
            "predicate_type": "structure",
            "operation": "inspect_android_package",
            "normalized_result": {
                "engine": {"name": "jadx"},
                "package_name": package,
                "version_name": "1.2",
                "version_code": "7",
                "min_sdk": "24",
                "target_sdk": "34",
                "permissions": ["android.permission.INTERNET", "android.permission.CAMERA"],
                "manifest": {"status": "complete", "text": MANIFEST_XML},
                "class_count": 120,
                "resource_count": 42,
                "signature_verification": "not_performed",
            },
            "confidence": "observed",
            "authority": "shipped-artifact",
            "limitations": ["Static decompiler observations do not establish runtime behavior."],
        },
        "meta": {"command": "inspect-android-package", "duration": "3.1s"},
    }


def test_build_analysis_real_envelope(tmp_path):
    ev = tmp_path / "evidence"
    ev.mkdir()
    (ev / "rea.inspect_android_package.json").write_text(
        json.dumps(_rea_envelope()), encoding="utf-8")
    out = build_analysis(
        tmp_path, package="com.demo.app", apk_name="demo.apk",
        agent_notes="## Flujos\n- login -> LoginActivity",
        unknowns=["no se verifico la red"],
        artifacts={"inspect": ev / "rea.inspect_android_package.json"},
    )
    md = out.read_text(encoding="utf-8")
    # metadatos reales de evidencia
    assert "ev_abc123" in md and "observed" in md and "shipped-artifact" in md
    assert "class: 120" in md
    # componentes del XML decodificado
    assert "MainActivity" in md and "DetailActivity" in md
    assert "SyncService" in md and "BootReceiver" in md
    assert "https://demo.test/item" in md          # deep link
    assert "Exportados (2)" in md
    # permisos sensibles + limitaciones declaradas por REA
    assert "`READ_SMS`" in md and "leer SMS" in md
    assert "no se verifico la red" in md
    assert "Static decompiler observations" in md


def test_manifest_parser_details():
    from rea_termux.manifest import parse_manifest, permission_risks, summary

    m = parse_manifest(MANIFEST_XML)
    assert m["package"] == "com.demo.app"
    assert m["launcher"] == ["com.demo.app.MainActivity"]
    assert m["min_sdk"] == "24" and m["target_sdk"] == "34"
    assert m["application"]["name"] == "com.demo.app.App"
    assert {c["name"] for c in m["components"]["activity"]} == {
        "com.demo.app.MainActivity", "com.demo.app.DetailActivity"}
    assert m["deep_links"][0]["uri"] == "https://demo.test/item"
    assert len(m["components"]["service"]) == 1
    s = summary(m)
    assert s["counts"]["activity"] == 2 and s["deep_links"]
    risks = permission_risks([p["name"] for p in m["permissions"]])
    assert any("CAMERA" in r for r in risks)
    assert any("READ_SMS" in r for r in risks)


def test_manifest_parser_tolerates_garbage():
    from rea_termux.manifest import parse_manifest

    assert parse_manifest("") == {}
    assert parse_manifest("no es xml") == {}
    assert parse_manifest("<manifest") == {}


def test_plan_markdown_mentions_policy():
    a = Answers(app_name="N", package_id="com.mine.n", screens=["home"])
    md = build_plan_markdown(a, source_package="com.src", evidence_summary="x")
    assert "Se reescribe, no se copia" in md
    assert "com.mine.n" in md
