"""Ensamblado de respuestas en streaming (SSE) de la API de OpenAI.

Los deltas llegan troceados: texto por un lado y `tool_calls` por otro, con el
nombre de la funcion en un chunk y los argumentos repartidos en muchos. Este
modulo los reconstruye al mismo formato que una respuesta no-streaming, de modo
que el resto del programa no distingue uno de otro.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator


@dataclass
class StreamAssembler:
    content: list[str] = field(default_factory=list)
    tool_calls: dict[int, dict[str, Any]] = field(default_factory=dict)
    model: str = ""
    usage: dict[str, Any] = field(default_factory=dict)
    finish_reason: str = ""
    chunks: int = 0

    # ------------------------------------------------------------------
    def feed(self, chunk: dict[str, Any]) -> str:
        """Procesa un objeto SSE; devuelve el texto incremental (si lo hay)."""
        if not isinstance(chunk, dict):
            return ""
        self.chunks += 1
        if chunk.get("model"):
            self.model = chunk["model"]
        if chunk.get("usage"):
            self.usage = chunk["usage"]
        out = ""
        for choice in chunk.get("choices") or []:
            if choice.get("finish_reason"):
                self.finish_reason = choice["finish_reason"]
            delta = choice.get("delta") or choice.get("message") or {}
            if isinstance(delta.get("content"), str):
                out += delta["content"]
            for tc in delta.get("tool_calls") or []:
                self._absorb_tool_call(tc)
        if out:
            self.content.append(out)
        return out

    def _absorb_tool_call(self, tc: dict[str, Any]) -> None:
        idx = tc.get("index")
        if idx is None:
            idx = len(self.tool_calls)
        slot = self.tool_calls.setdefault(int(idx), {
            "id": "", "type": "function",
            "function": {"name": "", "arguments": ""},
        })
        if tc.get("id"):
            slot["id"] = tc["id"]
        if tc.get("type"):
            slot["type"] = tc["type"]
        fn = tc.get("function") or {}
        if fn.get("name"):
            slot["function"]["name"] = fn["name"]
        if fn.get("arguments"):
            slot["function"]["arguments"] += fn["arguments"]

    # ------------------------------------------------------------------
    @property
    def text(self) -> str:
        return "".join(self.content)

    def message(self) -> dict[str, Any]:
        msg: dict[str, Any] = {"role": "assistant", "content": self.text}
        calls = [self.tool_calls[k] for k in sorted(self.tool_calls)]
        calls = [c for c in calls if c["function"]["name"]]
        if calls:
            for i, c in enumerate(calls):
                c.setdefault("id", "")
                if not c["id"]:
                    c["id"] = f"call_{i}"
            msg["tool_calls"] = calls
        return msg


def parse_sse(lines: Iterator[str]) -> Iterator[dict[str, Any]]:
    """Convierte lineas `data: {...}` en objetos; ignora comentarios y [DONE]."""
    for line in lines:
        line = line.strip()
        if not line or line.startswith(":"):
            continue
        if line.startswith("data:"):
            line = line[5:].strip()
        if line in ("[DONE]", "DONE"):
            return
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("error"):
            raise RuntimeError(str(obj["error"])[:300])
        yield obj
