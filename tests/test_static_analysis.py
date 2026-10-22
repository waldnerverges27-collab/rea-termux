"""Escaneo estatico del APK y diferencias sin filtrar secretos."""
from __future__ import annotations

import json
import zipfile

from rea_termux.static_analysis import (analyze_apk, compare_reports, render_diff_markdown,
                                        render_markdown, shannon_entropy, write_outputs,
                                        _sanitize_url)

MANIFEST = '''<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="com.example.demo">
  <uses-sdk android:minSdkVersion="23" android:targetSdkVersion="34" />
  <uses-permission android:name="android.permission.INTERNET" />
  <uses-permission android:name="android.permission.CAMERA" />
  <application>
    <activity android:name=".MainActivity" android:exported="true">
      <intent-filter>
        <action android:name="android.intent.action.VIEW" />
        <category android:name="android.intent.category.DEFAULT" />
        <data android:scheme="demo" android:host="open" android:pathPrefix="/item" />
      </intent-filter>
    </activity>
  </application>
</manifest>'''


def make_apk(path, *, endpoint="https://api.example.com/v1/items?token=private_query_value",
             include_camera=False, sdk="com/google/firebase/analytics/FirebaseAnalytics"):
    secret = "AIzaSyA" + "B" * 35
    code = (f"GET {endpoint} api_key={secret} android.permission.INTERNET ".encode()
            + b"Manifest.permission.CAMERA " + sdk.encode()
            + b" -----BEGIN PRIVATE KEY----- "
            + b" high_entropy_token=Aq3Zx8JmP4vN6bY1cK9rT2wL5sD0fG7hQ")
    if not include_camera:
        code = code.replace(b"Manifest.permission.CAMERA", b"Manifest.permission.RECORD_AUDIO")
    config = {
        "project_info": {"project_id": "demo-firebase", "project_number": "12345",
                         "firebase_url": "https://demo-firebase.firebaseio.com"},
        "client": [{"api_key": [{"current_key": secret}], "client_info": {"mobilesdk_app_id": "1:123:android:abc"}}],
    }
    license_text = "GNU AFFERO GENERAL PUBLIC LICENSE\nVersion 3, 19 November 2007\n"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("classes.dex", code)
        zf.writestr("assets/google-services.json", json.dumps(config))
        zf.writestr("META-INF/LICENSE", license_text)
        zf.writestr("lib/arm64-v8a/libdemo.so", b"native-library")
    return path


def manifest_envelope(xml=MANIFEST):
    return {"ok": True, "data": {"normalized_result": {"manifest": {"text": xml}}}}


def test_scan_detects_urls_firebase_permissions_deeplinks_sdk_and_license(tmp_path):
    apk = make_apk(tmp_path / "sample.apk")
    report = analyze_apk(apk, manifest_envelope())
    assert report["package"] == "com.example.demo"
    assert report["urls"][0]["url"] == "https://api.example.com/v1/items?token=%3Credacted%3E"
    assert {x["host"] for x in report["hosts"]} >= {"api.example.com", "demo-firebase.firebaseio.com"}
    assert report["deep_links"][0]["uri"] == "demo://open/item"
    assert report["permissions"]["declared"] == [
        "android.permission.CAMERA", "android.permission.INTERNET"]
    assert "android.permission.INTERNET" in {x["name"] for x in report["permissions"]["observed_in_strings"]}
    assert "android.permission.CAMERA" in report["permissions"]["declared_not_observed"]
    assert report["firebase"][0]["project_id"] == "demo-firebase"
    assert "Firebase" in {x["name"] for x in report["sdk_fingerprints"]}
    assert report["licenses"][0]["license"] == "AGPL-3.0-or-later"


def test_url_sanitizer_redacts_queries_and_high_entropy_path_segments():
    token = "Aq3Zx8JmP4vN6bY1cK9rT2wL5sD0fG7hQ"
    safe, host = _sanitize_url(f"https://api.example.com/v1/{token}?access_token=private-value")
    assert safe == "https://api.example.com/v1/<redacted>?access_token=%3Credacted%3E"
    assert token not in safe and "private-value" not in safe
    assert host == "api.example.com"


def test_secrets_are_redacted_everywhere_and_entropy_is_reported(tmp_path):
    apk = make_apk(tmp_path / "secrets.apk")
    report = analyze_apk(apk, manifest_envelope())
    serialized = json.dumps(report) + render_markdown(report)
    assert "AIzaSyA" not in serialized
    assert "private_query_value" not in serialized
    assert all(item["value"] == "<redacted>" for item in report["secrets"])
    assert any(x["type"] == "google_api_key" for x in report["secrets"])
    assert shannon_entropy("aaaaaaaaaaaa") == 0
    assert shannon_entropy("J9mK2pQ7vX4zN8aB3cD6eF1gH5jL0wR9") > 4


def test_manifest_diff_is_skipped_when_one_xml_manifest_is_unavailable(tmp_path):
    a = analyze_apk(make_apk(tmp_path / "a.apk"), manifest_envelope())
    b = analyze_apk(make_apk(tmp_path / "b.apk"), None)
    diff = compare_reports(a, b)
    assert diff["categories"]["deep_links"]["comparable"] is False
    assert diff["categories"]["permissions"]["comparable"] is False
    assert "No comparable" in render_diff_markdown(diff)


def test_outputs_write_static_report_and_license_notice(tmp_path):
    report = analyze_apk(make_apk(tmp_path / "a.apk"), manifest_envelope())
    paths = write_outputs(tmp_path / "analysis", report)
    assert all(path.is_file() for path in paths.values())
    notice = paths["license_notice"].read_text()
    assert "AGPL-3.0-or-later" in notice and "no implica que se hayan copiado" in notice


def test_diff_reports_set_changes_but_never_values(tmp_path):
    a = analyze_apk(make_apk(tmp_path / "a.apk", endpoint="https://new.example.net/api"), manifest_envelope())
    b = analyze_apk(make_apk(tmp_path / "b.apk", endpoint="https://old.example.org/api"), manifest_envelope())
    diff = compare_reports(a, b)
    assert "https://new.example.net/api" in diff["categories"]["urls"]["added_in_current"]
    assert "https://old.example.org/api" in diff["categories"]["urls"]["removed_from_current"]
    md = render_diff_markdown(diff)
    assert "No se guardan ni comparan valores secretos" in md
    assert "AIzaSyA" not in json.dumps(diff)


def test_analyze_cli_writes_static_and_diff_artifacts(tmp_path, monkeypatch):
    from rea_termux import cli
    from rea_termux.rea_cli import CLIResult

    current = make_apk(tmp_path / "current.apk", endpoint="https://new.example.net/api")
    other = make_apk(tmp_path / "other.apk", endpoint="https://old.example.org/api")
    ws = tmp_path / "workspace"
    ws.mkdir()
    monkeypatch.setattr(cli, "preflight", lambda *a, **k: (True, []))
    monkeypatch.setattr(cli.rea_cli, "npx_available", lambda: True)
    monkeypatch.setattr(cli.rea_cli, "detect_package_name", lambda *a: "com.example.demo")

    def fake_inspect(cfg, apk, out_dir, *, full=True):
        out_dir.mkdir(parents=True, exist_ok=True)
        artifact = out_dir / "rea.inspect_android_package.json"
        artifact.write_text(json.dumps(manifest_envelope()), encoding="utf-8")
        return CLIResult([], 0, "", "", 0.01, json.loads(artifact.read_text())), artifact

    monkeypatch.setattr(cli.rea_cli, "inspect_android_package", fake_inspect)
    args = type("Args", (), {
        "config": None, "apk": str(current), "out": str(ws), "diff": str(other),
        "quiet": True, "fast": True, "no_vcs": True, "no_agent": True,
        "with_signature": False, "continue_on_error": False, "package": None,
    })()
    assert cli.cmd_analyze(args) == 0
    assert (ws / "analysis" / "STATIC_ANALYSIS.md").is_file()
    assert (ws / "analysis" / "STATIC_DIFF.json").is_file()
    assert (ws / "analysis" / "NOTICE.md").is_file()
    assert (ws / "analysis" / "ANALYSIS.md").is_file()


def test_analyze_parser_accepts_diff_option():
    from rea_termux.cli import build_parser
    args = build_parser().parse_args(["analyze", "first.apk", "--diff", "second.apk"])
    assert args.diff == "second.apk"


def test_invalid_apk_fails_closed_with_limitations(tmp_path):
    p = tmp_path / "bad.apk"
    p.write_bytes(b"not a zip")
    report = analyze_apk(p, manifest_envelope())
    assert report["scan"]["limitations"]
    assert report["urls"] == []
