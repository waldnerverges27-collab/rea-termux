"""Probe: valida los supuestos contra el REA real (Tier 0 del roadmap).

Todo el resto del programa asume cosas que salieron de documentacion, no de una
ejecucion real: nombre del paquete, flags del CLI, forma del JSON, nombres de
herramientas MCP, variable del jar de JADX. Este modulo las comprueba una a una
y emite un informe con ✅/❌ para que se pueda corregir en un solo sitio.

Tambien descubre capacidades (`rea --help` + `tools/list`) y deja una tabla de
alias, de modo que si REA renombra un comando el programa se adapta.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable

from .config import Config, data_home
from . import rea_cli

#: comandos del CLI que el programa necesita, con alias tolerantes
COMMAND_ALIASES: dict[str, list[str]] = {
    "inspect_android_package": [
        "inspect-android-package", "inspect-android-app", "android-package",
        "apk-inspect", "inspect-apk",
    ],
    "inspect_apk_signature": [
        "inspect-apk-signature", "apk-signature", "inspect-signature",
    ],
    "mcp": ["mcp", "serve", "stdio"],
}

#: herramientas MCP minimas para el flujo de analisis
REQUIRED_TOOLS = [
    "inspect_android_package",
    "search_android_classes",
    "inspect_android_class",
    "inspect_android_method",
    "trace_android_references",
]

#: supuestos sobre el formato de salida
JSON_EXPECTED_KEYS = ("manifest", "packageName", "package_name", "package")


@dataclass
class Check:
    id: str
    title: str
    ok: bool
    detail: str = ""
    critical: bool = True
    fix: str = ""


@dataclass
class ProbeReport:
    when: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%S"))
    rea_version: str = ""
    checks: list[Check] = field(default_factory=list)
    discovered: dict[str, Any] = field(default_factory=dict)
    aliases: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks if c.critical)

    def to_dict(self) -> dict[str, Any]:
        return {
            "when": self.when,
            "rea_version": self.rea_version,
            "ok": self.ok,
            "checks": [asdict(c) for c in self.checks],
            "discovered": self.discovered,
            "aliases": self.aliases,
        }


# --------------------------------------------------------------------- base
def _run(argv: list[str], timeout: float = 120.0) -> tuple[int, str, str]:
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace")
        return p.returncode, p.stdout or "", p.stderr or ""
    except FileNotFoundError:
        return 127, "", "no encontrado"
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout {timeout:.0f}s"


def _npx(cfg: Config, *args: str, timeout: float = 120.0):
    npx = shutil.which("npx")
    if not npx:
        return 127, "", "npx no encontrado (pkg install nodejs)"
    return _run([npx, "-y", f"rea-agents@{cfg.rea_version}", *args], timeout=timeout)


def _help_text(cfg: Config) -> tuple[int, str]:
    code, out, err = _npx(cfg, "--help", timeout=180)
    return code, out + err


def _commands_from_help(text: str) -> list[str]:
    """Extrae nombres de comando del texto de ayuda de REA."""
    found: list[str] = set()
    for line in text.splitlines():
        m = re.match(r"^\s{0,4}([a-z][a-z0-9]*(?:-[a-z0-9]+)+)\b", line)
        if m:
            found.add(m.group(1))
        m2 = re.match(r"^\s{2,6}([a-z][a-z0-9]{2,30})\s{2,}\S", line)
        if m2 and m2.group(1) not in {"usage", "options", "commands", "examples"}:
            found.add(m2.group(1))
    return sorted(found)


# ------------------------------------------------------------------- probe
def probe(
    cfg: Config,
    *,
    apk: str | Path | None = None,
    echo: Callable[[str], None] = lambda _m: None,
    deep: bool = True,
) -> ProbeReport:
    rep = ProbeReport()

    # 1) herramientas basicas
    npx = shutil.which("npx")
    rep.checks.append(Check(
        "npx", "npx disponible", bool(npx), npx or "no encontrado",
        fix="pkg install nodejs npm",
    ))
    node = shutil.which("node")
    node_ver = ""
    if node:
        code, out, _ = _run([node, "--version"], timeout=20)
        node_ver = out.strip()
    major = 0
    m = re.search(r"v(\d+)", node_ver)
    if m:
        major = int(m.group(1))
    rep.checks.append(Check(
        "node", "Node.js en una rama soportada",
        bool(node) and major >= 22, node_ver or "no encontrado",
        fix="REA pide 22.19+/24.11+/26+; si Termux no la trae, usa proot Debian",
    ))

    # 2) version de REA
    if npx:
        code, out, err = _npx(cfg, "--version", timeout=180)
        rep.rea_version = (out or err).strip().splitlines()[0] if (out or err) else ""
        rep.checks.append(Check(
            "rea_version", f"rea-agents@{cfg.rea_version} ejecutable", code == 0,
            rep.rea_version or f"rc={code}",
            fix="fija otra version en [rea_version] del TOML",
        ))

        # 3) descubrimiento de comandos
        hcode, help_text = _help_text(cfg)
        commands = _commands_from_help(help_text)
        rep.discovered["commands"] = commands
        rep.discovered["help_ok"] = hcode == 0
        for logical, aliases in COMMAND_ALIASES.items():
            hit = next((a for a in aliases if a in commands), None)
            if hit:
                rep.aliases[logical] = hit
            rep.checks.append(Check(
                f"cmd_{logical}", f"comando '{logical}' disponible", bool(hit),
                hit or f"no encontrado entre {len(commands)} comandos",
                critical=logical != "inspect_apk_signature",
                fix="" if hit else f"revisa `npx rea-agents@<v> --help`; alias probados: {aliases}",
            ))

    # 4) jar de JADX
    import os as _os
    jar = _os.environ.get("REA_JADX_MCP_JAR") or ""
    jar_path = Path(jar) if jar else None
    if jar_path is None:
        for cand in (data_home() / "android").glob("*.jar"):
            jar_path = cand
            break
    rep.checks.append(Check(
        "jadx_jar", "jar de JADX localizado", bool(jar_path and jar_path.is_file()),
        str(jar_path) if jar_path else "REA_JADX_MCP_JAR sin definir",
        fix="bash scripts/termux-bootstrap.sh --jadx",
    ))

    # 5) servidor MCP: handshake + catalogo
    tools: list[str] = []
    if deep and npx:
        try:
            from .mcp_client import MCPClient
            client = MCPClient(command=cfg.mcp.command, args=list(cfg.mcp.args),
                               env=dict(cfg.mcp.env),
                               startup_timeout_s=max(60.0, cfg.mcp.startup_timeout_s))
            client.start()
            try:
                tools = [t.get("name", "") for t in client.list_tools()]
                rep.discovered["server_info"] = client.server_info
            finally:
                client.close()
            rep.checks.append(Check(
                "mcp_handshake", "handshake MCP + tools/list", bool(tools),
                f"{len(tools)} herramientas",
                fix="revisa npx rea-agents@<v> mcp y el log .rea/mcp-server.log",
            ))
        except Exception as exc:
            rep.checks.append(Check(
                "mcp_handshake", "handshake MCP + tools/list", False,
                f"{type(exc).__name__}: {exc}",
                fix="npx -y rea-agents@<v> mcp (revisa que el comando mcp exista)",
            ))
        rep.discovered["tools"] = tools
        for t in REQUIRED_TOOLS:
            got = t in tools
            rep.checks.append(Check(
                f"tool_{t}", f"herramienta MCP '{t}'", got,
                "presente" if got else "ausente",
                critical=False,
                fix="actualiza [mcp].always_include con los nombres reales",
            ))
        # sugerencias: herramientas android realmente expuestas
        android = [t for t in tools if "android" in t.lower()]
        rep.discovered["android_tools"] = android[:40]

    # 6) CLI sobre un APK real (si se aporta)
    if apk:
        apk_path = Path(apk).expanduser()
        if not apk_path.is_file():
            rep.checks.append(Check("apk", "APK de prueba legible", False, str(apk_path)))
        else:
            cmd = rep.aliases.get("inspect_android_package", "inspect-android-package")
            code, out, err = _npx(cfg, cmd, str(apk_path.resolve()),
                                 "--format", "json", timeout=600)
            parsed = None
            if out.strip():
                try:
                    parsed = json.loads(out)
                except json.JSONDecodeError:
                    start = max(out.find("{"), out.find("["))
                    if start >= 0:
                        try:
                            parsed = json.loads(out[start:])
                        except json.JSONDecodeError:
                            parsed = None
            rep.checks.append(Check(
                "cli_inspect", f"`{cmd}` devuelve JSON", parsed is not None,
                f"rc={code}, {len(out)} bytes" if parsed is None else "JSON valido",
                fix=f"stderr: {(err or out)[:300]}",
            ))
            if parsed is not None:
                keys = _top_keys(parsed)
                rep.discovered["inspect_top_keys"] = keys
                from .report import normalized as _norm
                norm = _norm(parsed)
                shape = ("sobre {ok,data,meta}" if isinstance(parsed, dict)
                         and isinstance(parsed.get("data"), dict)
                         else "nodo de datos directo")
                rep.discovered["output_shape"] = shape
                rep.checks.append(Check(
                    "json_shape", "salida con normalized_result reconocible",
                    bool(norm), f"{shape} · top-level: {keys[:8]}",
                    fix="ajusta report.normalized() si la forma cambio",
                ))
                if isinstance(norm, dict):
                    rep.discovered["normalized_keys"] = list(norm)[:20]
                    mtext = ((norm.get("manifest") or {}).get("text") or "")
                    rep.checks.append(Check(
                        "manifest_xml", "manifest decodificado en texto (manifest.text)",
                        "<manifest" in mtext, f"{len(mtext)} chars",
                        fix="sin el XML se pierden componentes y deep links",
                    ))
                    if "<manifest" in mtext:
                        from .manifest import parse_manifest
                        mm = parse_manifest(mtext)
                        rep.checks.append(Check(
                            "manifest_parse", "manifest.py parsea el XML",
                            bool(mm.get("package")),
                            f"package={mm.get('package')} "
                            f"actividades={len(mm.get('components', {}).get('activity', []))}",
                        ))
                pkg = rea_cli._find_package(parsed)
                rep.checks.append(Check(
                    "package_detect", "package id detectable", bool(pkg),
                    pkg or "no encontrado", critical=False,
                ))
            # prueba de la firma (opcional)
            sig_cmd = rep.aliases.get("inspect_apk_signature")
            if sig_cmd:
                scode, sout, _serr = _npx(cfg, sig_cmd, str(apk_path.resolve()),
                                          "--format", "json", timeout=300)
                rep.checks.append(Check(
                    "cli_signature", f"`{sig_cmd}` funciona", scode == 0 and bool(sout.strip()),
                    f"rc={scode}", critical=False,
                ))

    return rep


def _top_keys(node: Any) -> list[str]:
    if isinstance(node, dict):
        return list(node.keys())[:30]
    if isinstance(node, list) and node and isinstance(node[0], dict):
        return list(node[0].keys())[:30]
    return []


# ------------------------------------------------------------------ persistencia
def report_path() -> Path:
    return data_home() / "probe.json"


def save(rep: ProbeReport, path: Path | None = None) -> Path:
    p = path or report_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(rep.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    return p


def load_aliases() -> dict[str, str]:
    """Alias descubiertos en el ultimo probe (para rea_cli)."""
    p = report_path()
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data.get("aliases") or {}


def print_report(rep: ProbeReport, echo: Callable[[str], None] = print) -> None:
    crit_fail = [c for c in rep.checks if not c.ok and c.critical]
    echo(f"REA: {rep.rea_version or '(desconocida)'} · "
         f"{sum(1 for c in rep.checks if c.ok)}/{len(rep.checks)} comprobaciones OK")
    for c in rep.checks:
        mark = "OK " if c.ok else ("!! " if c.critical else "-- ")
        echo(f"  [{mark}] {c.title}: {c.detail}")
        if not c.ok and c.fix:
            echo(f"         -> {c.fix}")
    if rep.discovered.get("commands"):
        echo(f"  comandos descubiertos: {len(rep.discovered['commands'])}")
    if rep.discovered.get("android_tools"):
        echo(f"  herramientas android: {', '.join(rep.discovered['android_tools'][:10])}")
    echo("")
    if crit_fail:
        echo(f"RESULTADO: {len(crit_fail)} supuesto(s) critico(s) no validados.")
    else:
        echo("RESULTADO: supuestos criticos validados.")


# ------------------------------------------------------------------ remediacion
def suggest_from_error(cfg: Config, error: dict[str, Any]) -> list[str]:
    """Traduce el sobre de error de REA a acciones concretas."""
    details = (error or {}).get("details") or {}
    diag = details.get("diagnostics") or {}
    out: list[str] = []
    code = str((error or {}).get("code") or "")
    reason = str(diag.get("reason") or (error or {}).get("message") or "")

    if diag.get("failure_kind") == "java_runtime" or "JDK 17" in reason:
        major = diag.get("java_major")
        out += [
            f"Java detectado: {major or 'desconocido'} (JADX necesita 17+ con jdk.compiler)",
            "Termux:  pkg install openjdk-17",
            "Verifica:  java -version && java --list-modules | grep jdk.compiler",
            "Si tu Termux no trae openjdk-17: proot-distro install debian && "
            "proot-distro login debian -- bash -lc 'apt install -y openjdk-17-jdk-headless'",
        ]
    if diag.get("provider_id") == "jadx" and "REA_JADX_MCP_JAR" not in reason:
        out.append("Comprueba el jar: export REA_JADX_MCP_JAR=~/.local/share/rea/android/"
                   "jadx-headless-mcp-0.7.1-all.jar")
    if code == "capability_unavailable" and not out:
        out.append("Capacidad no disponible en este host; revisa el proveedor indicado "
                   "en details.provider_id")
    if code == "unsupported_provider":
        out.append("REA no soporta ese proveedor en este sistema; para APKs el camino "
                   "es JADX (requiere JDK 17+)")
    if not out:
        out.append("Ejecuta `rea-termux probe --apk <apk>` para mas detalle")
    return out
