"""Cliente MCP minimo sobre stdio, sin dependencias externas.

Protocolo: JSON-RPC 2.0, un objeto JSON por linea en stdin/stdout del
servidor. Se implementa initialize -> notifications/initialized ->
tools/list (con paginacion) -> tools/call.

Se escribe a mano (en vez de usar el SDK oficial) porque en Termux conviene
cero dependencias pesadas y porque necesitamos control sobre timeouts,
truncado de resultados enormes y registro de stderr.
"""
from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROTOCOL_VERSION = "2025-06-18"
CLIENT_INFO = {"name": "rea-termux", "version": "0.1.0"}


class MCPError(RuntimeError):
    pass


@dataclass
class ToolResult:
    name: str
    text: str
    is_error: bool = False
    structured: Any = None
    truncated: bool = False
    raw_chars: int = 0
    duration_s: float = 0.0
    images: list[dict[str, str]] = field(default_factory=list)


@dataclass
class MCPClient:
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None
    startup_timeout_s: float = 120.0
    call_timeout_s: float = 600.0
    stderr_log: Path | None = None

    _proc: subprocess.Popen | None = None
    _reader: threading.Thread | None = None
    _inflight: dict[int, queue.Queue] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _next_id: int = 1
    _closing: bool = False
    server_info: dict[str, Any] = field(default_factory=dict)
    server_capabilities: dict[str, Any] = field(default_factory=dict)
    instructions: str = ""

    # ------------------------------------------------------------------
    def _which(self) -> str:
        exe = shutil.which(self.command)
        if not exe:
            raise MCPError(
                f"No se encontro '{self.command}' en PATH. "
                f"En Termux: pkg install nodejs npm"
            )
        return exe

    def start(self) -> None:
        if self._proc and self._proc.poll() is None:
            return
        exe = self._which()
        env = os.environ.copy()
        env.update({str(k): str(v) for k, v in (self.env or {}).items()})
        env.setdefault("NO_COLOR", "1")
        env.setdefault("FORCE_COLOR", "0")
        self._proc = subprocess.Popen(
            [exe, *self.args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=self.cwd or None,
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        self._reader = threading.Thread(target=self._read_stdout, daemon=True)
        self._reader.start()
        threading.Thread(target=self._drain_stderr, daemon=True).start()
        self._initialize()

    # ------------------------------------------------------------------
    def _read_stdout(self) -> None:
        assert self._proc and self._proc.stdout
        for line in self._proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                self._log(f"[stdout no-JSON] {line[:400]}")
                continue
            if isinstance(msg, dict) and "id" in msg and ("result" in msg or "error" in msg):
                with self._lock:
                    q = self._inflight.get(int(msg["id"]))
                if q:
                    q.put(msg)
            elif isinstance(msg, dict) and "method" in msg and "id" in msg:
                # peticion del servidor hacia el cliente: se declina amablemente
                self._reply_unsupported(msg)
            # notificaciones: se ignoran
        # EOF
        with self._lock:
            for q in self._inflight.values():
                q.put({"error": {"code": -32000, "message": "servidor cerro stdout"}})

    def _reply_unsupported(self, msg: dict[str, Any]) -> None:
        resp = {
            "jsonrpc": "2.0",
            "id": msg.get("id"),
            "error": {"code": -32601, "message": "metodo no soportado por rea-termux"},
        }
        self._send(resp)

    def _drain_stderr(self) -> None:
        assert self._proc and self._proc.stderr
        for line in self._proc.stderr:
            self._log(line.rstrip())

    def _log(self, line: str) -> None:
        if self.stderr_log:
            try:
                with open(self.stderr_log, "a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
            except OSError:
                pass

    def _send(self, obj: dict[str, Any]) -> None:
        if not self._proc or not self._proc.stdin:
            raise MCPError("servidor MCP no iniciado")
        payload = json.dumps(obj, ensure_ascii=False)
        try:
            self._proc.stdin.write(payload + "\n")
            self._proc.stdin.flush()
        except (BrokenPipeError, ValueError) as exc:
            raise MCPError(f"no se pudo escribir al servidor MCP: {exc}") from exc

    # ------------------------------------------------------------------
    def request(self, method: str, params: dict[str, Any] | None = None,
                timeout: float | None = None) -> Any:
        if not self._proc:
            self.start()
        with self._lock:
            rid = self._next_id
            self._next_id += 1
            q: queue.Queue = queue.Queue()
            self._inflight[rid] = q
        self._send({"jsonrpc": "2.0", "id": rid, "method": method,
                    "params": params or {}})
        tmo = self.call_timeout_s if timeout is None else timeout
        try:
            msg = q.get(timeout=tmo)
        except queue.Empty:
            raise MCPError(f"timeout de {tmo}s esperando respuesta a {method}") from None
        finally:
            with self._lock:
                self._inflight.pop(rid, None)
        if "error" in msg:
            err = msg["error"] or {}
            raise MCPError(f"{method}: {err.get('message', err)}")
        return msg.get("result")

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    # ------------------------------------------------------------------
    def _initialize(self) -> None:
        res = self.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"roots": {"listChanged": False}},
                "clientInfo": CLIENT_INFO,
            },
            timeout=self.startup_timeout_s,
        )
        self.server_info = (res or {}).get("serverInfo") or {}
        self.server_capabilities = (res or {}).get("capabilities") or {}
        self.instructions = (res or {}).get("instructions") or ""
        self.notify("notifications/initialized")

    def list_tools(self) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        cursor: str | None = None
        for _ in range(50):
            params = {"cursor": cursor} if cursor else {}
            res = self.request("tools/list", params, timeout=self.startup_timeout_s)
            tools.extend((res or {}).get("tools") or [])
            cursor = (res or {}).get("nextCursor")
            if not cursor:
                break
        return tools

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None,
                  *, max_chars: int = 14000) -> ToolResult:
        t0 = time.time()
        res = self.request(
            "tools/call",
            {"name": name, "arguments": arguments or {}},
            timeout=self.call_timeout_s,
        ) or {}
        text_parts: list[str] = []
        images: list[dict[str, str]] = []
        for block in res.get("content") or []:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    text_parts.append(block.get("text") or "")
                elif block.get("type") == "image":
                    data = block.get("data")
                    mime = block.get("mimeType") or "image/png"
                    if isinstance(data, str) and data and len(data) <= 18_000_000:
                        images.append({"data": data, "mimeType": str(mime)})
                    text_parts.append("[imagen recibida]")
                else:
                    text_parts.append(f"[{block.get('type')} no textual omitido]")
            else:
                text_parts.append(str(block))
        text = "\n".join(p for p in text_parts if p).strip()
        raw_len = len(text)
        truncated = raw_len > max_chars
        if truncated:
            head = text[: int(max_chars * 0.7)]
            tail = text[-int(max_chars * 0.25):]
            text = (
                head
                + f"\n\n[... truncado: {raw_len - len(head) - len(tail)} caracteres "
                f"omitidos de {raw_len}. Usa un filtro mas especifico o pide "
                f"la seccion concreta. ...]\n\n"
                + tail
            )
        return ToolResult(
            name=name,
            text=text or "(sin contenido de texto)",
            is_error=bool(res.get("isError")),
            structured=res.get("structuredContent"),
            truncated=truncated,
            raw_chars=raw_len,
            duration_s=time.time() - t0,
            images=images,
        )

    # ------------------------------------------------------------------
    def alive(self) -> bool:
        return bool(self._proc) and self._proc.poll() is None

    def close(self) -> None:
        self._closing = True
        if not self._proc:
            return
        try:
            if self._proc.stdin:
                self._proc.stdin.close()
        except Exception:
            pass
        try:
            self._proc.terminate()
            self._proc.wait(timeout=8)
        except Exception:
            try:
                self._proc.kill()
            except Exception:
                pass
        self._proc = None

    def __enter__(self) -> "MCPClient":
        self.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


# ----------------------------------------------------------------------
def select_tools(
    tools: list[dict[str, Any]],
    *,
    max_tools: int = 48,
    always_include: list[str] | None = None,
    include_patterns: list[str] | None = None,
    exclude_patterns: list[str] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Recorta el catalogo (REA expone ~200 herramientas) sin perder las clave.

    Devuelve (seleccionadas, notas) donde notas explica que se dejo fuera.
    """
    notes: list[str] = []
    always = [a.lower() for a in (always_include or [])]
    inc = [p.lower() for p in (include_patterns or [])]
    exc = [p.lower() for p in (exclude_patterns or [])]
    by_name = {t.get("name", ""): t for t in tools if t.get("name")}

    picked: dict[str, dict[str, Any]] = {}
    for name, t in by_name.items():
        low = name.lower()
        if any(p in low for p in exc):
            continue
        if any(a in low for a in always) or any(p in low for p in inc):
            picked[name] = t

    if len(picked) < max_tools:
        # rellena con herramientas que parezcan de lectura (inspect/search/trace)
        prefer = ("inspect", "search", "trace", "list", "read", "get", "analyze",
                  "audit", "plan", "investigate", "detect")
        rest = sorted(
            (n for n in by_name if n not in picked and not any(p in n.lower() for p in exc)),
            key=lambda n: (0 if n.lower().startswith(prefer) else 1, len(n), n),
        )
        for n in rest:
            if len(picked) >= max_tools:
                break
            picked[n] = by_name[n]

    if len(picked) < len(by_name):
        notes.append(
            f"catalogo MCP: {len(picked)} de {len(by_name)} herramientas expuestas "
            f"al modelo (max_tools={max_tools}). Ajusta [mcp].include_patterns si "
            f"necesitas otras."
        )
    return list(picked.values()), notes
