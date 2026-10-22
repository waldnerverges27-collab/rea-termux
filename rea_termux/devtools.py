"""Herramientas de desarrollo que el agente usa junto a las de REA/MCP.

El agente deja de ser solo un analizador: puede leer, escribir y editar los
archivos del proyecto, compilar/ejecutar comandos, capturar la pantalla del
telefono y **recordar** decisiones en la memoria del proyecto.

Todo queda enjaulado dentro del workspace (`_jail`).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .memory import ProjectMemory

MAX_READ = 40_000
MAX_WRITE = 400_000


class ToolError(RuntimeError):
    pass


@dataclass
class DevTools:
    root: Path
    memory: ProjectMemory
    echo: Callable[[str], None] = lambda _m: None
    allow_command: bool = True
    command_timeout: float = 180.0
    auto_track: bool = True
    #: no escribe en disco: acumula propuestas y muestra diffs
    dry_run: bool = False
    #: callback para pedir aprobacion de cada escritura en dry-run
    confirm: Callable[[str, str], bool] | None = None
    #: propuestas pendientes (solo dry_run)
    pending: list[dict[str, Any]] = field(default_factory=list)

    # ------------------------------------------------------------------ jail
    def _resolve(self, path: str) -> Path:
        if not path:
            raise ToolError("ruta vacia")
        p = Path(path).expanduser()
        target = (self.root / p).resolve() if not p.is_absolute() else p.resolve()
        root = self.root.resolve()
        if target != root and root not in target.parents:
            raise ToolError(
                f"ruta fuera del workspace: {path} (permitido: {root})"
            )
        return target

    def _rel(self, p: Path) -> str:
        try:
            return str(p.resolve().relative_to(self.root.resolve()))
        except ValueError:
            return str(p)

    # ---------------------------------------------------------------- specs
    def specs(self) -> list[dict[str, Any]]:
        def fn(name: str, desc: str, props: dict[str, Any], required: list[str]):
            return {"type": "function", "function": {
                "name": name, "description": desc,
                "parameters": {"type": "object", "properties": props,
                               "required": required},
            }}

        S = {"type": "string"}
        return [
            fn("list_files", "Lista archivos del proyecto (globs relativos).",
               {"glob": {**S, "description": "p.ej. 'app/**/*.kt'"},
                "max": {"type": "integer"}}, []),
            fn("read_file", "Lee un archivo del proyecto.",
               {"path": S, "max_chars": {"type": "integer"}}, ["path"]),
            fn("write_file",
               "Crea o sobrescribe un archivo del proyecto. Usa rutas relativas. "
               "Incluye 'summary' con una linea de que hace el archivo.",
               {"path": S, "content": S, "summary": S}, ["path", "content"]),
            fn("edit_file",
               "Reemplaza un fragmento exacto de un archivo (primera coincidencia). "
               "Prefierelo a write_file para cambios pequenos.",
               {"path": S, "old_text": S, "new_text": S, "summary": S},
               ["path", "old_text", "new_text"]),
            fn("run_command",
               "Ejecuta un comando de shell con cwd = workspace (test, gradle, "
               "flutter, npm, adb...). Devuelve stdout/stderr y codigo de salida.",
               {"command": S, "timeout": {"type": "integer"}}, ["command"]),
            fn("remember",
               "Registra en la memoria del proyecto una decision, restriccion, "
               "hallazgo o mejora. Escribelo como frase autonoma y concreta: "
               "sobrevive al olvido del contexto.",
               {"text": S, "kind": {"type": "string",
                                    "enum": ["decision", "constraint", "evidence",
                                             "finding", "improvement", "risk", "note"]}},
               ["text"]),
            fn("add_todo", "Anade una tarea abierta a la memoria.",
               {"text": S, "screen": S}, ["text"]),
            fn("complete_todo", "Marca una tarea como hecha.", {"id": S}, ["id"]),
            fn("define", "Anade un termino del dominio al glosario del proyecto.",
               {"term": S, "definition": S}, ["term", "definition"]),
            fn("project_status",
               "Devuelve el estado completo del proyecto (decisiones, tareas, "
               "archivos). Uselo cuando necesites recordar que se hizo.", {}, []),
            fn("verify_build",
               "Comprueba que el proyecto compila/analiza (o al menos pasa los "
               "chequeos estaticos) y devuelve los errores con archivo:linea. "
               "Usalo despues de escribir codigo.",
               {"stack": {**S, "description": "opcional: flutter | android-kotlin | "
                                              "react-native | web-pwa"}}, []),
            fn("capture_screen",
               "Captura la pantalla del telefono (adb) y su jerarquia de vistas: "
               "guarda PNG + XML + wireframe SVG en ui/ y lo registra en memoria.",
               {"name": {**S, "description": "nombre logico de la pantalla"}}, ["name"]),
        ]

    # -------------------------------------------------------------- dispatch
    def dispatch(self, name: str, args: dict[str, Any]) -> str:
        handler = getattr(self, f"_t_{name}", None)
        if handler is None:
            raise ToolError(f"herramienta desconocida: {name}")
        return handler(**args)

    # ------------------------------------------------------------- archivos
    def _t_list_files(self, glob: str = "**/*", max: int = 200) -> str:
        root = self.root.resolve()
        out: list[str] = []
        for p in sorted(root.glob(glob)):
            if p.is_dir():
                continue
            rel = str(p.relative_to(root))
            if rel.startswith((".rea/", ".git/")) or "/node_modules/" in f"/{rel}/":
                continue
            out.append(rel)
            if len(out) >= max:
                out.append(f"... (limite {max})")
                break
        return "\n".join(out) or "(sin archivos)"

    def _t_read_file(self, path: str, max_chars: int = MAX_READ) -> str:
        p = self._resolve(path)
        if not p.is_file():
            raise ToolError(f"no existe: {path}")
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise ToolError(f"no se pudo leer: {exc}") from exc
        if len(text) > max_chars:
            text = text[:max_chars] + f"\n... (truncado, {len(text)} chars en total)"
        return text

    def _t_write_file(self, path: str, content: str, summary: str = "") -> str:
        if len(content) > MAX_WRITE:
            raise ToolError(f"contenido demasiado grande ({len(content)} > {MAX_WRITE})")
        p = self._resolve(path)
        if self.dry_run:
            return self._propose(p, content, summary, mode="crear/sobrescribir")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        if self.auto_track:
            self.memory.track_file(self._rel(p), summary=summary)
        self.memory.event("write_file", {"path": self._rel(p), "chars": len(content)})
        return f"escrito {self._rel(p)} ({len(content)} chars)"

    def _t_edit_file(self, path: str, old_text: str, new_text: str,
                     summary: str = "") -> str:
        p = self._resolve(path)
        if not p.is_file():
            raise ToolError(f"no existe: {path}")
        text = p.read_text(encoding="utf-8")
        if old_text not in text:
            raise ToolError(
                "el texto a reemplazar no aparece literalmente; lee el archivo con "
                "read_file y copia el fragmento exacto"
            )
        count = text.count(old_text)
        updated = text.replace(old_text, new_text, 1)
        if self.dry_run:
            return self._propose(p, updated, summary, mode="editar",
                                 before=text, count=count)
        p.write_text(updated, encoding="utf-8")
        if self.auto_track:
            self.memory.track_file(self._rel(p), summary=summary)
        self.memory.event("edit_file", {"path": self._rel(p)})
        extra = f" (habia {count} coincidencias, reemplace 1)" if count > 1 else ""
        return f"editado {self._rel(p)}{extra}"

    # --------------------------------------------------------------- dry-run
    def _propose(self, p: Path, new_content: str, summary: str, *, mode: str,
                 before: str | None = None, count: int = 0) -> str:
        from .vcs import diff_text

        rel = self._rel(p)
        old = before if before is not None else (
            p.read_text(encoding="utf-8", errors="replace") if p.is_file() else "")
        diff = diff_text(self.root, rel, old, new_content)
        entry = {"path": rel, "mode": mode, "summary": summary,
                 "content": new_content, "diff": diff}
        self.pending.append(entry)
        self.memory.event("propose", {"path": rel, "mode": mode, "summary": summary})

        if self.confirm is not None and self.confirm(rel, diff):
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(new_content, encoding="utf-8")
            entry["applied"] = True
            if self.auto_track:
                self.memory.track_file(rel, summary=summary)
            head = diff.splitlines()[:60]
            return (f"APLICADO (el usuario lo aprobo): {rel}\n"
                    + "\n".join(head))

        tail = "" if count <= 1 else f" (habia {count} coincidencias)"
        return (f"PROPUESTA (dry-run, no escrito): {rel}{tail}\n"
                f"```diff\n{diff[:3000]}\n```\n"
                f"Para aplicarlo: `rea-termux apply {self._rel(self.root)}` "
                f"o responde 'aplica'.")

    def apply_pending(self) -> list[str]:
        """Escribe en disco todas las propuestas pendientes del dry-run."""
        done: list[str] = []
        for e in self.pending:
            if e.get("applied"):
                continue
            p = self._resolve(e["path"])
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(e["content"], encoding="utf-8")
            e["applied"] = True
            if self.auto_track:
                self.memory.track_file(e["path"], summary=e.get("summary", ""))
            done.append(e["path"])
        self.memory.event("apply_pending", {"files": done})
        return done

    def pending_markdown(self) -> str:
        if not self.pending:
            return "_(sin propuestas pendientes)_"
        out = [f"## Propuestas pendientes ({len(self.pending)})", ""]
        for e in self.pending:
            state = "aplicada" if e.get("applied") else "pendiente"
            out.append(f"### `{e['path']}` ({e['mode']}, {state})")
            if e.get("summary"):
                out.append(f"_{e['summary']}_")
            out.append("```diff\n" + e["diff"][:4000] + "\n```")
            out.append("")
        return "\n".join(out)

    # --------------------------------------------------------------- comandos
    def _t_run_command(self, command: str, timeout: int = 120) -> str:
        if not self.allow_command:
            raise ToolError("run_command deshabilitado por configuracion")
        tmo = min(float(timeout or 120), self.command_timeout)
        try:
            proc = subprocess.run(
                command, shell=True, cwd=str(self.root), capture_output=True,
                text=True, timeout=tmo, encoding="utf-8", errors="replace",
            )
        except subprocess.TimeoutExpired:
            return f"TIMEOUT tras {tmo:.0f}s: {command}"
        out = (proc.stdout or "")[-6000:]
        err = (proc.stderr or "")[-4000:]
        self.memory.event("run_command", {"cmd": command, "rc": proc.returncode})
        parts = [f"$ {command}", f"exit={proc.returncode}"]
        if out:
            parts.append("--- stdout ---\n" + out)
        if err:
            parts.append("--- stderr ---\n" + err)
        return "\n".join(parts)

    # ---------------------------------------------------------------- memoria
    def _t_remember(self, text: str, kind: str = "note") -> str:
        f = self.memory.remember(text, kind=kind, source="agent")
        return f"recordado {f['id']} [{f['kind']}]: {f['text'][:160]}"

    def _t_add_todo(self, text: str, screen: str = "") -> str:
        t = self.memory.add_todo(text, screen=screen)
        return f"tarea {t['id']} anadida: {t['text']}"

    def _t_complete_todo(self, id: str) -> str:
        t = self.memory.complete_todo(id)
        if not t:
            return f"no existe la tarea {id}"
        return f"tarea {id} completada"

    def _t_define(self, term: str, definition: str) -> str:
        self.memory.define(term, definition)
        return f"glosario: {term} = {definition}"

    def _t_project_status(self) -> str:
        return self.memory.status_markdown()

    # ----------------------------------------------------------- verificacion
    def _t_verify_build(self, stack: str = "") -> str:
        from . import verify as verify_mod

        res = verify_mod.verify(self.root, stack=stack or None)
        verify_mod.save_report(self.root, res)
        self.memory.event("verify_build", res.to_dict())
        self.echo(f"  · verificado: {res.summary(limit=5)}")
        return res.summary(limit=25) + "\n\n" + json.dumps(
            res.to_dict(), indent=2, ensure_ascii=False)[:6000]

    # -------------------------------------------------------------- capturas
    def _t_capture_screen(self, name: str) -> str:
        from .uicapture import capture
        res = capture(self.root, name)
        self.memory.event("capture_screen", res)
        if res.get("png"):
            self.memory.track_file(res["png"], role="captura de pantalla",
                                   summary=f"UI original: {name}")
        if res.get("wireframe"):
            self.memory.track_file(res["wireframe"], role="wireframe",
                                   summary=f"wireframe de {name}")
        self.memory.remember(
            f"Pantalla '{name}' capturada ({res.get('nodes', 0)} nodos, "
            f"tamano {res.get('size', '?')}).", kind="evidence", source="capture_screen"
        )
        return json.dumps(res, indent=2, ensure_ascii=False)
