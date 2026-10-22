"""Esquemas de salida estructurada (JSON Schema) y extraccion tolerante.

Dos piezas:

1. `jsonschema_lite()` — validador propio (type, properties, required, items,
   enum, additionalProperties, anyOf/oneOf, min/max). Sin dependencias: en
   Termux conviene no arrastrar `jsonschema` + `referencing`.
2. `extract_json()` — saca el objeto JSON de una respuesta del modelo aunque
   venga envuelta en ```json ... ``` o con texto alrededor.

Los esquemas estrictos (`strict: true`) no admiten `additionalProperties` sin
declarar ni propiedades fuera de `required`, asi que `strictify()` los normaliza.
"""
from __future__ import annotations

import json
import re
from typing import Any

# -------------------------------------------------------------------- esquemas
PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "app_name": {"type": "string"},
        "package_id": {"type": "string"},
        "purpose": {"type": "string"},
        "improvements": {"type": "array", "items": {"type": "string"}},
        "screens": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "purpose": {"type": "string"},
                    "components": {"type": "array", "items": {"type": "string"}},
                    "data_needs": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["name", "purpose"],
                "additionalProperties": False,
            },
        },
        "data_model": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "entity": {"type": "string"},
                    "fields": {"type": "array", "items": {"type": "string"}},
                    "source": {"type": "string",
                               "enum": ["original", "nuevo", "no_verificado"]},
                },
                "required": ["entity", "fields"],
                "additionalProperties": False,
            },
        },
        "risks": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["app_name", "package_id", "purpose", "screens"],
    "additionalProperties": False,
}

UNKNOWNS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "unknowns": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string"},
                    "why": {"type": "string"},
                    "next_step": {"type": "string"},
                },
                "required": ["topic", "why"],
                "additionalProperties": False,
            },
        },
        "summary": {"type": "string"},
    },
    "required": ["unknowns"],
    "additionalProperties": False,
}

FLOW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "flows": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "entry": {"type": "string"},
                    "steps": {"type": "array", "items": {"type": "string"}},
                    "data": {"type": "array", "items": {"type": "string"}},
                    "evidence": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["name", "entry", "steps"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["flows"],
    "additionalProperties": False,
}

SCHEMAS: dict[str, dict[str, Any]] = {
    "plan": PLAN_SCHEMA,
    "unknowns": UNKNOWNS_SCHEMA,
    "flows": FLOW_SCHEMA,
}


def get(name: str) -> dict[str, Any]:
    try:
        return SCHEMAS[name]
    except KeyError:
        raise ValueError(f"esquema desconocido: {name} (hay: {', '.join(SCHEMAS)})") from None


# ------------------------------------------------------------------- estricto
def strictify(schema: dict[str, Any]) -> dict[str, Any]:
    """Normaliza un esquema para `strict: true` (OpenAI y compatibles).

    Reglas: `additionalProperties: false` en cada objeto, `required` con TODAS
    las propiedades, y sin `default`/`examples` (varios proveedores los rechazan).
    """
    if not isinstance(schema, dict):
        return schema
    out: dict[str, Any] = {}
    for k, v in schema.items():
        if k in ("default", "examples"):
            continue
        if k == "properties" and isinstance(v, dict):
            out[k] = {name: strictify(sub) for name, sub in v.items()}
        elif k == "items" and isinstance(v, dict):
            out[k] = strictify(v)
        elif k in ("anyOf", "oneOf", "allOf") and isinstance(v, list):
            out[k] = [strictify(x) for x in v]
        else:
            out[k] = v
    if out.get("type") == "object" and "properties" in out:
        out["additionalProperties"] = False
        # `strict: true` exige que TODAS las propiedades esten en required
        out["required"] = list(out["properties"])
    return out


# ------------------------------------------------------------------- validacion
_TYPES = {
    "string": str, "number": (int, float), "integer": int,
    "boolean": bool, "array": list, "object": dict, "null": type(None),
}


def validate(value: Any, schema: dict[str, Any], path: str = "$") -> list[str]:
    """Lista de errores (vacia = valido). Subconjunto util de JSON Schema."""
    errors: list[str] = []
    if not isinstance(schema, dict):
        return errors
    t = schema.get("type")
    if t:
        types = t if isinstance(t, list) else [t]
        ok = False
        for one in types:
            py = _TYPES.get(one)
            if py is None:
                ok = True
                break
            if one == "number" and isinstance(value, bool):
                continue
            if isinstance(value, py):
                ok = True
                break
        if not ok:
            errors.append(f"{path}: se esperaba {t}, llego {type(value).__name__}")
            return errors
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: '{value}' no esta en {schema['enum']}")
    if isinstance(value, dict):
        props = schema.get("properties") or {}
        for req in schema.get("required") or []:
            if req not in value:
                errors.append(f"{path}: falta la clave obligatoria '{req}'")
        for k, sub in props.items():
            if k in value:
                errors += validate(value[k], sub, f"{path}.{k}")
        if schema.get("additionalProperties") is False:
            extra = set(value) - set(props)
            for k in sorted(extra):
                errors.append(f"{path}: clave no permitida '{k}'")
    if isinstance(value, list):
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for i, item in enumerate(value):
                errors += validate(item, item_schema, f"{path}[{i}]")
    if "minItems" in schema and isinstance(value, list) and len(value) < schema["minItems"]:
        errors.append(f"{path}: minimo {schema['minItems']} elemento(s)")
    return errors


def coerce(value: Any, schema: dict[str, Any]) -> Any:
    """Arreglos baratos antes de fallar: 'true' -> true, numeros en texto, etc."""
    if not isinstance(schema, dict):
        return value
    t = schema.get("type")
    if t == "boolean" and isinstance(value, str):
        if value.strip().lower() in ("true", "si", "sí", "yes", "1"):
            return True
        if value.strip().lower() in ("false", "no", "0"):
            return False
    if t == "integer" and isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    if t == "number" and isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            pass
    if t == "string" and isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    if t == "array" and isinstance(value, (str, dict)):
        return [value]
    if t == "object" and isinstance(value, list) and len(value) == 1 and isinstance(value[0], dict):
        return value[0]
    if isinstance(value, dict) and isinstance(schema.get("properties"), dict):
        return {k: coerce(v, schema["properties"].get(k, {})) for k, v in value.items()}
    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        return [coerce(v, schema["items"]) for v in value]
    return value


# ------------------------------------------------------------------ extraccion
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def extract_json(text: str) -> Any:
    """Saca el primer objeto/array JSON de un texto, aunque venga con prosa."""
    if not isinstance(text, str):
        return None
    text = text.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = _FENCE_RE.search(text)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            pass
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        if start < 0:
            continue
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == opener:
                depth += 1
            elif ch == closer:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        break
    return None
