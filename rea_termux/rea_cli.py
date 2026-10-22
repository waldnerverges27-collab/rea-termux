"""Invocaciones deterministas del CLI de REA.

Antes de dejar que el modelo llame herramientas a ciegas, se ejecuta el CLI
de REA y se guarda la evidencia cruda en disco. Ventajas:
  * el agente razona sobre artefactos (barato en tokens y auditable);
  * si el modelo falla, el analisis ya esta hecho;
  * se puede diffear entre corridas.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from datetime import datetime
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Config


#: ruta opcional donde registrar fallos del CLI (Tier 0.4)
_FAIL_LOG: Path | None = None


def set_fail_log(path: str | Path | None) -> None:
    global _FAIL_LOG
    _FAIL_LOG = Path(path) if path else None


def _log_failure(argv: list[str], rc: int, stdout: str, stderr: str,
                 duration: float) -> None:
    if _FAIL_LOG is None:
        return
    try:
        _FAIL_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(_FAIL_LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "t": datetime.now().isoformat(timespec="seconds"),
                "argv": argv, "rc": rc, "duration_s": round(duration, 2),
                "stderr": (stderr or "")[-1500:],
                "stdout_head": (stdout or "")[:400],
            }, ensure_ascii=False) + "\n")
    except OSError:
        pass


def command_for(logical: str, default: str) -> str:
    """Devuelve el alias real descubierto por `probe` (si lo hay)."""
    try:
        from .probe import load_aliases
        return load_aliases().get(logical, default)
    except Exception:
        return default


#: codigos de error que REA devuelve dentro de un JSON con exit code 0
REA_ERROR_KEYS = ("code", "category", "message", "retryable", "remediation")


@dataclass
class CLIResult:
    argv: list[str]
    returncode: int
    stdout: str
    stderr: str
    duration_s: float
    json_data: Any = None
    #: sobre de error de REA {code, category, message, remediation, details}
    error: dict[str, Any] | None = None

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and self.error is None

    @property
    def error_message(self) -> str:
        if not self.error:
            return ""
        parts = [str(self.error.get("code") or ""),
                 str(self.error.get("category") or ""),
                 str(self.error.get("message") or "")]
        return " · ".join(p for p in parts if p)

    @property
    def remediation(self) -> str:
        if not self.error:
            return ""
        rem = self.error.get("remediation")
        if isinstance(rem, dict):
            return str(rem.get("action") or rem.get("message") or "")
        return str(rem or "")


def detect_error_envelope(data: Any) -> dict[str, Any] | None:
    """REA puede responder 200/rc=0 con un objeto de error: hay que detectarlo."""
    if not isinstance(data, dict):
        return None
    if not all(k in data for k in ("code", "message")):
        return None
    # un resultado normal trae datos (manifest, classes...), un error trae 'details'
    if any(k in data for k in ("manifest", "classes", "packageName", "package_name")):
        return None
    if "details" in data or "remediation" in data or data.get("retryable") is not None:
        return data
    return None


def npx_available() -> bool:
    return bool(shutil.which("npx"))


def rea_argv(cfg: Config, *args: str) -> list[str]:
    pinned = f"rea-agents@{cfg.rea_version}"
    return ["npx", "-y", pinned, *args]


def run(cfg: Config, *args: str, timeout: float = 600.0,
        expect_json: bool = True) -> CLIResult:
    argv = rea_argv(cfg, *args)
    env = os.environ.copy()
    env.setdefault("NO_COLOR", "1")
    env.setdefault("FORCE_COLOR", "0")
    t0 = time.time()
    proc = subprocess.run(
        argv, capture_output=True, text=True, timeout=timeout, env=env,
        encoding="utf-8", errors="replace",
    )
    data = None
    if expect_json:
        out = proc.stdout.strip()
        if out:
            try:
                data = json.loads(out)
            except json.JSONDecodeError:
                start = min(
                    [i for i in (out.find("{"), out.find("[")) if i >= 0] or [-1]
                )
                if start >= 0:
                    try:
                        data = json.loads(out[start:])
                    except json.JSONDecodeError:
                        data = None
    error_env = detect_error_envelope(data) if expect_json else None
    duration = time.time() - t0
    if proc.returncode != 0 or error_env is not None:
        _log_failure(argv, proc.returncode, proc.stdout, proc.stderr, duration)
    return CLIResult(
        argv=argv,
        returncode=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
        duration_s=duration,
        json_data=data,
        error=error_env,
    )


def save_error(out_dir: Path, name: str, res: CLIResult) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / name
    p.write_text(json.dumps(res.error or {"stderr": res.stderr}, indent=2,
                            ensure_ascii=False), encoding="utf-8")
    return p


def save_artifact(out_dir: Path, name: str, res: CLIResult) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / name
    if res.json_data is not None:
        target.write_text(
            json.dumps(res.json_data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    else:
        target.write_text(res.stdout or res.stderr, encoding="utf-8")
    return target


def inspect_android_package(cfg: Config, apk: str | Path, out_dir: Path,
                            *, full: bool = True) -> tuple[CLIResult, Path | None]:
    """`rea inspect-android-package` -> manifest, clases, firmas."""
    cmd = command_for("inspect_android_package", "inspect-android-package")
    args = [cmd, str(Path(apk).resolve()), "--format", "json"]
    if full:
        args.append("--full-output")
    res = run(cfg, *args)
    if res.error is not None:
        save_error(out_dir, "rea.inspect_android_package.error.json", res)
        return res, None
    art = save_artifact(out_dir, "rea.inspect_android_package.json", res) if res.stdout.strip() else None
    return res, art


def inspect_apk_signature(cfg: Config, apk: str | Path, out_dir: Path) -> tuple[CLIResult, Path | None]:
    cmd = command_for("inspect_apk_signature", "inspect-apk-signature")
    res = run(cfg, cmd, str(Path(apk).resolve()), "--format", "json")
    if res.error is not None:
        save_error(out_dir, "rea.inspect_apk_signature.error.json", res)
        return res, None
    art = save_artifact(out_dir, "rea.inspect_apk_signature.json", res) if res.stdout.strip() else None
    return res, art


def detect_package_name(apk: str | Path, artifacts: Path | None = None) -> str | None:
    """Intenta sacar el package id del JSON de REA ya guardado."""
    if artifacts and artifacts.is_file():
        try:
            data = json.loads(artifacts.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = None
        pkg = _find_package(data)
        if pkg:
            return pkg
    # fallback 1: parser AXML propio (sin dependencias)
    try:
        from .apkmeta import detect_package as _axml_detect
        pkg = _axml_detect(apk)
        if pkg:
            return pkg
    except Exception:
        pass
    # fallback 2: aapt
    aapt = shutil.which("aapt")
    if aapt:
        try:
            out = subprocess.run(
                [aapt, "dump", "badging", str(apk)],
                capture_output=True, text=True, timeout=60,
            ).stdout
            for tok in out.split():
                if tok.startswith("package:") or tok.startswith("name="):
                    pass
            line = next((l for l in out.splitlines() if l.startswith("package:")), "")
            for part in line.split():
                if part.startswith("name="):
                    return part.split("=", 1)[1].strip("'\"")
        except Exception:
            return None
    return None


def _find_package(node: Any, depth: int = 0) -> str | None:
    if depth > 8:
        return None
    if isinstance(node, dict):
        for k in ("packageName", "package_name", "package", "manifestPackage", "id"):
            v = node.get(k)
            if isinstance(v, str) and "." in v and " " not in v and len(v) < 160:
                return v
        for v in node.values():
            got = _find_package(v, depth + 1)
            if got:
                return got
    elif isinstance(node, list):
        for v in node[:200]:
            got = _find_package(v, depth + 1)
            if got:
                return got
    return None
