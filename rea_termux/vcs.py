"""Control de versiones del workspace: red de seguridad del agente.

El agente escribe archivos reales. Este modulo da:

  * `init()`     — repo propio en el workspace (si no hay uno ajeno)
  * `snapshot()` — commit por turno, para poder volver atras
  * `undo()`     — deshace el ultimo cambio del agente
  * `diff()`     — que cambio desde un punto
  * `dry_run`    — el agente propone cambios y se aplican solo si el usuario acepta

Reglas de convivencia: si el workspace ya es un repo git del usuario, NO se
toca salvo que se pida explicitamente (`init(force=False)` lo detecta y avisa).
Todos los comandos usan `-c user.name/user.email` para no depender de la
configuracion global del telefono.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MARKER = ".rea/vcs.json"
DEFAULT_IGNORE = """\
# generado por rea-termux
*.apk
*.aab
*.xapk
*.jar
*.zip
*.tar
*.tar.gz
node_modules/
build/
.gradle/
.dart_tool/
dist/
out/
__pycache__/
.rea/sessions/
.rea/lock
"""
AUTHOR = ("rea-termux", "rea-termux@localhost")
MAX_STAGE_BYTES = 5 * 1024 * 1024


class VCSError(RuntimeError):
    pass


@dataclass
class Commit:
    hash: str
    subject: str
    when: str

    def short(self) -> str:
        return self.hash[:8]


def available() -> bool:
    return bool(shutil.which("git"))


def _git(root: Path, *args: str, timeout: float = 60.0, check: bool = False):
    cmd = ["git", "-C", str(root),
           "-c", f"user.name={AUTHOR[0]}", "-c", f"user.email={AUTHOR[1]}",
           "-c", "commit.gpgsign=false", *args]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace")
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise VCSError(f"git no disponible: {exc}") from exc
    if check and p.returncode != 0:
        raise VCSError(f"git {' '.join(args)} -> rc={p.returncode}: "
                       f"{(p.stderr or p.stdout)[:300]}")
    return p


# --------------------------------------------------------------------- estado
def is_repo(root: Path) -> bool:
    return (root / ".git").exists()


def is_ours(root: Path) -> bool:
    """¿El repo lo creamos nosotros? (marcador en .rea/vcs.json)"""
    marker = root / MARKER
    if not marker.is_file():
        return False
    try:
        return bool(json.loads(marker.read_text(encoding="utf-8")).get("ours"))
    except (OSError, json.JSONDecodeError):
        return False


def _write_marker(root: Path, extra: dict[str, Any] | None = None) -> None:
    p = root / MARKER
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {"ours": True, "created": time.strftime("%Y-%m-%dT%H:%M:%S")}
    data.update(extra or {})
    p.write_text(json.dumps(data, indent=2), encoding="utf-8")


def init(root: Path, *, allow_existing: bool = False) -> dict[str, Any]:
    """Crea el repo (o adopta uno existente si allow_existing)."""
    root = root.resolve()
    if not available():
        return {"ok": False, "reason": "git no instalado (Termux: pkg install git)"}

    if is_repo(root):
        if is_ours(root):
            return {"ok": True, "reason": "ya inicializado", "ours": True}
        if not allow_existing:
            return {"ok": False, "ours": False,
                    "reason": "el workspace ya es un repositorio git tuyo; "
                              "usa --adopt para que rea-termux haga commits en el"}
        _write_marker(root, {"adopted": True})
        return {"ok": True, "reason": "repo existente adoptado", "ours": False}

    _git(root, "init", "-q", check=True)
    ignore = root / ".gitignore"
    if not ignore.is_file():
        ignore.write_text(DEFAULT_IGNORE, encoding="utf-8")
    _write_marker(root)
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "rea-termux: estado inicial", "--allow-empty")
    return {"ok": True, "reason": "inicializado", "ours": True}


def status_porcelain(root: Path) -> list[str]:
    p = _git(root, "status", "--porcelain")
    return [l for l in p.stdout.splitlines() if l.strip()]


def has_changes(root: Path) -> bool:
    return bool(status_porcelain(root))


def _too_big(root: Path) -> list[str]:
    big = []
    for line in status_porcelain(root):
        path = line[3:].strip()
        f = root / path
        try:
            if f.is_file() and f.stat().st_size > MAX_STAGE_BYTES:
                big.append(path)
        except OSError:
            continue
    return big


def snapshot(root: Path, message: str, *, body: str = "",
             allow_empty: bool = False) -> Commit | None:
    """Commit de todo el workspace. Devuelve None si no habia nada que guardar."""
    root = root.resolve()
    if not is_repo(root):
        return None
    big = _too_big(root)
    _git(root, "add", "-A")
    for path in big:
        # nunca versionamos binarios enormes: los dejamos fuera del commit
        _git(root, "reset", "-q", "--", path)
    if not has_changes(root) and not allow_empty:
        return None
    msg = message.strip().replace("\n", " ")[:160] or "snapshot"
    args = ["commit", "-q", "-m", msg]
    if body.strip():
        args += ["-m", body.strip()[:2000]]
    res = _git(root, *args)
    if res.returncode != 0:
        return None
    h = _git(root, "rev-parse", "HEAD").stdout.strip()
    when = _git(root, "log", "-1", "--format=%cI").stdout.strip()
    return Commit(hash=h, subject=msg, when=when)


def log(root: Path, limit: int = 15) -> list[Commit]:
    if not is_repo(root):
        return []
    p = _git(root, "log", f"-{limit}", "--format=%H%x1f%s%x1f%cI")
    out: list[Commit] = []
    for line in p.stdout.splitlines():
        parts = line.split("\x1f")
        if len(parts) == 3:
            out.append(Commit(hash=parts[0], subject=parts[1], when=parts[2]))
    return out


def head(root: Path) -> str | None:
    if not is_repo(root):
        return None
    p = _git(root, "rev-parse", "HEAD")
    return p.stdout.strip() or None


def diff(root: Path, *, since: str | None = None, paths: list[str] | None = None,
         stat: bool = False, worktree: bool = False) -> str:
    if not is_repo(root):
        return ""
    args = ["diff"]
    if stat:
        args.append("--stat")
    if worktree:
        pass
    elif since:
        args.append(f"{since}..HEAD")
    else:
        args.append("HEAD")
    if paths:
        args += ["--", *paths]
    return _git(root, *args).stdout


def diff_text(root: Path, rel: str, before: str, after: str) -> str:
    """Diff unificado para el modo dry-run (sin tocar el disco dos veces)."""
    import difflib

    b = before.splitlines(keepends=True)
    a = after.splitlines(keepends=True)
    return "".join(difflib.unified_diff(b, a, fromfile=f"a/{rel}", tofile=f"b/{rel}",
                                        n=3))


def undo(root: Path, *, steps: int = 1) -> dict[str, Any]:
    """Deshace los ultimos N commits nuestros (soft reset + descarte de cambios).

    Solo opera si el repo lo creamos nosotros: no toca historia ajena.
    """
    root = root.resolve()
    if not is_repo(root):
        return {"ok": False, "reason": "sin repositorio"}
    if not is_ours(root):
        return {"ok": False, "reason": "el repositorio no lo creo rea-termux"}
    commits = log(root, limit=steps + 1)
    if not commits:
        return {"ok": False, "reason": "sin commits"}
    if len(commits) <= steps:
        # volver al primer commit: dejar el arbol como estaba entonces
        first = commits[-1]
        _git(root, "reset", "-q", "--hard", first.hash)
        return {"ok": True, "to": first.short(), "what": first.subject}
    target = commits[steps]
    _git(root, "reset", "-q", "--hard", target.hash)
    return {"ok": True, "to": target.short(), "what": target.subject,
            "undone": [c.subject for c in commits[:steps]]}


def restore(root: Path, commit: str, paths: list[str] | None = None) -> dict[str, Any]:
    root = root.resolve()
    if not is_repo(root):
        return {"ok": False, "reason": "sin repositorio"}
    args = ["checkout", commit, "--"] + (paths or ["."])
    p = _git(root, *args)
    ok = p.returncode == 0
    return {"ok": ok, "detail": (p.stderr or p.stdout).strip()[:300]}


def files_changed_since(root: Path, since: str | None) -> list[str]:
    if not is_repo(root):
        return []
    rng = f"{since}..HEAD" if since else "HEAD"
    p = _git(root, "diff", "--name-only", rng)
    return [l for l in p.stdout.splitlines() if l.strip()]


def ensure(root: Path, *, allow_existing: bool = False) -> bool:
    """Azucar: devuelve True si hay repo utilizable en el workspace."""
    r = init(root, allow_existing=allow_existing)
    return bool(r.get("ok"))
