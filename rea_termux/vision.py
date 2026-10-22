"""Analisis multimodal de capturas y captura de pantalla opcional via REA."""
from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

from .config import Config
from .mcp_client import MCPClient
from .providers import Router, StructuredResult

MAX_IMAGE_BYTES = 8 * 1024 * 1024
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

VISION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "screen_type": {"type": "string"},
        "layout_description": {"type": "string"},
        "primary_action": {"type": "string"},
        "components": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string"},
                    "label": {"type": "string"},
                    "position": {"type": "string"},
                    "confidence": {"type": "number"},
                },
                "required": ["type", "label", "position", "confidence"],
                "additionalProperties": False,
            },
        },
        "navigation": {"type": "array", "items": {"type": "string"}},
        "accessibility_issues": {"type": "array", "items": {"type": "string"}},
        "improvements": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "number"},
    },
    "required": ["screen_type", "layout_description", "primary_action", "components",
                 "navigation", "accessibility_issues", "improvements", "confidence"],
    "additionalProperties": False,
}


def image_message(path: str | Path, screen_name: str) -> list[dict[str, Any]]:
    """Crea un mensaje OpenAI multimodal; valida tipo/tamano antes de codificar."""
    p = Path(path)
    size = p.stat().st_size
    if size <= len(PNG_SIGNATURE) or size > MAX_IMAGE_BYTES:
        raise ValueError(f"PNG fuera de limites: {size} bytes (maximo {MAX_IMAGE_BYTES})")
    raw = p.read_bytes()
    if not raw.startswith(PNG_SIGNATURE):
        raise ValueError("la captura no tiene firma PNG valida")
    uri = "data:image/png;base64," + base64.b64encode(raw).decode("ascii")
    return [{
        "role": "user",
        "content": [
            {"type": "text", "text": (
                f"Analiza visualmente esta captura Android llamada '{screen_name}'. "
                "Describe solo lo visible, sin inferir acciones o datos no observables. "
                "Identifica tipo de pantalla, jerarquia, componentes y posicion relativa "
                "(arriba/centro/abajo, izquierda/derecha), accion principal, navegacion, "
                "problemas de accesibilidad y mejoras de UI. No copies marca, textos "
                "distintivos ni recursos; el resultado se usara como evidencia de layout. "
                "En cada componente asigna confidence entre 0 y 1. Devuelve JSON exacto."
            )},
            {"type": "image_url", "image_url": {"url": uri, "detail": "high"}},
        ],
    }]


def analyze_screenshot(router: Router, path: str | Path, screen_name: str) -> StructuredResult:
    """Solo usa proveedores declarados multimodales; no degrada a texto sin imagen."""
    messages = image_message(path, screen_name)
    result = router.structured(messages, VISION_SCHEMA, schema_name="ui_vision",
                               retries=1, only_vision=True)
    if result.data is not None:
        for key in ("confidence",):
            value = result.data.get(key)
            if isinstance(value, (int, float)) and not 0 <= value <= 1:
                result.errors.append(f"$.{key}: debe estar entre 0 y 1")
                result.data = None
                break
        if result.data is not None:
            for i, item in enumerate(result.data.get("components", [])):
                value = item.get("confidence")
                if not isinstance(value, (int, float)) or not 0 <= value <= 1:
                    result.errors.append(f"$.components[{i}].confidence: debe estar entre 0 y 1")
                    result.data = None
                    break
    return result


def _args_for_tool(tool: dict[str, Any], serial: str | None) -> dict[str, Any] | None:
    """Completa unicamente parametros requeridos cuyo significado sea conocido."""
    schema = tool.get("inputSchema") or {}
    props = schema.get("properties") or {}
    args: dict[str, Any] = {}
    for key in schema.get("required") or []:
        prop = props.get(key) or {}
        low = key.lower()
        if any(x in low for x in ("serial", "device")) and serial:
            args[key] = serial
        elif "enum" in prop and prop["enum"]:
            args[key] = prop["enum"][0]
        elif "default" in prop:
            args[key] = prop["default"]
        else:
            return None
    return args


def _image_from_text(text: str) -> bytes | None:
    """Extrae solo una imagen PNG explicitamente codificada en base64."""
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None

    def walk(value: Any) -> bytes | None:
        if isinstance(value, dict):
            for k, v in value.items():
                if k.lower() in {"image_base64", "png_base64", "data"} and isinstance(v, str):
                    candidate = v.split(",", 1)[-1] if v.startswith("data:image/png;base64,") else v
                    if len(candidate) <= 12_000_000:
                        try:
                            raw = base64.b64decode(candidate, validate=True)
                        except (ValueError, base64.binascii.Error):
                            pass
                        else:
                            if raw.startswith(PNG_SIGNATURE):
                                return raw
                found = walk(v)
                if found:
                    return found
        elif isinstance(value, list):
            for item in value:
                found = walk(item)
                if found:
                    return found
        return None
    return walk(data)


def capture_from_rea(cfg: Config, serial: str | None = None,
                      mcp: MCPClient | None = None) -> tuple[bytes | None, str]:
    """Intenta usar capture_adb_screen de REA; la captura adb local es fallback."""
    owned = mcp is None
    mcp = mcp or MCPClient(
        command=cfg.mcp.command, args=cfg.mcp.args, env=cfg.mcp.env, cwd=cfg.mcp.cwd,
        startup_timeout_s=cfg.mcp.startup_timeout_s,
        call_timeout_s=min(cfg.mcp.call_timeout_s, 90.0),
    )
    try:
        tools = mcp.list_tools()
        tool = next((t for t in tools if t.get("name") == "capture_adb_screen"), None)
        if not tool:
            return None, "REA no publica capture_adb_screen"
        args = _args_for_tool(tool, serial)
        if args is None:
            return None, "capture_adb_screen requiere parametros desconocidos; uso adb local"
        result = mcp.call_tool("capture_adb_screen", args, max_chars=2000)
        if result.is_error:
            return None, f"capture_adb_screen fallo: {result.text[:180]}"
        for image in result.images:
            mime = image.get("mimeType", "")
            if mime not in ("image/png", "image/x-png"):
                continue
            try:
                raw = base64.b64decode(image["data"], validate=True)
            except (ValueError, base64.binascii.Error):
                continue
            if raw.startswith(PNG_SIGNATURE) and len(raw) <= MAX_IMAGE_BYTES:
                return raw, "REA: capture_adb_screen"
        raw = _image_from_text(result.text)
        if raw and len(raw) <= MAX_IMAGE_BYTES:
            return raw, "REA: capture_adb_screen"
        return None, "REA no devolvio una imagen PNG; uso adb local"
    except Exception as exc:
        # REA es una mejora opcional: cualquier fallo vuelve a la captura local.
        return None, f"captura REA no disponible ({type(exc).__name__}); uso adb local"
    finally:
        if owned:
            mcp.close()
