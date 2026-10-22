"""Verificacion de que el proyecto compila/analiza, y bucle de auto-reparacion.

Filosofia: en Termux no siempre hay Android SDK ni Flutter, asi que el modulo
tiene tres niveles y siempre dice cual uso:

  1. **toolchain real**  — `flutter analyze`, `tsc --noEmit`, `gradle compileDebugKotlin`
  2. **compilador suelto** — `dart analyze`, `kotlinc`, `node --check`
  3. **chequeo estatico propio** — siempre disponible: llaves/paréntesis
     balanceados, marcadores sin sustituir, `package` que no cuadra con la ruta,
     archivos vacios. Es poco profundo pero detecta los fallos que de verdad
     rompen una generacion.

Los errores se normalizan a `Finding(file, line, col, severity, message, code)`.
Con eso el agente recibe contexto preciso y puede corregir.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Iterable

# --------------------------------------------------------------------- modelo
SEVERITIES = ("error", "warning", "info")


@dataclass
class Finding:
    file: str
    line: int
    col: int
    severity: str
    message: str
    code: str = ""
    source: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def one_line(self) -> str:
        loc = f"{self.file}:{self.line}" + (f":{self.col}" if self.col else "")
        tag = f"[{self.severity}]"
        code = f" ({self.code})" if self.code else ""
        return f"{loc} {tag} {self.message}{code}"


@dataclass
class VerifyResult:
    ok: bool
    tool: str
    findings: list[Finding] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    raw: str = ""
    duration_s: float = 0.0

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "error"]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "warning"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok, "tool": self.tool,
            "errors": [f.to_dict() for f in self.errors],
            "warnings": [f.to_dict() for f in self.warnings],
            "notes": self.notes,
            "duration_s": round(self.duration_s, 2),
        }

    def summary(self, limit: int = 12) -> str:
        if self.ok:
            head = f"OK ({self.tool})"
            if self.warnings:
                head += f" · {len(self.warnings)} aviso(s)"
            return head
        head = f"{len(self.errors)} error(es), {len(self.warnings)} aviso(s) ({self.tool})"
        lines = [head] + [f"  - {f.one_line()}" for f in self.errors[:limit]]
        if len(self.errors) > limit:
            lines.append(f"  - … y {len(self.errors) - limit} mas")
        return "\n".join(lines)


# -------------------------------------------------------------------- parsers
_DART_RE = re.compile(
    r"^\s*(error|warning|info)\s+•\s+(.*?)\s+•\s+(.+?):(\d+):(\d+)\s+•\s+(\S+)"
)
_TSC_RE = re.compile(
    r"^(?P<file>[^\s(].*?)\((?P<line>\d+),(?P<col>\d+)\):\s*(?P<sev>error|warning)\s*"
    r"(?P<code>TS\d+)?:?\s*(?P<msg>.*)$"
)
_KOTLIN_RE = re.compile(
    r"^(?P<sev>[ew]):\s*(?:file://)?(?P<file>[^:\s]+):(?P<line>\d+):(?P<col>\d+)\s+(?P<msg>.*)$"
)
_GENERIC_RE = re.compile(
    r"^(?P<file>[\w./\\-]+\.\w+):(?P<line>\d+)(?::(?P<col>\d+))?:\s*"
    r"(?P<sev>error|warning)?:?\s*(?P<msg>.*)$"
)


def parse_dart(text: str) -> list[Finding]:
    out = []
    for line in text.splitlines():
        m = _DART_RE.match(line)
        if m:
            sev, msg, path, ln, col, code = m.groups()
            out.append(Finding(path, int(ln), int(col), sev, msg.strip(), code, "dart"))
    return out


def parse_tsc(text: str) -> list[Finding]:
    out = []
    for line in text.splitlines():
        m = _TSC_RE.match(line)
        if m:
            d = m.groupdict()
            out.append(Finding(d["file"], int(d["line"]), int(d["col"]),
                               d["sev"], d["msg"].strip(), d.get("code") or "", "tsc"))
    return out


def parse_kotlin(text: str) -> list[Finding]:
    out = []
    for line in text.splitlines():
        m = _KOTLIN_RE.match(line.strip())
        if m:
            d = m.groupdict()
            sev = "error" if d["sev"] == "e" else "warning"
            out.append(Finding(d["file"], int(d["line"]), int(d["col"]), sev,
                               d["msg"].strip(), "", "kotlin"))
        elif "FAILED" in line and "Task" in line:
            out.append(Finding("", 0, 0, "error", line.strip(), "", "gradle"))
    return out


def parse_generic(text: str) -> list[Finding]:
    out = []
    for line in text.splitlines():
        m = _GENERIC_RE.match(line.strip())
        if m:
            d = m.groupdict()
            sev = d.get("sev") or "error"
            out.append(Finding(d["file"], int(d["line"]), int(d.get("col") or 0),
                               sev, (d.get("msg") or "").strip(), "", "generic"))
    return out


PARSERS = {
    "dart": parse_dart,
    "tsc": parse_tsc,
    "kotlin": parse_kotlin,
    "generic": parse_generic,
}


def parse_output(tool: str, text: str) -> list[Finding]:
    """Prueba el parser del tool y, si no saca nada, cae al generico."""
    parser = PARSERS.get(tool, parse_generic)
    found = parser(text)
    if not found and tool != "generic":
        found = parse_generic(text)
    return found


# ------------------------------------------------------------- chequeo estatico
_PLACEHOLDER_RE = re.compile(r"__[A-Z][A-Z0-9_]{2,}__")
_PAIRS = {"{": "}", "(": ")", "[": "]"}


def _strip_code_like(text: str, ext: str) -> str:
    """Quita strings y comentarios para contar llaves de verdad."""
    out: list[str] = []
    i, n = 0, len(text)
    line_comment = "//" if ext in (".kt", ".kts", ".dart", ".ts", ".tsx", ".js", ".java") else "#"
    block = ext in (".kt", ".kts", ".java", ".dart", ".ts", ".tsx", ".js")
    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if text.startswith(line_comment, i):
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        if block and text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        if ch in ("'", '"'):
            quote = ch
            triple = text.startswith(quote * 3, i)
            if triple:
                j = text.find(quote * 3, i + 3)
                i = n if j < 0 else j + 3
                continue
            i += 1
            while i < n:
                if text[i] == "\\":
                    i += 2
                    continue
                if text[i] == quote or text[i] == "\n":
                    i += 1
                    break
                i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def static_checks(root: Path, stack: str, *, max_files: int = 400) -> list[Finding]:
    """Chequeos que siempre se pueden correr, sin toolchain."""
    findings: list[Finding] = []
    exts = {
        "flutter": (".dart",),
        "android-kotlin": (".kt", ".kts", ".xml"),
        "react-native": (".ts", ".tsx", ".js", ".json"),
        "web-pwa": (".ts", ".tsx", ".js", ".json", ".html"),
    }.get(stack, (".kt", ".dart", ".ts", ".tsx", ".js"))
    files = [p for p in root.rglob("*")
             if p.is_file() and p.suffix in exts
             and "node_modules" not in p.parts and "build" not in p.parts
             and ".rea" not in p.parts and ".git" not in p.parts]
    for p in files[:max_files]:
        rel = str(p.relative_to(root))
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if not text.strip():
            findings.append(Finding(rel, 1, 1, "warning", "archivo vacio", "empty"))
            continue
        for m in _PLACEHOLDER_RE.finditer(text):
            line = text.count("\n", 0, m.start()) + 1
            findings.append(Finding(rel, line, m.start() % 200 + 1, "error",
                                    f"marcador de plantilla sin sustituir: {m.group(0)}",
                                    "placeholder"))
            break
        if p.suffix in (".kt", ".kts", ".dart", ".ts", ".tsx", ".js"):
            clean = _strip_code_like(text, p.suffix)
            stack_chars: list[str] = []
            for idx, ch in enumerate(clean):
                if ch in _PAIRS:
                    stack_chars.append(ch)
                elif ch in _PAIRS.values():
                    if not stack_chars or _PAIRS[stack_chars[-1]] != ch:
                        line = clean.count("\n", 0, idx) + 1
                        findings.append(Finding(rel, line, 1, "error",
                                                f"cierre '{ch}' sin apertura", "braces"))
                        stack_chars = []
                        break
                    stack_chars.pop()
            else:
                if stack_chars:
                    findings.append(Finding(rel, clean.count("\n") + 1, 1, "error",
                                            f"quedan {len(stack_chars)} bloque(s) sin cerrar",
                                            "braces"))
        if p.suffix in (".kt", ".kts"):
            m = re.search(r"^\s*package\s+([\w.]+)", text, re.M)
            if m:
                pkg = m.group(1)
                if pkg.startswith("__"):
                    findings.append(Finding(rel, 1, 1, "error",
                                            f"package sin sustituir: {pkg}", "package"))
                else:
                    expect = pkg.replace(".", "/")
                    if expect not in rel.replace("\\", "/"):
                        findings.append(Finding(
                            rel, 1, 1, "warning",
                            f"package '{pkg}' no coincide con la ruta del archivo",
                            "package-path"))
    return findings


# ------------------------------------------------------------------ ejecucion
def _run(cmd: list[str], cwd: Path, timeout: float) -> tuple[int, str, float]:
    import time

    t0 = time.time()
    try:
        p = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                           timeout=timeout, encoding="utf-8", errors="replace")
        return p.returncode, (p.stdout or "") + (p.stderr or ""), time.time() - t0
    except FileNotFoundError:
        return 127, f"{cmd[0]} no encontrado", time.time() - t0
    except subprocess.TimeoutExpired:
        return 124, f"timeout {timeout:.0f}s: {' '.join(cmd)}", time.time() - t0


DEFAULT_COMMANDS: dict[str, list[tuple[str, str, list[str]]]] = {
    # stack -> [(nombre, parser, argv)]
    "flutter": [
        ("flutter", "dart", ["flutter", "analyze", "--no-pub", "--no-fatal-infos"]),
        ("dart", "dart", ["dart", "analyze", "--no-fatal-infos"]),
    ],
    "android-kotlin": [
        ("gradle", "kotlin", ["gradle", "compileDebugKotlin", "--offline", "-q"]),
        ("kotlinc", "kotlin", ["kotlinc", "-d", "/dev/null"]),
    ],
    "react-native": [
        ("tsc", "tsc", ["npx", "--no-install", "tsc", "--noEmit", "-p", "."]),
    ],
    "web-pwa": [
        ("tsc", "tsc", ["npx", "--no-install", "tsc", "--noEmit", "-p", "."]),
    ],
}

TIMEOUTS = {"flutter": 300.0, "gradle": 420.0, "tsc": 180.0, "dart": 240.0,
            "kotlinc": 300.0}


def detect_stack(root: Path) -> str:
    if (root / "pubspec.yaml").is_file():
        return "flutter"
    if (root / "app" / "build.gradle.kts").is_file() or (root / "build.gradle.kts").is_file():
        return "android-kotlin"
    if (root / "app.json").is_file() and (root / "package.json").is_file():
        return "react-native"
    if (root / "package.json").is_file():
        return "web-pwa"
    for sub in ("app/flutter", "app/android-kotlin", "app/react-native", "app/web-pwa"):
        if (root / sub).is_dir():
            return detect_stack(root / sub)
    return "generic"


def tool_available(name: str) -> bool:
    if name.startswith("npx"):
        return bool(shutil.which("npx"))
    return bool(shutil.which(name))


def verify(
    root: str | Path,
    *,
    stack: str | None = None,
    use_toolchain: bool = True,
    commands: dict[str, str] | None = None,
    timeout: float | None = None,
) -> VerifyResult:
    """Verifica el proyecto: toolchain si hay, y siempre chequeo estatico."""
    root = Path(root).resolve()
    stack = stack or detect_stack(root)
    notes: list[str] = []
    findings: list[Finding] = []
    tool = "static"

    if use_toolchain:
        for name, parser, argv in DEFAULT_COMMANDS.get(stack, []):
            cmd = argv
            if commands and stack in commands and commands[stack] and name == DEFAULT_COMMANDS[stack][0][0]:
                cmd = commands[stack].split()
                name = cmd[0]
            exe = cmd[0]
            if exe == "npx":
                ok_exe = bool(shutil.which("npx")) and (root / "package.json").is_file()
            else:
                ok_exe = bool(shutil.which(exe))
            if not ok_exe:
                notes.append(f"{exe}: no disponible en este equipo")
                continue
            tmo = timeout or TIMEOUTS.get(name, 240.0)
            rc, out, dur = _run(cmd, root, tmo)
            if rc == 127 or (not out.strip() and rc != 0):
                notes.append(f"{name}: no ejecutable ({out.strip()[:120]})")
                continue
            parsed = parse_output(parser, out)
            # algunos analizadores devuelven 1 con solo avisos
            real_errors = [f for f in parsed if f.severity == "error"]
            tool = name
            findings = parsed
            if rc == 0 and not real_errors:
                findings = parsed
            if not parsed and rc != 0:
                findings = [Finding("", 0, 0, "error",
                                    out.strip().splitlines()[-1][:300] if out.strip()
                                    else f"{name} fallo (rc={rc})", "", name)]
            return VerifyResult(
                ok=not [f for f in findings if f.severity == "error"],
                tool=name, findings=findings,
                notes=notes + ["chequeo estatico omitido: el toolchain respondio"],
                raw=out[-4000:], duration_s=dur,
            )

    static = static_checks(root, stack)
    findings = static
    if tool != "static":
        notes.append("sin toolchain: resultado solo estatico")
    else:
        notes.append("sin toolchain disponible: resultado solo estatico "
                     "(llaves, marcadores, package/ruta)")
    return VerifyResult(ok=not [f for f in findings if f.severity == "error"],
                        tool="static", findings=findings, notes=notes)


# ------------------------------------------------------------ bucle de arreglo
class SelfHeal:
    """verify -> pedir al agente que corrija -> verify, hasta que compile."""

    def __init__(self, session: Any, *, max_rounds: int = 3, stack: str | None = None):
        self.session = session
        self.max_rounds = max_rounds
        self.stack = stack
        self.history: list[dict[str, Any]] = []

    def run(self) -> dict[str, Any]:
        ws = Path(self.session.ws)
        result = verify(ws, stack=self.stack)
        self._record(result, round_no=0)
        round_no = 0
        while not result.ok and round_no < self.max_rounds:
            round_no += 1
            prompt = self._prompt(result)
            self.session.echo(f"\n· ronda {round_no}/{self.max_rounds}: "
                              f"{len(result.errors)} error(es) → pidiendo correccion")
            try:
                self.session.turn(prompt)
            except Exception as exc:  # noqa: BLE001
                self.session.echo(f"  ! el agente fallo: {exc}")
                break
            result = verify(ws, stack=self.stack)
            self._record(result, round_no=round_no)
        if result.ok:
            self.session.memory.remember(
                f"Verificacion superada ({result.tool}) tras {round_no} ronda(s) de "
                f"auto-reparacion.", kind="evidence", source="self_heal")
        else:
            self.session.memory.remember(
                f"Verificacion FALLIDA ({result.tool}): {len(result.errors)} error(es) "
                f"tras {round_no} ronda(s). Revisar PROJECT_STATUS.md.",
                kind="risk", source="self_heal")
            if self.session.devtools:
                for f in result.errors[:10]:
                    self.session.memory.add_todo(
                        f"corregir: {f.one_line()}", screen="build")
        return {"ok": result.ok, "rounds": round_no,
                "tool": result.tool,
                "errors": [f.to_dict() for f in result.errors][:20],
                "history": self.history}

    # ------------------------------------------------------------------
    def _record(self, result: VerifyResult, *, round_no: int) -> None:
        self.history.append({"round": round_no, **result.to_dict()})
        if self.session.devtools:
            self.session.memory.event("verify", {
                "round": round_no, "ok": result.ok, "tool": result.tool,
                "errors": len(result.errors), "warnings": len(result.warnings),
            })

    def _prompt(self, result: VerifyResult) -> str:
        lines = [
            f"La verificacion del proyecto FALLO ({result.tool}). "
            f"Corrige los errores y no cambies el diseno ni la funcionalidad.",
            "",
            f"Errores ({len(result.errors)}):",
        ]
        for f in result.errors[:20]:
            lines.append(f"- {f.one_line()}")
        if result.warnings:
            lines += ["", f"Avisos ({len(result.warnings)}), opcionales:"]
            for f in result.warnings[:10]:
                lines.append(f"- {f.one_line()}")
        if result.notes:
            lines += ["", "Notas:"] + [f"- {n}" for n in result.notes]
        lines += [
            "",
            "Instrucciones:",
            "1. Lee el archivo implicado (read_file) antes de editar.",
            "2. Corrige la causa, no silencies el error.",
            "3. Registra con `remember` cualquier decision no obvia.",
            "4. No introduzcas recursos ni codigo del APK original.",
        ]
        return "\n".join(lines)


def save_report(root: Path, result: VerifyResult, history: list[dict[str, Any]] | None = None) -> Path:
    p = Path(root) / ".rea" / "verify.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"latest": result.to_dict(), "history": history or []},
                            indent=2, ensure_ascii=False), encoding="utf-8")
    return p
