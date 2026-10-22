"""Cliente para cualquier endpoint compatible con la API de OpenAI.

Objetivo: que el agente no se caiga porque un router devuelva 429, un 5xx,
un timeout o un JSON raro. Se define una cadena de proveedores
(9router, omniroute, ...) y se degrada al siguiente ante errores
recuperables, dejando traza de cada intento.
"""
from __future__ import annotations

import json
import os
import random
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable

try:  # httpx es la unica dependencia externa real
    import httpx
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "Falta httpx. Instala con:  pip install httpx\n(error original: %s)" % exc
    )

from .config import ProviderCfg
from .schemas import coerce as _coerce
from .schemas import extract_json, strictify, validate
from .stream import StreamAssembler, parse_sse

RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504, 520, 521, 522, 524}
#: errores donde vale la pena probar el siguiente proveedor
FATAL_HINTS = (
    "invalid api key",
    "incorrect api key",
    "unauthorized",
    "does not exist",
    "no such model",
    "model_not_found",
)


class ProviderError(RuntimeError):
    def __init__(self, provider: str, message: str, *, status: int | None = None):
        super().__init__(f"[{provider}] {message}")
        self.provider = provider
        self.status = status


class AllProvidersFailed(RuntimeError):
    pass


@dataclass
class ChatResult:
    content: str
    tool_calls: list[dict[str, Any]]
    raw: dict[str, Any]
    provider: str
    model: str
    usage: dict[str, Any]

    @property
    def message(self) -> dict[str, Any]:
        """Mensaje listo para reenviar al modelo (formato OpenAI)."""
        msg: dict[str, Any] = {"role": "assistant", "content": self.content or ""}
        if self.tool_calls:
            msg["tool_calls"] = self.tool_calls
        return msg


def _looks_fatal(msg: str) -> bool:
    low = msg.lower()
    return any(h in low for h in FATAL_HINTS)


class Router:
    """Cadena de proveedores OpenAI-compatibles con fallback."""

    def __init__(
        self,
        providers: Iterable[ProviderCfg],
        *,
        log: Callable[[str], None] | None = None,
        client: httpx.Client | None = None,
    ):
        self.providers = list(providers)
        if not self.providers:
            raise AllProvidersFailed(
                "No hay proveedores configurados. Ejecuta `rea-termux init` y "
                "rellena base_url/model/api_key_env."
            )
        self.log = log or (lambda _m: None)
        self._client = client or httpx.Client(follow_redirects=True)
        self.last_error: str | None = None
        #: el agente lo activa; cada proveedor puede rechazarlo (supports_stream)
        self.stream: bool = False

    # ------------------------------------------------------------------
    def _headers(self, p: ProviderCfg) -> dict[str, str]:
        h = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "rea-termux/0.1 (+termux)",
        }
        key = p.resolve_key()
        if key:
            h["Authorization"] = f"Bearer {key}"
        h.update({str(k): str(v) for k, v in (p.extra_headers or {}).items()})
        return h

    def _payload(
        self,
        p: ProviderCfg,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        *,
        temperature: float | None,
        max_tokens: int | None,
        stream: bool = False,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": p.model,
            "messages": messages,
            "temperature": p.temperature if temperature is None else temperature,
            "max_tokens": p.max_tokens if max_tokens is None else max_tokens,
            "stream": stream,
        }
        if stream:
            # pedimos usage al final (lo soportan OpenAI, OpenRouter, Groq...)
            body["stream_options"] = {"include_usage": True}
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
            body["parallel_tool_calls"] = False
        return body

    # ------------------------------------------------------------------
    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        only_tools_capable: bool = False,
        stream: bool | None = None,
        on_delta: Callable[[str], None] | None = None,
    ) -> ChatResult:
        errors: list[str] = []
        for p in self.providers:
            if only_tools_capable and not p.supports_tools:
                continue
            attempt_tools = bool(tools) and p.supports_tools
            for attempt in range(max(1, p.retries + 1)):
                try:
                    return self._one(p, messages, tools, attempt_tools,
                                     temperature, max_tokens,
                                     stream=self.stream if stream is None else stream,
                                     on_delta=on_delta)
                except ProviderError as exc:
                    errors.append(str(exc))
                    self.log(f"  ! {exc}")
                    retryable = exc.status in RETRYABLE_STATUS or exc.status is None
                    if not retryable or _looks_fatal(str(exc)):
                        break  # siguiente proveedor
                    if attempt < p.retries:
                        delay = min(20.0, (2**attempt) * 1.5) + random.random()
                        time.sleep(delay)
        self.last_error = "\n".join(errors[-6:]) if errors else None
        raise AllProvidersFailed(
            "Todos los proveedores fallaron. Ultimos errores:\n" + (self.last_error or "?")
        )

    # ------------------------------------------------------------------
    def _one(
        self,
        p: ProviderCfg,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        with_tools: bool,
        temperature: float | None,
        max_tokens: int | None,
        *,
        stream: bool = False,
        on_delta: Callable[[str], None] | None = None,
    ) -> ChatResult:
        if not p.base_url or not p.model:
            raise ProviderError(p.name, "base_url o model vacio (config incompleta)")
        if not p.resolve_key():
            raise ProviderError(
                p.name, f"falta la variable de entorno {p.api_key_env or '(sin api_key_env)'}"
            )
        want_stream = stream and p.supports_stream
        body = self._payload(p, messages, tools if with_tools else None,
                             temperature=temperature, max_tokens=max_tokens,
                             stream=want_stream)
        url = p.endpoint()
        if want_stream:
            try:
                return self._stream_one(p, url, body, with_tools, temperature,
                                        max_tokens, on_delta)
            except ProviderError as exc:
                # muchos routers "aceptan" stream y devuelven JSON: reintento normal
                if exc.status in (400, 404, 405, 422, 501) or "stream" in str(exc).lower():
                    self.log(f"  ~ {p.name}: streaming no soportado, modo normal")
                    p.supports_stream = False
                    return self._one(p, messages, tools, with_tools, temperature,
                                     max_tokens, stream=False, on_delta=on_delta)
                raise
        try:
            r = self._client.post(url, headers=self._headers(p), json=body,
                                  timeout=p.timeout_s)
        except httpx.HTTPError as exc:
            raise ProviderError(p.name, f"conexion: {type(exc).__name__}: {exc}") from exc

        if r.status_code >= 400:
            snippet = (r.text or "")[:600].replace("\n", " ")
            # algunos modelos no aceptan `tools`: reintento sin herramientas
            if with_tools and r.status_code in (400, 422) and "tool" in snippet.lower():
                self.log(f"  ~ {p.name}: no acepta tools, reintento en modo texto")
                return self._one(p, messages, None, False, temperature, max_tokens,
                                 stream=stream, on_delta=on_delta)
            raise ProviderError(p.name, f"HTTP {r.status_code}: {snippet}", status=r.status_code)

        try:
            data = r.json()
        except json.JSONDecodeError as exc:
            raise ProviderError(p.name, f"respuesta no-JSON: {r.text[:200]}") from exc

        choices = data.get("choices") or []
        if not choices:
            err = data.get("error") or data
            raise ProviderError(p.name, f"sin choices: {str(err)[:300]}")
        msg = choices[0].get("message") or {}
        content = msg.get("content") or ""
        if isinstance(content, list):  # algunos routers mandan bloques
            content = "".join(
                b.get("text", "") for b in content if isinstance(b, dict)
            )
        return ChatResult(
            content=content,
            tool_calls=msg.get("tool_calls") or [],
            raw=data,
            provider=p.name,
            model=data.get("model") or p.model,
            usage=data.get("usage") or {},
        )

    # ------------------------------------------------------------------
    def _stream_one(
        self,
        p: ProviderCfg,
        url: str,
        body: dict[str, Any],
        with_tools: bool,
        temperature: float | None,
        max_tokens: int | None,
        on_delta: Callable[[str], None] | None,
    ) -> ChatResult:
        """Peticion con SSE: reconstruye texto y tool_calls."""
        asm = StreamAssembler()
        try:
            with self._client.stream("POST", url, headers=self._headers(p),
                                     json=body, timeout=p.timeout_s) as resp:
                if resp.status_code >= 400:
                    raw = resp.read().decode("utf-8", "replace")[:600].replace("\n", " ")
                    raise ProviderError(p.name, f"HTTP {resp.status_code}: {raw}",
                                        status=resp.status_code)
                ctype = (resp.headers.get("content-type") or "").lower()
                if "text/event-stream" not in ctype:
                    # el proveedor ignoro `stream`: leemos JSON normal
                    payload = resp.read()
                    try:
                        data = json.loads(payload)
                    except json.JSONDecodeError as exc:
                        raise ProviderError(p.name, "stream sin SSE y sin JSON") from exc
                    msg = ((data.get("choices") or [{}])[0].get("message") or {})
                    content = msg.get("content") or ""
                    if content and on_delta:
                        on_delta(content)
                    return ChatResult(content=content,
                                      tool_calls=msg.get("tool_calls") or [],
                                      raw=data, provider=p.name,
                                      model=data.get("model") or p.model,
                                      usage=data.get("usage") or {})
                for chunk in parse_sse(resp.iter_lines()):
                    piece = asm.feed(chunk)
                    if piece and on_delta:
                        on_delta(piece)
        except httpx.HTTPError as exc:
            raise ProviderError(p.name, f"stream interrumpido: {type(exc).__name__}: {exc}") from exc
        except RuntimeError as exc:
            raise ProviderError(p.name, f"error en el stream: {exc}") from exc

        if not asm.chunks:
            raise ProviderError(p.name, "stream vacio")
        msg = asm.message()
        return ChatResult(
            content=msg.get("content") or "",
            tool_calls=msg.get("tool_calls") or [],
            raw={"streamed": True, "chunks": asm.chunks, "finish": asm.finish_reason},
            provider=p.name,
            model=asm.model or p.model,
            usage=asm.usage or {},
        )

    # ------------------------------------------------------------------
    def structured(
        self,
        messages: list[dict[str, Any]],
        schema: dict[str, Any],
        *,
        schema_name: str = "resultado",
        retries: int = 2,
        stream: bool = False,
        on_delta: Callable[[str], None] | None = None,
        only_vision: bool = False,
    ) -> StructuredResult:
        """Pide al modelo una salida JSON que valide `schema`, con reintentos.

        Estrategia por proveedor:
          1. `response_format: {type: json_schema, strict}` si esta soportado
          2. `{type: json_object}` si solo soporta eso
          3. instrucciones en el prompt (siempre funciona)
        """
        strict = strictify(schema)
        last_errors: list[str] = []
        last_raw = ""
        attempts = 0
        for p in self.providers:
            if only_vision and not p.supports_vision:
                continue
            attempts += 1
            modes: list[str] = []
            if p.structured_json_schema:
                modes.append("json_schema")
            if p.structured_json_object:
                modes.append("json_object")
            modes.append("prompt")

            convo = messages_for_structured(messages, strict)
            for mode in modes:
                extra: dict[str, Any] = {}
                if mode == "json_schema":
                    extra = {"response_format": {
                        "type": "json_schema",
                        "json_schema": {"name": schema_name, "strict": True,
                                        "schema": strict},
                    }}
                elif mode == "json_object":
                    extra = {"response_format": {"type": "json_object"}}
                try:
                    res = self._one_raw(p, convo, extra, stream=stream, on_delta=on_delta)
                except ProviderError as exc:
                    last_errors = [str(exc)]
                    self.log(f"  ! {exc}")
                    if mode == "json_schema" and exc.status in (400, 404, 422, 500, 501):
                        p.structured_json_schema = False
                        continue
                    if mode == "json_object" and exc.status in (400, 422):
                        p.structured_json_object = False
                        continue
                    break

                data = extract_json(res.content)
                last_raw = res.content
                if data is None:
                    last_errors = ["la respuesta no contiene JSON"]
                else:
                    data = _coerce(data, strict)
                    errs = validate(data, strict)
                    if not errs:
                        return StructuredResult(data, attempts, res.provider,
                                                res.model, [], res.content)
                    last_errors = errs
                # reintento con el error concreto delante
                feedback = ("La respuesta anterior no valido el esquema:\n- "
                            + "\n- ".join(last_errors[:8])
                            + "\nCorrige y devuelve SOLO el JSON completo.")
                convo = convo + [
                    {"role": "assistant", "content": (res.content or "")[:4000]},
                    {"role": "user", "content": feedback},
                ]
                self.log(f"  ~ salida estructurada invalida ({len(last_errors)} error(es)), reintentando")
                if mode == "json_schema":
                    p.structured_json_schema = True
        if only_vision and attempts == 0:
            return StructuredResult(None, 0, "", "", [
                "ningun proveedor tiene supports_vision = true en la configuracion"
            ], "")
        return StructuredResult(None, attempts, self.providers[0].name,
                                self.providers[0].model, last_errors, last_raw)

    def chat_structured(self, messages, schema, **kw) -> StructuredResult:
        return self.structured(messages, schema, **kw)

    # ------------------------------------------------------------------
    def _one_raw(self, p: ProviderCfg, messages: list[dict[str, Any]],
                 payload_extra: dict[str, Any], *, stream: bool = False,
                 on_delta: Callable[[str], None] | None = None) -> ChatResult:
        """Como _one pero aceptando campos extra del cuerpo (response_format)."""
        if not p.base_url or not p.model:
            raise ProviderError(p.name, "base_url o model vacio")
        if not p.resolve_key():
            raise ProviderError(p.name, f"falta ${p.api_key_env}")
        body = self._payload(p, messages, None, temperature=None, max_tokens=None,
                             stream=bool(stream and p.supports_stream))
        body.update(payload_extra)
        url = p.endpoint()
        if body.get("stream"):
            return self._stream_body(p, url, body, on_delta)
        try:
            r = self._client.post(url, headers=self._headers(p), json=body,
                                  timeout=p.timeout_s)
        except httpx.HTTPError as exc:
            raise ProviderError(p.name, f"conexion: {type(exc).__name__}: {exc}") from exc
        if r.status_code >= 400:
            raise ProviderError(p.name, f"HTTP {r.status_code}: "
                                        f"{(r.text or '')[:400]}", status=r.status_code)
        try:
            data = r.json()
        except json.JSONDecodeError as exc:
            raise ProviderError(p.name, "respuesta no-JSON") from exc
        choices = data.get("choices") or []
        if not choices:
            raise ProviderError(p.name, f"sin choices: {str(data)[:200]}")
        msg = choices[0].get("message") or {}
        return ChatResult(content=msg.get("content") or "",
                          tool_calls=msg.get("tool_calls") or [], raw=data,
                          provider=p.name, model=data.get("model") or p.model,
                          usage=data.get("usage") or {})

    def _stream_body(self, p: ProviderCfg, url: str, body: dict[str, Any],
                     on_delta: Callable[[str], None] | None) -> ChatResult:
        asm = StreamAssembler()
        try:
            with self._client.stream("POST", url, headers=self._headers(p),
                                     json=body, timeout=p.timeout_s) as resp:
                if resp.status_code >= 400:
                    raw = resp.read().decode("utf-8", "replace")[:400]
                    raise ProviderError(p.name, f"HTTP {resp.status_code}: {raw}",
                                        status=resp.status_code)
                ctype = (resp.headers.get("content-type") or "").lower()
                if "text/event-stream" not in ctype:
                    data = json.loads(resp.read())
                    msg = ((data.get("choices") or [{}])[0].get("message") or {})
                    return ChatResult(content=msg.get("content") or "", tool_calls=[],
                                      raw=data, provider=p.name,
                                      model=data.get("model") or p.model,
                                      usage=data.get("usage") or {})
                for chunk in parse_sse(resp.iter_lines()):
                    piece = asm.feed(chunk)
                    if piece and on_delta:
                        on_delta(piece)
        except httpx.HTTPError as exc:
            raise ProviderError(p.name, f"stream: {type(exc).__name__}") from exc
        return ChatResult(content=asm.text, tool_calls=asm.message().get("tool_calls") or [],
                          raw={"streamed": True}, provider=p.name,
                          model=asm.model or p.model, usage=asm.usage or {})

    # ------------------------------------------------------------------
    def probe(self, provider: ProviderCfg) -> tuple[bool, str]:
        """Comprueba credenciales/listado de modelos. No gasta tokens de chat."""
        if not provider.base_url:
            return False, "base_url vacio"
        if not provider.resolve_key():
            return False, f"falta ${provider.api_key_env}"
        url = provider.base_url.rstrip("/") + "/models"
        try:
            r = self._client.get(url, headers=self._headers(provider), timeout=30.0)
        except httpx.HTTPError as exc:
            return False, f"conexion: {type(exc).__name__}"
        if r.status_code == 200:
            try:
                ids = [m.get("id") for m in r.json().get("data", [])][:5]
            except Exception:
                return True, "200 (respuesta no estandar)"
            return True, "modelos: " + ", ".join(str(i) for i in ids)
        return False, f"HTTP {r.status_code}: {(r.text or '')[:160]}"

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass


class StructuredResult:
    """Resultado de una llamada con salida JSON validada."""

    def __init__(self, data: Any, attempts: int, provider: str, model: str,
                 errors: list[str] | None = None, raw: str = ""):
        self.data = data
        self.attempts = attempts
        self.provider = provider
        self.model = model
        self.errors = errors or []
        self.raw = raw

    def __repr__(self) -> str:
        return f"StructuredResult(attempts={self.attempts}, errors={len(self.errors)})"


def messages_for_structured(messages: list[dict[str, Any]],
                            schema: dict[str, Any]) -> list[dict[str, Any]]:
    """Refuerza el prompt para proveedores sin `response_format`."""
    import json as _json

    hint = (
        "\n\nRESPONDE SOLO CON JSON que valide exactamente este JSON Schema, "
        "sin texto adicional ni bloques de codigo:\n"
        + _json.dumps(schema, ensure_ascii=False)[:4000]
    )
    out = [dict(m) for m in messages]
    for m in out:
        if m.get("role") == "system":
            m["content"] = (m.get("content") or "") + hint
            return out
    return [{"role": "system", "content": hint.strip()}] + out


def tool_schema_from_mcp(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convierte herramientas MCP al formato `tools` de OpenAI."""
    out: list[dict[str, Any]] = []
    for t in tools:
        name = t.get("name")
        if not name:
            continue
        schema = t.get("inputSchema") or {"type": "object", "properties": {}}
        desc = (t.get("description") or "").strip()
        if len(desc) > 1200:
            desc = desc[:1200] + " [...]"
        out.append(
            {
                "type": "function",
                "function": {"name": name, "description": desc, "parameters": schema},
            }
        )
    return out
