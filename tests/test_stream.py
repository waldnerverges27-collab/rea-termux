"""Streaming: ensamblado de deltas SSE y Router con servidor real."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx
import pytest

from rea_termux.config import Config, ProviderCfg
from rea_termux.providers import Router
from rea_termux.stream import StreamAssembler, parse_sse


# --------------------------------------------------------------- ensamblado
def test_assembler_text_only():
    a = StreamAssembler()
    for piece in ["Ho", "la, ", "mundo"]:
        a.feed({"choices": [{"delta": {"content": piece}}]})
    assert a.text == "Hola, mundo"
    assert a.message()["content"] == "Hola, mundo"
    assert "tool_calls" not in a.message()


def test_assembler_tool_call_split_across_chunks():
    """Nombre en un chunk y argumentos JSON troceados en cuatro."""
    a = StreamAssembler()
    a.feed({"choices": [{"delta": {"tool_calls": [
        {"index": 0, "id": "call_1", "type": "function",
         "function": {"name": "read_file", "arguments": ""}}]}}]})
    for frag in ['{"pa', 'th": "a', '.kt"}', ""]:
        a.feed({"choices": [{"delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": frag}}]}}]})
    msg = a.message()
    assert len(msg["tool_calls"]) == 1
    call = msg["tool_calls"][0]
    assert call["id"] == "call_1"
    assert call["function"]["name"] == "read_file"
    assert json.loads(call["function"]["arguments"]) == {"path": "a.kt"}


def test_assembler_two_parallel_tool_calls():
    a = StreamAssembler()
    a.feed({"choices": [{"delta": {"tool_calls": [
        {"index": 0, "id": "c0", "function": {"name": "read_file", "arguments": "{}"}},
        {"index": 1, "id": "c1", "function": {"name": "list_files", "arguments": "{}"}},
    ]}}]})
    msg = a.message()
    assert [c["function"]["name"] for c in msg["tool_calls"]] == ["read_file", "list_files"]


def test_assembler_usage_and_finish():
    a = StreamAssembler()
    a.feed({"model": "m1", "choices": [{"delta": {"content": "x"}}]})
    a.feed({"choices": [{"delta": {}, "finish_reason": "stop"}]})
    a.feed({"usage": {"total_tokens": 42}})
    assert a.model == "m1" and a.finish_reason == "stop"
    assert a.usage["total_tokens"] == 42


def test_parse_sse_ignores_noise():
    lines = [
        ": ping",
        "",
        'data: {"choices":[{"delta":{"content":"a"}}]}',
        'data: {"choices":[{"delta":{"content":"b"}}]}',
        "data: [DONE]",
        'data: {"choices":[{"delta":{"content":"no debe llegar"}}]}',
    ]
    got = [c["choices"][0]["delta"]["content"] for c in parse_sse(iter(lines))]
    assert got == ["a", "b"]


def test_parse_sse_raises_on_error_event():
    with pytest.raises(RuntimeError):
        list(parse_sse(iter(['data: {"error": {"message": "boom"}}'])))


# ------------------------------------------------------------ router real SSE
class SSEHandler(BaseHTTPRequestHandler):
    mode = "sse"

    def log_message(self, *a):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        H = SSEHandler
        if H.mode == "json":  # proveedor que ignora stream
            body = json.dumps({"choices": [{"message": {"role": "assistant",
                                                        "content": "sin stream"}}],
                               "model": "m"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        chunks = [
            {"choices": [{"delta": {"content": "Pienso"}}]},
            {"choices": [{"delta": {"content": " y llamo."}}]},
            {"choices": [{"delta": {"tool_calls": [
                {"index": 0, "id": "call_x", "function": {"name": "verify_build",
                                                          "arguments": ""}}]}}]},
            {"choices": [{"delta": {"tool_calls": [
                {"index": 0, "function": {"arguments": '{"stack": "flutter"}'}}]}}]},
            {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
            {"usage": {"total_tokens": 77}},
        ]
        for c in chunks:
            self.wfile.write(f"data: {json.dumps(c)}\n\n".encode())
            self.wfile.flush()
        self.wfile.write(b"data: [DONE]\n\n")


@pytest.fixture()
def server():
    srv = HTTPServer(("127.0.0.1", 0), SSEHandler)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    yield srv
    srv.shutdown()


def make_router(srv, monkeypatch, mode="sse", stream=True):
    monkeypatch.setenv("FAKE_KEY", "sk-x")
    SSEHandler.mode = mode
    port = srv.server_address[1]
    p = ProviderCfg(name="fake", base_url=f"http://127.0.0.1:{port}/v1",
                    model="m", api_key_env="FAKE_KEY")
    r = Router([p])
    r.stream = stream
    return r


def test_router_streams_text_and_tool_calls(server, monkeypatch):
    r = make_router(server, monkeypatch)
    deltas: list[str] = []
    res = r.chat([{"role": "user", "content": "hola"}],
                 on_delta=deltas.append)
    assert "".join(deltas) == "Pienso y llamo."
    assert res.content == "Pienso y llamo."
    assert res.tool_calls[0]["function"]["name"] == "verify_build"
    assert json.loads(res.tool_calls[0]["function"]["arguments"]) == {"stack": "flutter"}
    assert res.usage["total_tokens"] == 77
    # el mensaje reenviable incluye las tool_calls ensambladas
    assert res.message["tool_calls"][0]["id"] == "call_x"


def test_router_survives_provider_without_stream(server, monkeypatch):
    r = make_router(server, monkeypatch, mode="json")
    res = r.chat([{"role": "user", "content": "x"}], on_delta=lambda _d: None)
    assert res.content == "sin stream"


def test_stream_disabled_uses_plain_json(server, monkeypatch):
    r = make_router(server, monkeypatch, mode="json", stream=False)
    res = r.chat([{"role": "user", "content": "x"}])
    assert res.content == "sin stream"
    assert res.tool_calls == []


def test_broken_stream_falls_back_to_plain_request(server, monkeypatch):
    """Si el stream falla (proxy que lo corta, router raro), se degrada solo."""
    runner = make_router(server, monkeypatch, mode="json")
    p = runner.providers[0]
    real_client = httpx.Client()

    def boom(*_a, **_k):
        raise httpx.ConnectError("stream cortado por el proxy")

    monkeypatch.setattr(real_client, "stream", boom, raising=False)
    runner._client = real_client  # noqa: SLF001
    res = runner.chat([{"role": "user", "content": "x"}], on_delta=lambda _d: None)
    assert res.content == "sin stream"      # respondio por la via normal
    assert p.supports_stream is False       # y no lo reintenta en cada turno


def test_provider_without_stream_support_skips_it(server, monkeypatch):
    runner = make_router(server, monkeypatch, mode="json")
    runner.providers[0].supports_stream = False
    called = {"stream": False}

    def should_not_be_called(*_a, **_k):
        called["stream"] = True
        raise AssertionError("no deberia usar streaming")

    monkeypatch.setattr(runner._client, "stream", should_not_be_called, raising=False)
    res = runner.chat([{"role": "user", "content": "x"}], on_delta=lambda _d: None)
    assert res.content == "sin stream"
    assert called["stream"] is False
