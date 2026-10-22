"""Stub de proveedor compatible con OpenAI para pruebas de integracion.

Devuelve una tool_call en la primera peticion y texto final en la segunda.
Uso: python tests/mock_openai.py 8765
"""
from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

COUNT = {"n": 0}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # silencio
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        COUNT["n"] += 1
        has_tool_result = any(m.get("role") == "tool" for m in body.get("messages", []))

        if not has_tool_result:
            payload = {
                "choices": [{"message": {
                    "role": "assistant",
                    "content": "Reviso el paquete con REA.",
                    "tool_calls": [{
                        "id": "call_abc",
                        "type": "function",
                        "function": {"name": "search_android_classes",
                                     "arguments": json.dumps({"query": "Login"})},
                    }],
                }}],
                "model": body.get("model", "stub"),
            }
        else:
            payload = {
                "choices": [{"message": {
                    "role": "assistant",
                    "content": (
                        "## Mapa de la app\n"
                        "- LoginActivity gestiona el alta.\n"
                        "## Flujos clave\n"
                        "- login -> com.example.demo.LoginActivity.onLogin\n"
                        "## Datos y red\n"
                        "- POST https://api.demo.test/v1/session\n\n"
                        "HUECOS:\n- no verifique el almacenamiento local\n"
                    ),
                }}],
                "model": body.get("model", "stub"),
            }
        raw = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        raw = json.dumps({"data": [{"id": "stub-model"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()
