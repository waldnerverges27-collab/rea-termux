"""Servidor MCP falso por stdio: imita lo justo de REA para probar el cliente."""
from __future__ import annotations

import base64
import json
import sys

TOOLS = [
    {
        "name": "inspect_android_package",
        "description": "Inspecciona un APK: manifest, clases.",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "name": "capture_adb_screen",
        "description": "Captura la pantalla conectada.",
        "inputSchema": {"type": "object", "properties": {"serial": {"type": "string"}}, "required": ["serial"]},
    },
    {
        "name": "search_android_classes",
        "description": "Busca clases por nombre.",
        "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}},
    },
]


def send(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)
        method = msg.get("method")
        rid = msg.get("id")

        if method == "initialize":
            send({"jsonrpc": "2.0", "id": rid, "result": {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "fake-rea", "version": "0.0.1"},
                "instructions": "Usa inspect_android_package primero.",
            }})
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}})
        elif method == "tools/call":
            name = (msg.get("params") or {}).get("name")
            args = (msg.get("params") or {}).get("arguments") or {}
            if name == "inspect_android_package":
                text = "package: com.example.demo\nactivities: MainActivity\n" + ("x" * 30000)
            elif name == "search_android_classes":
                text = "Resultado para " + str(args.get("query"))
            elif name == "capture_adb_screen":
                png = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"fake-image").decode()
                send({"jsonrpc": "2.0", "id": rid, "result": {
                    "content": [{"type": "image", "mimeType": "image/png", "data": png}],
                    "isError": False}})
                continue
            else:
                send({"jsonrpc": "2.0", "id": rid,
                      "error": {"code": -32601, "message": "tool not found"}})
                continue
            send({"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text", "text": text}], "isError": False}})
        elif rid is not None:
            send({"jsonrpc": "2.0", "id": rid,
                  "error": {"code": -32601, "message": "metodo no soportado"}})


if __name__ == "__main__":
    main()
