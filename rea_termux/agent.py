"""Bucle de tool-calling: modelo OpenAI-compatible <-> MCP de REA."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .config import Config
from .mcp_client import MCPClient, ToolResult, select_tools
from .providers import ChatResult, Router, tool_schema_from_mcp

SYSTEM_PROMPT = """\
Eres un ingeniero de reimplementacion de software que trabaja dentro de Termux
(Android, arm64). Operas el servidor MCP "REA" (Reverse Engineer Anything), que
te da herramientas para descompilar y analizar aplicaciones.

## Reglas de trabajo
1. EVIDENCIA OBLIGATORIA. Nunca afirmes el comportamiento de una funcion sin
   haberla leido con una herramienta. Cita el paquete.clase.metodo exacto.
2. DE LO GRANDE A LO PEQUEÑO. Primero estructura (manifest, paquetes, entry
   points, dependencias), luego flujos, luego metodos concretos.
3. UNA PREGUNTA POR HERRAMIENTA. Prefiere llamadas pequeñas y especificas antes
   que volcar un arbol entero.
4. SI UNA HERRAMIENTA FALLA, no la repitas igual: cambia de estrategia o de
   parametros, y anota la limitacion.
5. MARCA LO DESCONOCIDO. Mantienes una lista de "huecos" (lo que no pudiste
   verificar) y la actualizas. No la rellenes con suposiciones.
6. IDIOMA: responde en español, conciso, con rutas y nombres de simbolos tal cual.

## Limites
- Solo se reimplementan apps propias o con permiso escrito. Si detectas que el
  paquete es de un tercero/marca, describelo como ANALISIS TECNICO y no generes
  sustitutos funcionales de su logica propietaria.
- No inventes endpoints, claves ni algoritmos: si no esta en la evidencia, dilo.
"""


@dataclass
class Turn:
    role: str
    content: str


@dataclass
class AgentRun:
    objective: str
    turns: int = 0
    tool_calls: int = 0
    tool_errors: int = 0
    provider_used: str = ""
    final_text: str = ""
    transcript_path: Path | None = None
    error: str | None = None


class Agent:
    def __init__(
        self,
        cfg: Config,
        router: Router,
        mcp: MCPClient,
        *,
        out_dir: Path,
        echo: Callable[..., None] = print,
        on_tool: Callable[[str, dict[str, Any]], bool] | None = None,
        stream: bool = False,
    ):
        self.cfg = cfg
        self.router = router
        self.mcp = mcp
        self.out_dir = out_dir
        self.echo = echo
        self.on_tool = on_tool
        self.stream = stream
        if stream:
            self.router.stream = True
        self._tools_cache: list[dict[str, Any]] | None = None
        self.transcript: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    def _log(self, entry: dict[str, Any]) -> None:
        self.transcript.append({"t": time.time(), **entry})

    # ------------------------------------------------------------------
    def tools(self) -> list[dict[str, Any]]:
        if self._tools_cache is None:
            raw = self.mcp.list_tools()
            sel, notes = select_tools(
                raw,
                max_tools=self.cfg.mcp.max_tools,
                always_include=self.cfg.mcp.always_include,
                include_patterns=self.cfg.mcp.include_patterns,
                exclude_patterns=self.cfg.mcp.exclude_patterns,
            )
            for n in notes:
                self.echo(f"· {n}")
            (self.out_dir / "mcp_tools.json").write_text(
                json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            self._tools_cache = sel
        return self._tools_cache

    # ------------------------------------------------------------------
    def _compact(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Recorta historial si excede el presupuesto de caracteres.

        Conserva: mensaje de sistema, primeros 4 mensajes (contexto) y la cola
        reciente; sustituye los tool results viejos por un resumen.
        """
        budget = self.cfg.agent.history_char_budget
        total = sum(len(json.dumps(m, ensure_ascii=False)) for m in messages)
        if total <= budget:
            return messages
        head, tail = messages[:1], messages[-12:]
        middle = messages[1:-12]
        shrunk: list[dict[str, Any]] = []
        for m in middle:
            if m.get("role") == "tool":
                c = m.get("content") or ""
                shrunk.append({**m, "content": f"[resultado previo omitido, {len(c)} chars] "
                                            + c[:300]})
            else:
                shrunk.append(m)
        out = head + shrunk + tail
        # si sigue pasado, descarta el medio entero
        if sum(len(json.dumps(m, ensure_ascii=False)) for m in out) > budget:
            out = head + shrunk[-4:] + tail
        return out

    # ------------------------------------------------------------------
    def run(self, objective: str, *, max_turns: int | None = None,
            context: str = "") -> AgentRun:
        turns_limit = max_turns or self.cfg.agent.max_turns
        system = (self.cfg.agent.system_prompt or SYSTEM_PROMPT)
        if self.mcp.instructions:
            system += (
                "\n\n## Instrucciones publicadas por el servidor MCP\n"
                + self.mcp.instructions[:3000]
            )
        user = objective if not context else f"{objective}\n\n## Contexto / evidencia\n{context}"
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        result = AgentRun(objective=objective)
        schemas = tool_schema_from_mcp(self.tools())
        self.transcript = [{"t": time.time(), "kind": "start", "objective": objective}]

        for turn in range(1, turns_limit + 1):
            result.turns = turn
            messages = self._compact(messages)
            try:
                res: ChatResult = self.router.chat(
                    messages, schemas,
                    on_delta=(lambda d: self.echo(d, end="")) if self.stream else None,
                )
            except Exception as exc:
                result.error = str(exc)
                self.echo(f"\n[!] el modelo fallo: {exc}")
                break
            result.provider_used = f"{res.provider}:{res.model}"
            self._log({"kind": "assistant", "content": res.content,
                       "tool_calls": res.tool_calls, "usage": res.usage})
            if res.content.strip():
                self.echo(f"\n\033[1m[{turn}] {res.provider}\033[0m\n{res.content.strip()}")

            if not res.tool_calls:
                result.final_text = res.content.strip()
                messages.append({"role": "assistant", "content": res.content})
                break

            messages.append(res.message)
            for call in res.tool_calls:
                name = (call.get("function") or {}).get("name", "")
                raw_args = (call.get("function") or {}).get("arguments") or "{}"
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
                except json.JSONDecodeError:
                    args = {}
                    self.echo(f"  ~ argumentos ilegibles para {name}, se usa {{}}")
                if self.on_tool and not self.on_tool(name, args):
                    payload = "El usuario denego la ejecucion de esta herramienta."
                    messages.append({"role": "tool", "tool_call_id": call.get("id", ""),
                                     "content": payload})
                    continue
                self.echo(f"  -> {name}({_short(args)})")
                try:
                    tr: ToolResult = self.mcp.call_tool(
                        name, args, max_chars=self.cfg.agent.max_tool_chars
                    )
                except Exception as exc:
                    result.tool_errors += 1
                    tr = ToolResult(name=name, text=f"ERROR: {exc}", is_error=True)
                result.tool_calls += 1
                if tr.is_error:
                    result.tool_errors += 1
                flag = "!" if tr.is_error else "ok"
                extra = f", truncado de {tr.raw_chars}" if tr.truncated else ""
                self.echo(f"     {flag} {tr.duration_s:.1f}s{extra}")
                self._log({"kind": "tool", "name": name, "args": args,
                           "is_error": tr.is_error, "chars": tr.raw_chars,
                           "text": tr.text[:4000]})
                messages.append(
                    {"role": "tool", "tool_call_id": call.get("id", ""),
                     "content": tr.text}
                )
        else:
            result.final_text = result.final_text or "(limite de turnos alcanzado)"

        self._log({"kind": "end", "final": result.final_text, "error": result.error})
        suffix = time.strftime("%Y%m%d-%H%M%S")
        tp = self.out_dir / f"transcript-{suffix}.json"
        try:
            tp.write_text(json.dumps(self.transcript, indent=2, ensure_ascii=False),
                          encoding="utf-8")
            result.transcript_path = tp
        except OSError:
            pass
        return result


# ------------------------------------------------------- huecos estructurados
def _parse_unknowns(text: str) -> list[str]:
    """Fallback: lee el bloque HUECOS: de la respuesta en prosa."""
    if "HUECOS:" not in text:
        return []
    tail = text.split("HUECOS:", 1)[1]
    out = []
    for line in tail.splitlines():
        line = line.strip().lstrip("-*").strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out[:40]


def extract_unknowns(router: Router, notes: str) -> tuple[list[str], str]:
    """Convierte las notas en huecos validados por esquema.

    Devuelve (huecos, resumen). Si el proveedor no colabora, cae al parseo
    del bloque HUECOS: (comportamiento anterior).
    """
    from .schemas import UNKNOWNS_SCHEMA

    if not (notes or "").strip():
        return [], ""
    messages = [
        {"role": "system", "content": (
            "Extraes los huecos de un analisis tecnico. No inventes: solo lo que "
            "el texto diga que NO se pudo verificar. Se breve y concreto.")},
        {"role": "user", "content": "Notas del analisis:\n\n" + notes[:12000]},
    ]
    try:
        res = router.structured(messages, UNKNOWNS_SCHEMA, schema_name="huecos")
    except Exception:  # noqa: BLE001
        res = None
    if res is not None and getattr(res, "data", None):
        items = [f"{u.get('topic')}: {u.get('why')}"
                 + (f" → {u.get('next_step')}" if u.get("next_step") else "")
                 for u in res.data.get("unknowns", [])]
        if items:
            return items[:40], res.data.get("summary", "")
    return _parse_unknowns(notes), ""


def _short(args: dict[str, Any], n: int = 110) -> str:
    s = json.dumps(args, ensure_ascii=False)
    return s if len(s) <= n else s[: n - 3] + "..."
