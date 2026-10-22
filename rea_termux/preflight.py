"""Preflight: comprueba las piezas que REA necesita en Termux y propone el fix."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config, data_home


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    fix: str = ""
    optional: bool = False


def _run(cmd: list[str], timeout: float = 20.0) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except FileNotFoundError:
        return 127, "no encontrado"
    except subprocess.TimeoutExpired:
        return 124, "timeout"


def node_version_ok(cfg: Config) -> Check:
    code, out = _run(["node", "--version"])
    if code == 127:
        return Check("Node.js", False, "no instalado",
                     "pkg install nodejs npm", optional=False)
    m = re.search(r"v(\d+)\.(\d+)", out)
    if not m:
        return Check("Node.js", False, f"version ilegible: {out.strip()[:40]}",
                     "pkg install nodejs")
    major, minor = int(m.group(1)), int(m.group(2))
    ok = (major == 22 and minor >= 19) or (major == 24 and minor >= 11) or major == 26 or major > 26
    fix = (
        "REA exige Node 22.19+ / 24.11+ / 26+. Termux suele traer una rama "
        "aceptable; si no, usa proot-distro:\n"
        "    pkg install proot-distro && proot-distro install debian\n"
        "    proot-distro login debian -- bash -lc 'apt install -y nodejs npm'"
    )
    return Check("Node.js", ok, f"v{major}.{minor}", fix)


def npx_check() -> Check:
    if shutil.which("npx"):
        return Check("npx", True, shutil.which("npx") or "")
    return Check("npx", False, "no encontrado", "pkg install nodejs npm")


def java_check() -> Check:
    """JADX exige JDK 17+ con jdk.compiler (REA lo verifica con --list-modules)."""
    if not shutil.which("java"):
        return Check("JDK 17+ (JADX)", False, "java no encontrado",
                     "pkg install openjdk-17\n"
                     "(JDK COMPLETO, no un JRE: REA compila un puente en runtime)",
                     optional=False)
    code, out = _run(["java", "-version"])
    ver = (out or "").splitlines()[0] if out else ""
    m = re.search(r'"?(\d+)', ver)
    major = int(m.group(1)) if m else 0
    if major == 1:  # "1.8.0"
        m2 = re.search(r"1\.(\d+)", ver)
        major = int(m2.group(1)) if m2 else 0
    if major < 17:
        return Check("JDK 17+ (JADX)", False, ver or f"java {major}",
                     "pkg install openjdk-17", optional=False)
    code, mods = _run(["java", "--list-modules"], timeout=20)
    has_compiler = code == 0 and "jdk.compiler" in mods
    return Check(
        "JDK 17+ (JADX)", has_compiler,
        f"{ver} · jdk.compiler {'presente' if has_compiler else 'AUSENTE'}",
        "instala un JDK completo (no un JRE): pkg install openjdk-17",
        optional=False,
    )


def jadx_jar_check() -> Check:
    env = os.environ.get("REA_JADX_MCP_JAR")
    candidates = []
    if env:
        candidates.append(Path(env))
    candidates += list((data_home() / "android").glob("jadx-headless-mcp-*.jar"))
    candidates += list((Path.home() / ".local/share/rea/android").glob("*.jar"))
    for c in candidates:
        if c.is_file():
            return Check("JADX headless (jar)", True, str(c))
    return Check(
        "JADX headless (jar)", False, "no encontrado",
        "bash scripts/termux-bootstrap.sh --jadx\n"
        "o exporta REA_JADX_MCP_JAR=/ruta/jadx-headless-mcp-0.7.1-all.jar",
        optional=True,
    )


def ps_check() -> Check:
    """En Linux REA exige un `ps` compatible con procps para rastrear JADX."""
    if not shutil.which("ps"):
        return Check("ps (procps)", False, "no encontrado",
                     "pkg install procps", optional=True)
    code, out = _run(["ps", "-o", "pid="])
    ok = code == 0 and out.strip() != ""
    return Check("ps (procps)", ok, "ok" if ok else "no soporta -o pid=",
                 "pkg install procps", optional=True)


def android_tool_checks() -> list[Check]:
    """Herramientas Android auxiliares; son opcionales para REA pero mejoran el analisis."""
    specs = [
        ("apktool", "APKTool", "pkg install apktool"),
        ("aapt", "Android aapt", "pkg install aapt"),
        ("aapt2", "Android aapt2", "pkg install aapt aapt2"),
        ("apksigner", "Android apksigner", "pkg install apksigner"),
        ("zipalign", "Android zipalign", "pkg install aapt (incluye build tools en Termux)"),
    ]
    checks = []
    for command, label, fix in specs:
        path = shutil.which(command)
        checks.append(Check(label, bool(path), path or "no instalado", fix, optional=True))
    return checks


def android_sdk_check() -> Check:
    """Detecta plataformas android.jar para compilacion; no bloquea el analisis APK."""
    candidates = [os.environ.get("ANDROID_SDK_ROOT"), os.environ.get("ANDROID_HOME"),
                  str(Path.home() / "Android" / "Sdk"), str(Path.home() / "android-sdk")]
    prefix = os.environ.get("PREFIX")
    if prefix:
        candidates.append(str(Path(prefix) / "share" / "android-sdk"))
    for value in candidates:
        if not value:
            continue
        root = Path(value).expanduser()
        platforms = sorted(p.name for p in (root / "platforms").glob("android-*")
                           if (p / "android.jar").is_file()) if (root / "platforms").is_dir() else []
        if platforms:
            return Check("Android SDK platform", True,
                         f"{root} · {', '.join(platforms)}", optional=True)
    return Check("Android SDK platform", False, "android.jar no detectado (solo afecta compilacion Android)",
                 "instala una plataforma Android compatible y define ANDROID_HOME / ANDROID_SDK_ROOT",
                 optional=True)


def device_check() -> Check:
    if not shutil.which("adb"):
        return Check("adb (opcional)", False, "no instalado",
                     "pkg install android-tools", optional=True)
    code, out = _run(["adb", "devices"], timeout=15)
    lines = [l for l in out.splitlines()[1:] if l.strip()]
    return Check("adb (opcional)", code == 0, f"{len(lines)} dispositivo(s)",
                 "pkg install android-tools", optional=True)


def arch_check() -> Check:
    import platform
    mach = platform.machine()
    ok = mach in ("aarch64", "arm64", "armv8l", "armv7l", "x86_64")
    return Check("Arquitectura", ok, mach,
                 "Los motores nativos (Ghidra/IDA/Hopper) no corren en Android; "
                 "usa JADX (Java) para APKs.", optional=False)


def all_checks(cfg: Config) -> list[Check]:
    return [
        arch_check(),
        node_version_ok(cfg),
        npx_check(),
        java_check(),
        jadx_jar_check(),
        ps_check(),
        *android_tool_checks(),
        android_sdk_check(),
        device_check(),
    ]


def preflight(cfg: Config, *, verbose: bool = True) -> tuple[bool, list[Check]]:
    checks = all_checks(cfg)
    blocking = [c for c in checks if not c.ok and not c.optional]
    if verbose:
        for c in checks:
            mark = "OK " if c.ok else ("-- " if c.optional else "XX ")
            print(f"  [{mark}] {c.name}: {c.detail}")
            if not c.ok and c.fix:
                for line in c.fix.splitlines():
                    print(f"         -> {line}")
    return (not blocking), checks
