"""Pruebas del bucle de agente con un proveedor OpenAI-compatible simulado."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

from rea_termux.agent import Agent
from rea_termux.config import Config, ProviderCfg
from rea_termux.mcp_client import MCPClient
from rea_termux.providers import Router

HERE = Path(__file__).parent


def make_router(script: list[dict], calls: list[dict]) -> Router:
    """Router con httpx.MockTransport que devuelve respuestas en orden."""
    state = {"i": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body)
        i = min(state["i"], len(script) - 1)
        state["i"] += 1
        return httpx.Response(200, json=script[i])

    p = ProviderCfg(name="fake", base_url="https://fake.test/v1", model="fake-model",
                    api_key_env="FAKE_KEY")
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return Router([p], client=client)


def tool_call_msg(name: str, args: dict, cid: str = "call_1") -> dict:
    return {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": cid,
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(args)},
                }],
            }
        }],
        "model": "fake-model",
        "usage": {"total_tokens": 10},
    }


def text_msg(text: str) -> dict:
    return {"choices": [{"message": {"role": "assistant", "content": text}}],
            "model": "fake-model", "usage": {"total_tokens": 5}}


@pytest.fixture()
def mcp(tmp_path):
    c = MCPClient(command=sys.executable,
                  args=[str(HERE / "fake_mcp_server.py")],
                  startup_timeout_s=15, call_timeout_s=15,
                  stderr_log=tmp_path / "srv.log")
    c.start()
    yield c
    c.close()


@pytest.fixture()
def cfg(monkeypatch, tmp_path):
    monkeypatch.setenv("FAKE_KEY", "sk-test")
    c = Config()
    c.agent.max_turns = 5
    c.workspace = str(tmp_path / "ws")
    return c


def test_agent_executes_tool_then_answers(cfg, mcp, tmp_path):
    calls: list[dict] = []
    router = make_router([
        tool_call_msg("search_android_classes", {"query": "Login"}),
        text_msg("El login está en com.example.LoginActivity.\n\nHUECOS:\n- no verifiqué la red"),
    ], calls)
    out = tmp_path / "analysis"
    out.mkdir()
    agent = Agent(cfg, router, mcp, out_dir=out, echo=lambda _m: None)
    run = agent.run("analiza el login")

    assert run.tool_calls == 1
    assert run.tool_errors == 0
    assert "LoginActivity" in run.final_text
    # el segundo request debe incluir el resultado de la herramienta
    second = calls[1]
    tool_msgs = [m for m in second["messages"] if m["role"] == "tool"]
    assert tool_msgs and "Resultado para Login" in tool_msgs[0]["content"]
    assert second["tools"][0]["function"]["name"]
    assert run.transcript_path and run.transcript_path.is_file()


def test_agent_survives_broken_tool_arguments(cfg, mcp, tmp_path):
    calls: list[dict] = []
    bad = {
        "choices": [{"message": {"role": "assistant", "content": "", "tool_calls": [{
            "id": "c1", "type": "function",
            "function": {"name": "search_android_classes", "arguments": "{no json"},
        }]}}],
        "model": "m",
    }
    router = make_router([bad, text_msg("listo")], calls)
    out = tmp_path / "a"
    out.mkdir()
    agent = Agent(cfg, router, mcp, out_dir=out, echo=lambda _m: None)
    run = agent.run("x")
    assert run.final_text == "listo"
    assert calls[1]["messages"][-1]["role"] == "tool"


def test_agent_stops_on_provider_failure(cfg, mcp, tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": {"message": "boom"}})

    p = ProviderCfg(name="fake", base_url="https://fake.test/v1", model="m",
                    api_key_env="FAKE_KEY", retries=0, timeout_s=2)
    router = Router([p], client=httpx.Client(transport=httpx.MockTransport(handler)))
    out = tmp_path / "a"
    out.mkdir()
    agent = Agent(cfg, router, mcp, out_dir=out, echo=lambda _m: None)
    run = agent.run("x")
    assert run.error and "400" not in run.error  # mensaje viene de AllProvidersFailed
    assert run.tool_calls == 0
