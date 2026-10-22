"""Deteccion e invocacion acotada de herramientas Android disponibles en Termux."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

MAX_OUTPUT_CHARS = 6000
TIMEOUTS = {"aapt": 90.0, "apksigner": 90.0, "zipalign": 90.0, "apktool": 300.0}
VERSION_ARGS = {
    "apktool": ["--version"], "aapt": ["version"], "aapt2": ["version"],
    "apksigner": ["version"], "zipalign": ["-h"], "adb": ["version"],
    "dexdump": ["-h"], "d8": ["--version"], "gradle": ["--version"],
}
EXTRA_TOOLS = ("sdkmanager", "apkanalyzer", "bundletool", "fastboot")


def _sdk_root() -> Path | None:
    candidates = [os.environ.get("ANDROID_SDK_ROOT"), os.environ.get("ANDROID_HOME"),
                  str(Path.home() / "Android" / "Sdk"),
                  str(Path.home() / "android-sdk"),
                  str(Path(os.environ.get("PREFIX", "/data/data/com.termux/files/usr"))
                      / "share" / "android-sdk")]
    for candidate in candidates:
        if candidate:
            root = Path(candidate).expanduser()
            if root.is_dir():
                return root.resolve()
    return None


def _sdk_inventory() -> dict[str, Any]:
    root = _sdk_root()
    if not root:
        return {"available": False, "root": "", "platforms": [], "build_tools": []}
    platforms = sorted(p.name for p in (root / "platforms").glob("android-*")
                       if (p / "android.jar").is_file()) if (root / "platforms").is_dir() else []
    build_tools = sorted(p.name for p in (root / "build-tools").iterdir() if p.is_dir()) \
        if (root / "build-tools").is_dir() else []
    return {"available": bool(platforms or build_tools), "root": str(root),
            "platforms": platforms, "build_tools": build_tools}


def inventory() -> dict[str, dict[str, Any]]:
    """Lista herramientas locales; no descarga, instala ni ejecuta el APK."""
    found: dict[str, dict[str, Any]] = {}
    for name, args in VERSION_ARGS.items():
        path = shutil.which(name)
        item: dict[str, Any] = {"available": bool(path), "path": path or ""}
        if path:
            try:
                result = subprocess.run([path, *args], capture_output=True, text=True,
                                        timeout=8, encoding="utf-8", errors="replace")
                output = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()
                item["version"] = output[:240]
            except (OSError, subprocess.TimeoutExpired):
                item["version"] = "no se pudo consultar"
        found[name] = item
    for name in EXTRA_TOOLS:
        path = shutil.which(name)
        found[name] = {"available": bool(path), "path": path or ""}
    return found


def _run(name: str, argv: list[str], *, timeout: float | None = None) -> dict[str, Any]:
    started = time.monotonic()
    try:
        proc = subprocess.run(argv, capture_output=True, text=True,
                              timeout=timeout or TIMEOUTS.get(name, 90),
                              encoding="utf-8", errors="replace")
        stdout, stderr = proc.stdout or "", proc.stderr or ""
        return {
            "tool": name, "available": True, "ok": proc.returncode == 0,
            "returncode": proc.returncode, "elapsed_s": round(time.monotonic() - started, 3),
            "command": argv, "stdout": stdout[:MAX_OUTPUT_CHARS],
            "stderr": stderr[:MAX_OUTPUT_CHARS],
            "truncated": len(stdout) > MAX_OUTPUT_CHARS or len(stderr) > MAX_OUTPUT_CHARS,
        }
    except FileNotFoundError:
        return {"tool": name, "available": False, "ok": False,
                "error": "no instalado", "elapsed_s": round(time.monotonic() - started, 3)}
    except subprocess.TimeoutExpired:
        return {"tool": name, "available": True, "ok": False, "error": "timeout",
                "elapsed_s": round(time.monotonic() - started, 3), "command": argv}
    except OSError as exc:
        return {"tool": name, "available": True, "ok": False,
                "error": type(exc).__name__, "elapsed_s": round(time.monotonic() - started, 3),
                "command": argv}


def _fresh_output(parent: Path, apk: Path) -> Path:
    """Directorio fijo versionado por el hash del APK, sin seguir symlinks."""
    digest_hash = hashlib.sha256()
    with apk.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest_hash.update(chunk)
    digest = digest_hash.hexdigest()[:12]
    base = parent / "apktool"
    base.mkdir(parents=True, exist_ok=True)
    if base.is_symlink() or base.resolve().parent != parent.resolve():
        raise OSError("directorio apktool inseguro; no se sigue un symlink")
    target = base / digest
    if target.is_symlink():
        raise OSError("destino apktool es un symlink; se rehusa escribir fuera del workspace")
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    return target


def process_apk(apk_path: str | Path, analysis_dir: str | Path,
                *, decode_resources: bool = True) -> dict[str, Any]:
    """Usa herramientas Android instaladas para metadatos, firma y recursos.

    Todas las operaciones son de lectura/verificacion; no instala ni ejecuta el APK,
    ni firma, alinea o modifica el archivo de entrada.
    """
    apk = Path(apk_path).expanduser().resolve()
    out = Path(analysis_dir).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    tools = inventory()
    report: dict[str, Any] = {
        "apk": apk.name, "tool_inventory": tools, "android_sdk": _sdk_inventory(), "steps": [],
        "read_only": True, "limitations": [], "artifacts": {},
    }
    if not report["android_sdk"].get("platforms"):
        report["limitations"].append(
            "No se detecto platform SDK con android.jar; la inspeccion APK funciona, "
            "pero compilar Android necesita instalar una plataforma compatible y definir ANDROID_HOME."
        )
    if not apk.is_file():
        report["limitations"].append("APK inexistente; no se ejecutaron herramientas.")
        return report

    aapt = tools.get("aapt", {}).get("path")
    if aapt:
        step = _run("aapt", [aapt, "dump", "badging", str(apk)])
        text = step.get("stdout", "")
        m = re.search(r"package: name='([^']+)'(?: versionCode='([^']*)')?(?: versionName='([^']*)')?", text)
        if m:
            report["aapt_metadata"] = {"package": m.group(1), "version_code": m.group(2) or "",
                                       "version_name": m.group(3) or ""}
        report["steps"].append(step)
    else:
        report["limitations"].append("aapt no instalado; package se intentara leer con JADX/AXML.")

    apksigner = tools.get("apksigner", {}).get("path")
    if apksigner:
        report["steps"].append(_run("apksigner", [apksigner, "verify", "--verbose",
                                                   "--print-certs", str(apk)]))
    else:
        report["limitations"].append("apksigner no instalado; no se verifico la firma con Android Build Tools.")

    zipalign = tools.get("zipalign", {}).get("path")
    if zipalign:
        report["steps"].append(_run("zipalign", [zipalign, "-c", "-v", "4", str(apk)]))
    else:
        report["limitations"].append("zipalign no instalado; se omitio la comprobacion de alineacion.")

    apktool = tools.get("apktool", {}).get("path")
    if decode_resources and apktool:
        try:
            target = _fresh_output(out, apk)
            step = _run("apktool", [apktool, "d", "--force", "--no-src", "--output",
                                     str(target), str(apk)])
            manifest = target / "AndroidManifest.xml"
            if step.get("ok") and manifest.is_file():
                step["manifest_xml"] = str(manifest)
                report["artifacts"]["apktool_manifest"] = str(manifest)
            report["steps"].append(step)
        except OSError as exc:
            report["steps"].append({"tool": "apktool", "available": True, "ok": False,
                                    "error": str(exc)[:300]})
    elif not apktool:
        report["limitations"].append("apktool no instalado; no se decodificaron recursos localmente.")
    else:
        report["limitations"].append("Decodificacion apktool omitida por configuracion.")

    return report


def write_report(analysis_dir: str | Path, report: dict[str, Any]) -> dict[str, Path]:
    out = Path(analysis_dir)
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / "ANDROID_TOOLS.json"
    md_path = out / "ANDROID_TOOLS.md"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    lines = [f"# Herramientas Android — `{report.get('apk', '')}`", "",
             "El APK de entrada no se modifico, instalo ni ejecuto.", "", "## Herramientas detectadas", ""]
    for name, item in report.get("tool_inventory", {}).items():
        state = item.get("path") or "no instalado"
        version_lines = (item.get("version") or "").strip().splitlines()
        version = version_lines[0][:120] if version_lines else ""
        lines.append(f"- `{name}`: `{state}`" + (f" — {version}" if version else ""))
    sdk = report.get("android_sdk") or {}
    lines += ["", "## Android SDK", ""]
    lines.append(f"- Raiz: `{sdk.get('root') or 'no detectada'}`")
    lines.append("- Plataformas (`android.jar`): " +
                 (", ".join(f"`{x}`" for x in sdk.get("platforms", [])) or "ninguna detectada"))
    lines.append("- Build Tools: " +
                 (", ".join(f"`{x}`" for x in sdk.get("build_tools", [])) or "ninguna detectada"))
    lines += ["", "## Operaciones de lectura/verificacion", ""]
    for step in report.get("steps", []):
        state = "OK" if step.get("ok") else "aviso/error"
        lines.append(f"### {step.get('tool')} — {state}")
        if step.get("error"):
            lines.append(f"- {step['error']}")
        output = (step.get("stdout") or step.get("stderr") or "").strip()
        if output:
            lines += ["", "```text", output[:1800], "```"]
        if step.get("manifest_xml"):
            lines.append(f"- Manifest decodificado: `{step['manifest_xml']}`")
        lines.append("")
    if report.get("aapt_metadata"):
        lines += ["## Identidad segun aapt", "",
                  f"- Package: `{report['aapt_metadata'].get('package')}`",
                  f"- Version: `{report['aapt_metadata'].get('version_name')}` "
                  f"(`{report['aapt_metadata'].get('version_code')}`)", ""]
    if report.get("limitations"):
        lines += ["## Herramientas/operaciones omitidas", ""]
        lines += [f"- {x}" for x in report["limitations"]]
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"android_tools_json": json_path, "android_tools_report": md_path}
