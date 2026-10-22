"""Pruebas del cliente MCP contra un servidor falso por stdio."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from rea_termux.mcp_client import MCPClient, MCPError, select_tools

HERE = Path(__file__).parent


@pytest.fixture()
def client(tmp_path):
    c = MCPClient(
        command=sys.executable,
        args=[str(HERE / "fake_mcp_server.py")],
        startup_timeout_s=15,
        call_timeout_s=15,
        stderr_log=tmp_path / "srv.log",
    )
    c.start()
    yield c
    c.close()


def test_initialize_and_server_info(client):
    assert client.server_info["name"] == "fake-rea"
    assert "inspect_android_package" in client.instructions


def test_list_tools(client):
    tools = client.list_tools()
    assert [t["name"] for t in tools] == [
        "inspect_android_package",
        "capture_adb_screen",
        "search_android_classes",
    ]


def test_call_tool_ok(client):
    res = client.call_tool("search_android_classes", {"query": "Login"})
    assert res.text == "Resultado para Login"
    assert not res.is_error


def test_call_tool_preserves_image_blocks(client):
    res = client.call_tool("capture_adb_screen", {"serial": "fake-1"})
    assert res.images and res.images[0]["mimeType"] == "image/png"
    assert "imagen recibida" in res.text


def test_call_tool_truncates(client):
    res = client.call_tool("inspect_android_package", {"path": "/tmp/a.apk"}, max_chars=500)
    assert res.truncated
    assert res.raw_chars > 30000
    assert len(res.text) < 1500
    assert "package: com.example.demo" in res.text
    assert "truncado" in res.text


def test_unknown_tool_raises(client):
    with pytest.raises(MCPError):
        client.call_tool("no_existe", {})


def test_select_tools_keeps_always_include():
    tools = [{"name": "inspect_android_package"}, {"name": "weird_tool"},
             {"name": "search_android_classes"}]
    sel, notes = select_tools(tools, max_tools=1, always_include=["inspect_android_package"])
    names = [t["name"] for t in sel]
    assert "inspect_android_package" in names
    assert len(names) <= 3
    assert notes  # avisa que recorto
