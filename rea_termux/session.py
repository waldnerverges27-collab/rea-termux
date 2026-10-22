"""Sesion de desarrollo conversacional y persistente.

Resuelve el "que no se olvide de lo que ha hecho":

  1. La conversacion se guarda en disco (`.rea/sessions/<id>.json`) y se
     reanuda con `rea-termux chat <ws> --session <id>` (o la ultima).
  2. En CADA turno se reconstruye el prompt de sistema con `memory.digest()`:
     decisiones, hallazgos, inventario de archivos, tareas y tokens de UI.
     Aunque el historial se recorte, el estado del proyecto viaja siempre.
  3. El agente tiene herramientas para escribir codigo y para `remember`,
     asi que lo que decide queda en memoria, no solo en el chat.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .agent import SYSTEM_PROMPT as ANALYSIS_PROMPT
from .config import Config
from . import vcs
from .devtools import DevTools, ToolError
from .mcp_client import MCPClient, select_tools
from .memory import ProjectMemory
from .providers import Router, tool_schema_from_mcp

DEV_SYSTEM = """\
Eres el ingeniero que construye, junto al usuario, una aplicacion NUEVA que
reproduce la FUNCIONALIDAD de otra app (que se analizo antes con REA).

## Como trabajas
1. **Memoria primero.** Al empezar cada turno recibes el ESTADO DEL PROYECTO.
   Confia en el, no re-descubras lo ya hecho ni contradigas decisiones previas.
   Si vas a cambiar una decision anterior, dilo explicitamente y registra el
   cambio con `remember`.
2. **Registra lo importante.** Tras cada decision de diseno, arquitectura o
   alcance, llama a `remember` con una frase autonoma ("La pantalla Home usa
   una lista LazyColumn con paginacion de 20"). Anade tareas con `add_todo` y
   cierralas con `complete_todo`. Esto es lo que hara que no olvides nada.
3. **Trabaja sobre archivos reales.** Usa `list_files`/`read_file` antes de
   editar; usa `edit_file` para cambios puntuales y `write_file` para archivos
   nuevos. Tras escribir, si puedes, ejecuta `run_command` para compilar o
   testear, y registra el resultado.
4. **Entrevista cuando falte informacion.** Antes de inventar un flujo o una
   regla de negocio, pregunta al usuario (una o dos preguntas concretas).
5. **Interfaz.** Respeta `DESIGN.md` y los tokens de UI. Reutiliza los
   componentes que ya creaste; no improvises un estilo distinto en cada
   pantalla. Mejora lo que el original hacia mal (accesibilidad, estados
   vacios/carga/error, modo oscuro, objetivos tactiles 48dp).

## Limites (no negociables)
- NO copies codigo, recursos graficos, textos, sonidos, iconos ni la marca del
  original. Todo se escribe de cero con identidad propia.
- NO eludas DRM, paywalls, licencias ni comprobaciones anticopia.
- Si el usuario pide algo que cruce esas lineas, explica la alternativa.

Responde en espanol, breve, y separa "lo que hice" de "lo que sigue".
"""


@dataclass
class TurnRecord:
    at: str
    user: str
    assistant: str
    tools: list[str]
    files: list[str]


@dataclass
class Session:
    cfg: Config
    router: Router
    ws: Path
    memory: ProjectMemory
    session_id: str = ""
    mcp: MCPClient | None = None
    echo: Callable[..., None] = print
    devtools: DevTools | None = None
    messages: list[dict[str, Any]] = field(default_factory=list)
    #: control de versiones del workspace (None si git no esta disponible)
    use_vcs: bool = True
    dry_run: bool = False
    stream: bool = True
    _tools: list[dict[str, Any]] | None = None
    _turn_base: str | None = None

    # ------------------------------------------------------------------
    @property
    def dir(self) -> Path:
        return self.ws / ".rea" / "sessions"

    @property
    def path(self) -> Path:
        return self.dir / f"{self.session_id}.json"

    @classmethod
    def latest_id(cls, ws: Path) -> str | None:
        d = ws / ".rea" / "sessions"
        files = sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime) if d.is_dir() else []
        return files[-1].stem if files else None

    def bind(self) -> "Session":
        if not self.session_id:
            self.session_id = time.strftime("s%Y%m%d-%H%M%S")
        self.dir.mkdir(parents=True, exist_ok=True)
        if self.path.is_file():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                self.messages = data.get("messages", [])
            except (OSError, json.JSONDecodeError):
                self.messages = []
        self.memory.register_session(self.session_id)
        if self.use_vcs:
            res = vcs.init(self.ws)
            if not res.get("ok"):
                self.echo(f"· control de versiones desactivado: {res.get('reason')}")
                self.use_vcs = False
        self.devtools = self.devtools or DevTools(
            self.ws, self.memory, echo=self.echo, dry_run=self.dry_run,
        )
        self.devtools.dry_run = self.dry_run
        return self

    # ------------------------------------------------------------------
    def save(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "id": self.session_id,
            "updated": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "messages": self.messages,
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    # ------------------------------------------------------------------
    def tools(self) -> list[dict[str, Any]]:
        if self._tools is not None:
            return self._tools
        schemas: list[dict[str, Any]] = list(self.devtools.specs()) if self.devtools else []
        if self.mcp is not None:
            try:
                raw = self.mcp.list_tools()
                sel, notes = select_tools(
                    raw,
                    max_tools=max(12, self.cfg.mcp.max_tools - len(schemas)),
                    always_include=self.cfg.mcp.always_include,
                    include_patterns=self.cfg.mcp.include_patterns,
                    exclude_patterns=self.cfg.mcp.exclude_patterns,
                )
                for n in notes:
                    self.echo(f"· {n}")
                schemas += tool_schema_from_mcp(sel)
            except Exception as exc:  # el analisis puede estar ya hecho
                self.echo(f"· MCP no disponible en esta sesion: {exc}")
        self._tools = schemas
        return schemas

    # ------------------------------------------------------------------
    def _system(self, focus: str) -> str:
        base = self.cfg.agent.system_prompt or DEV_SYSTEM
        if not self.cfg.agent.system_prompt:
            base = DEV_SYSTEM + "\n\n---\n\n" + ANALYSIS_PROMPT.split("## Reglas de trabajo")[0]
        parts = [base, self.memory.digest(focus=focus)]
        design = self.ws / "DESIGN.md"
        if design.is_file():
            txt = design.read_text(encoding="utf-8")
            parts.append("## DESIGN.md (extracto)\n" + txt[:2500])
        plan = self.ws / "CLONE_PLAN.md"
        if plan.is_file():
            parts.append("## CLONE_PLAN.md (extracto)\n" + plan.read_text(encoding="utf-8")[:1800])
        return "\n\n".join(parts)

    def _trim(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Conserva el arranque de la sesion y la cola; el resto vive en memoria."""
        keep_tail = 18
        budget = self.cfg.agent.history_char_budget
        if len(messages) <= keep_tail + 4:
            cand = messages
        else:
            cand = messages[:2] + messages[-keep_tail:]
        total = sum(len(json.dumps(m, ensure_ascii=False)) for m in cand)
        if total <= budget:
            return cand
        shrunk = []
        for m in cand:
            if m.get("role") == "tool":
                c = str(m.get("content") or "")
                shrunk.append({**m, "content": f"[omitido {len(c)} chars] " + c[:200]})
            else:
                shrunk.append(m)
        return shrunk

    # ------------------------------------------------------------------
    def turn(self, user_text: str, *, max_turns: int | None = None) -> str:
        if self.devtools is None:
            self.bind()
        assert self.devtools is not None
        self.memory.event("turn", {"session": self.session_id, "user": user_text[:400]})
        self._turn_base = vcs.head(self.ws) if self.use_vcs else None
        history = self._trim(self.messages)
        payload = [{"role": "system", "content": self._system(user_text)}] + history
        payload.append({"role": "user", "content": user_text})
        self.messages.append({"role": "user", "content": user_text})

        schemas = self.tools()
        limit = max_turns or self.cfg.agent.max_turns
        self.router.stream = self.stream
        printed = {"any": False}

        def _delta(chunk: str) -> None:
            printed["any"] = True
            self.echo(chunk, end="")

        limit = max_turns or self.cfg.agent.max_turns
        used_tools: list[str] = []
        touched: list[str] = []
        final = ""

        for _step in range(limit):
            printed["any"] = False
            try:
                res = self.router.chat(
                    payload, schemas or None,
                    on_delta=_delta if self.stream else None,
                )
            finally:
                if printed["any"]:
                    self.echo("")
            content = (res.content or "").strip()
            if content and not printed["any"]:
                self.echo(f"\n{content}")
            self.messages.append({"role": "assistant", "content": res.content or "",
                                  **({"tool_calls": res.tool_calls} if res.tool_calls else {})})
            payload.append(res.message)
            if content:
                final = content
            if not res.tool_calls:
                break

            for call in res.tool_calls:
                f = call.get("function") or {}
                name = f.get("name", "")
                try:
                    args = json.loads(f.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                used_tools.append(name)
                self.echo(f"  -> {name}")
                try:
                    if self.devtools and name in _DEV_TOOL_NAMES:
                        out = self.devtools.dispatch(name, args)
                        if name in ("write_file", "edit_file"):
                            touched.append(str(args.get("path", "")))
                    elif self.mcp is not None:
                        out = self.mcp.call_tool(name, args,
                                                 max_chars=self.cfg.agent.max_tool_chars).text
                    else:
                        out = f"herramienta no disponible: {name}"
                except ToolError as exc:
                    out = f"ERROR: {exc}"
                except Exception as exc:
                    out = f"ERROR: {type(exc).__name__}: {exc}"
                self.messages.append({"role": "tool", "tool_call_id": call.get("id", ""),
                                      "content": out})
                payload.append({"role": "tool", "tool_call_id": call.get("id", ""),
                                "content": out})
                self.echo(f"     ok ({len(out)} chars)")
            self.save()

        self.save()
        # verificacion opcional tras el turno (solo informa, no corrige sola)
        if getattr(self.cfg.agent, "verify_after_turn", False) and touched:
            from . import verify as verify_mod
            res = verify_mod.verify(self.ws)
            verify_mod.save_report(self.ws, res)
            flag = "OK" if res.ok else f"{len(res.errors)} error(es)"
            self.echo(f"· verificacion: {flag} ({res.tool})")
            if not res.ok:
                self.memory.remember(
                    f"Tras el turno quedan errores de verificacion: "
                    f"{res.errors[0].one_line()}", kind="risk", source="verify")

        # propuestas del modo dry-run: quedan en disco para `vcs apply`
        if self.devtools and self.devtools.pending:
            pf = self.ws / ".rea" / "pending.json"
            pf.parent.mkdir(parents=True, exist_ok=True)
            pf.write_text(json.dumps(self.devtools.pending, indent=2,
                                     ensure_ascii=False), encoding="utf-8")

        # commit por turno: red de seguridad y traza de lo que hizo el agente
        if self.use_vcs:
            changed = vcs.status_porcelain(self.ws)
            if changed:
                summary = (final or user_text).strip().splitlines()[0][:120]
                c = vcs.snapshot(
                    self.ws,
                    summary,
                    body=f"pedido: {user_text.strip()[:600]}\n"
                         f"sess: {self.session_id}\n"
                         f"herramientas: {', '.join(dict.fromkeys(used_tools)) or '-'}",
                )
                if c:
                    self.echo(f"· commit {c.short()} ({len(changed)} archivo(s))")
                    self.memory.remember(
                        f"Turno guardado en git ({c.short()}): {summary}",
                        kind="note", source="vcs",
                    )
            elif touched:
                self.echo("· sin cambios nuevos que versionar")

        # resumen determinista del turno en la linea de tiempo de la memoria
        self.memory.data["timeline"].append({
            "t": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "what": f"sess {self.session_id}: {user_text[:80]} → "
                    f"{len(used_tools)} herramientas, {len(set(touched))} archivos",
        })
        for rel in dict.fromkeys(t for t in touched if t):
            try:
                self.memory.track_file(rel)
            except OSError:
                pass
        self.memory.save()
        self.memory.write_status()
        return final or "(sin respuesta)"

    # ------------------------------------------------------------------
    def shell(self) -> None:
        """REPL: conversacion continua en la misma sesion."""
        print(f"\nSesion `{self.session_id}` · escribe 'salir' para terminar.\n")
        while True:
            try:
                text = input("tu> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if text.lower() in ("salir", "exit", "quit", ":q"):
                break
            if not text:
                continue
            if text.startswith("/"):
                self.command(text)
                continue
            try:
                self.turn(text)
            except Exception as exc:
                self.echo(f"[!] {type(exc).__name__}: {exc}")
            print(f"{_dim('memoria:')} {len(self.memory.facts(kind=None))} hechos · "
                  f"{len(self.memory.open_todos())} tareas abiertas · "
                  f"{len(self.memory.inventory())} archivos")
        self.save()
        print(f"sesion guardada en {self.path}\nPara continuar: "
              f"rea-termux chat {self.ws} --session {self.session_id}")


    # ------------------------------------------------------------ comandos
    def command(self, line: str) -> None:
        """/undo, /diff, /log, /status, /apply, /pending, /help"""
        parts = line.strip().split()
        cmd, args = parts[0].lower(), parts[1:]
        if cmd in ("/help", "/?"):
            print("  /undo [n]    deshace los ultimos n turnos (git reset --hard)")
            print("  /diff        cambios sin commitear + ultimo commit")
            print("  /log [n]     ultimos commits del workspace")
            print("  /verify [--fix]  comprueba que compila (y opcionalmente corrige)")
            print("  /pending     propuestas pendientes (modo --dry-run)")
            print("  /apply       aplica las propuestas pendientes")
            print("  /status      estado del proyecto (memoria)")
            print("  salir        termina (la sesion queda guardada)")
            return
        if cmd == "/undo":
            n = int(args[0]) if args and args[0].isdigit() else 1
            res = vcs.undo(self.ws, steps=n)
            if res.get("ok"):
                print(f"  deshecho → {res.get('to')} ({res.get('what')})")
                for w in res.get("undone") or []:
                    print(f"    - {w}")
                self.memory.remember(
                    f"Se deshicieron {n} turno(s) con git hasta {res.get('to')}.",
                    kind="note", source="vcs",
                )
            else:
                print(f"  no se pudo deshacer: {res.get('reason')}")
            return
        if cmd == "/diff":
            d = vcs.diff(self.ws, worktree=True)
            last = vcs.log(self.ws, limit=1)
            if last and len(last) > 1:
                d += vcs.diff(self.ws, since=last[1].hash)
            print(d[:6000] or "  (sin cambios)")
            return
        if cmd == "/log":
            n = int(args[0]) if args and args[0].isdigit() else 10
            for c in vcs.log(self.ws, limit=n):
                print(f"  {c.short()}  {c.when}  {c.subject}")
            return
        if cmd == "/pending":
            print(self.devtools.pending_markdown() if self.devtools else "(sin devtools)")
            return
        if cmd == "/apply":
            done = self.devtools.apply_pending() if self.devtools else []
            print("  aplicado: " + ", ".join(done) if done else "  (nada pendiente)")
            return
        if cmd == "/verify":
            from . import verify as verify_mod
            res = verify_mod.verify(self.ws)
            print("  " + res.summary(limit=20).replace("\n", "\n  "))
            verify_mod.save_report(self.ws, res)
            if res.ok:
                return
            if args and args[0] in ("--fix", "-f"):
                heal = verify_mod.SelfHeal(self, max_rounds=2)
                out = heal.run()
                print(f"  auto-reparacion: ok={out['ok']} rondas={out['rounds']}")
            return
        if cmd == "/status":
            print(self.memory.status_markdown())
            return
        print(f"  comando desconocido: {cmd}  (prueba /help)")

    def undo(self, steps: int = 1) -> dict[str, Any]:
        return vcs.undo(self.ws, steps=steps)

    def diff(self, since: str | None = None) -> str:
        return vcs.diff(self.ws, since=since)


_DEV_TOOL_NAMES = {
    "list_files", "read_file", "write_file", "edit_file", "run_command",
    "remember", "add_todo", "complete_todo", "define", "project_status",
    "capture_screen", "verify_build",
}


def _dim(s: str) -> str:
    return f"\033[2m{s}\033[0m"


def build_session(
    cfg: Config,
    router: Router,
    ws: Path,
    *,
    session_id: str | None = None,
    with_mcp: bool = True,
    echo: Callable[..., None] = print,
) -> Session:
    memory = ProjectMemory.load(ws)
    sid = session_id or Session.latest_id(ws) or ""
    mcp = None
    if with_mcp:
        from .cli import _mcp  # import perezoso: evita ciclo
        mcp = _mcp(cfg, ws / ".rea")
        try:
            mcp.start()
        except Exception as exc:
            echo(f"· MCP no disponible ({exc}); sigo solo con herramientas de archivos")
            mcp = None
    s = Session(cfg=cfg, router=router, ws=ws.resolve(), memory=memory,
                session_id=sid, mcp=mcp, echo=echo)
    return s.bind()
