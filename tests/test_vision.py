"""Pruebas de vision multimodal y captura REA con fallback."""
from __future__ import annotations

import base64
import json
import struct
import threading
import zlib
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from rea_termux.config import Config, MCPServerCfg, ProviderCfg
from rea_termux.mcp_client import MCPClient
from rea_termux.providers import Router
from rea_termux.vision import (VISION_SCHEMA, analyze_screenshot, capture_from_rea,
                               image_message)


def png_1x1() -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff))
    raw = b"\x00\x20\x40\x60\xff"
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">2I5B", 1, 1, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


class VisionHandler(BaseHTTPRequestHandler):
    seen: list[dict] = []
    payload: dict = {}

    def log_message(self, *_):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        type(self).seen.append(body)
        raw = json.dumps({"choices": [{"message": {"content": json.dumps(type(self).payload)}}],
                          "model": "vision-model"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


@pytest.fixture()
def server():
    srv = HTTPServer(("127.0.0.1", 0), VisionHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    VisionHandler.seen = []
    VisionHandler.payload = {
        "screen_type": "login",
        "layout_description": "Formulario vertical con cabecera y accion inferior.",
        "primary_action": "Entrar",
        "components": [{"type": "button", "label": "Entrar", "position": "abajo centro",
                        "confidence": 0.94}],
        "navigation": [],
        "accessibility_issues": ["El campo superior no muestra etiqueta"],
        "improvements": ["Aumentar el area tactil"],
        "confidence": 0.91,
    }
    yield srv
    srv.shutdown()


def make_router(server, monkeypatch, vision=True):
    monkeypatch.setenv("VISION_KEY", "test-key")
    p = ProviderCfg(name="multimodal", base_url=f"http://127.0.0.1:{server.server_address[1]}/v1",
                    model="vision-model", api_key_env="VISION_KEY", supports_vision=vision,
                    retries=0)
    return Router([p]), p


def test_image_message_embeds_png_data_uri(tmp_path):
    p = tmp_path / "shot.png"
    p.write_bytes(png_1x1())
    msg = image_message(p, "login")
    content = msg[0]["content"]
    assert content[0]["type"] == "text"
    img = content[1]["image_url"]
    assert img["detail"] == "high"
    assert img["url"].startswith("data:image/png;base64,")
    assert base64.b64decode(img["url"].split(",", 1)[1]) == p.read_bytes()


def test_image_message_rejects_bad_and_oversized_files(tmp_path):
    p = tmp_path / "bad.png"
    p.write_bytes(b"not png")
    with pytest.raises(ValueError, match="limites"):
        image_message(p, "bad")
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * (8 * 1024 * 1024))
    with pytest.raises(ValueError, match="limites"):
        image_message(p, "large")


def test_analyze_screenshot_sends_image_and_validates_schema(server, monkeypatch, tmp_path):
    p = tmp_path / "shot.png"
    p.write_bytes(png_1x1())
    router, _ = make_router(server, monkeypatch)
    result = analyze_screenshot(router, p, "inicio")
    assert result.data and result.data["screen_type"] == "login"
    assert result.data["confidence"] == 0.91
    body = VisionHandler.seen[0]
    user = next(m for m in body["messages"] if m["role"] == "user")
    assert user["content"][1]["type"] == "image_url"
    assert body["response_format"]["type"] == "json_schema"


def test_vision_provider_flag_is_required(server, monkeypatch, tmp_path):
    p = tmp_path / "shot.png"
    p.write_bytes(png_1x1())
    router, _ = make_router(server, monkeypatch, vision=False)
    result = analyze_screenshot(router, p, "inicio")
    assert result.data is None
    assert "supports_vision = true" in result.errors[0]
    assert VisionHandler.seen == []


def test_rea_capture_image_is_primary(tmp_path):
    fake = Path(__file__).with_name("fake_mcp_server.py")
    client = MCPClient(command=__import__("sys").executable, args=[str(fake)],
                       startup_timeout_s=5, call_timeout_s=5)
    cfg = Config(mcp=MCPServerCfg(command="unused"))
    data, source = capture_from_rea(cfg, serial="fake-1", mcp=client)
    client.close()
    assert data and data.startswith(b"\x89PNG\r\n\x1a\n")
    assert source == "REA: capture_adb_screen"


def test_vision_schema_is_closed():
    assert VISION_SCHEMA["additionalProperties"] is False
