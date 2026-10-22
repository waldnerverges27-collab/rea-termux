"""Parser del AndroidManifest.xml ya decodificado por REA/JADX.

`normalized_result.manifest.text` trae el XML en claro. Parsearlo bien da mucho
mas que buscar claves a ciegas: componentes con `exported`, intent-filters,
deep links, features, clase Application y tema. Es la base del "mapa de la app".
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any

ANDROID_NS = "{http://schemas.android.com/apk/res/android}"


def _a(el: ET.Element, name: str, default: str = "") -> str:
    return el.get(ANDROID_NS + name) or el.get(name) or default


def _strip_ns(tag: str) -> str:
    return tag.split("}", 1)[-1]


def manifest_xml_from(normalized: dict[str, Any]) -> str:
    """Saca el XML decodificado de un normalized_result."""
    man = normalized.get("manifest")
    if isinstance(man, dict):
        text = man.get("text")
        if isinstance(text, str):
            return text
    if isinstance(man, str):
        return man
    return ""


def parse_manifest(text: str) -> dict[str, Any]:
    """Devuelve un resumen estructurado del manifest (o {} si no se puede)."""
    if not text or "<manifest" not in text:
        return {}
    start = text.find("<?xml")
    if start < 0:
        start = text.find("<manifest")
    try:
        root = ET.fromstring(text[start:])
    except ET.ParseError:
        # algunos manifiestos traen entidades sin escapar
        cleaned = re.sub(r"&(?!(amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)", "&amp;", text[start:])
        try:
            root = ET.fromstring(cleaned)
        except ET.ParseError:
            return {}

    out: dict[str, Any] = {
        "package": root.get("package") or "",
        "version_name": _a(root, "versionName"),
        "version_code": _a(root, "versionCode"),
        "install_location": _a(root, "installLocation"),
        "permissions": [],
        "permissions_sdk23": [],
        "features": [],
        "components": {"activity": [], "service": [], "receiver": [], "provider": []},
        "deep_links": [],
        "launcher": [],
        "application": {},
        "uses_library": [],
    }

    for el in root.iter():
        tag = _strip_ns(el.tag)
        if tag in ("uses-sdk",):
            out["min_sdk"] = _a(el, "minSdkVersion")
            out["target_sdk"] = _a(el, "targetSdkVersion")
        elif tag == "uses-permission":
            name = _a(el, "name")
            if name:
                entry = {"name": name}
                if _a(el, "maxSdkVersion"):
                    entry["max_sdk"] = _a(el, "maxSdkVersion")
                out["permissions"].append(entry)
        elif tag == "uses-permission-sdk-23":
            name = _a(el, "name")
            if name:
                out["permissions_sdk23"].append({"name": name})
        elif tag == "uses-feature":
            name = _a(el, "name")
            if name:
                out["features"].append({"name": name, "required": _a(el, "required", "true")})
        elif tag == "uses-library":
            out["uses_library"].append({"name": _a(el, "name"),
                                        "required": _a(el, "required", "true")})
        elif tag == "application":
            out["application"] = {
                "name": _a(el, "name"),
                "label": _a(el, "label"),
                "icon": _a(el, "icon"),
                "theme": _a(el, "theme"),
                "allow_backup": _a(el, "allowBackup"),
                "network_security_config": _a(el, "networkSecurityConfig"),
                "hardware_accelerated": _a(el, "hardwareAccelerated"),
            }
        elif tag in ("activity", "activity-alias", "service", "receiver", "provider"):
            kind = "activity" if tag.startswith("activity") else tag
            comp = {
                "name": _a(el, "name"),
                "exported": _a(el, "exported"),
                "enabled": _a(el, "enabled", "true"),
                "permission": _a(el, "permission"),
                "authorities": _a(el, "authorities"),
                "launch_mode": _a(el, "launchMode"),
                "parent": _a(el, "parentActivityName"),
                "actions": [],
                "categories": [],
                "data": [],
            }
            for child in el:
                ctag = _strip_ns(child.tag)
                if ctag != "intent-filter":
                    continue
                for sub in child:
                    stag = _strip_ns(sub.tag)
                    if stag == "action":
                        comp["actions"].append(_a(sub, "name"))
                    elif stag == "category":
                        comp["categories"].append(_a(sub, "name"))
                    elif stag == "data":
                        comp["data"].append({
                            "scheme": _a(sub, "scheme"),
                            "host": _a(sub, "host"),
                            "path": _a(sub, "path") or _a(sub, "pathPrefix"),
                            "mime": _a(sub, "mimeType"),
                        })
            out["components"][kind].append(comp)

            if "android.intent.category.LAUNCHER" in comp["categories"]:
                out["launcher"].append(comp["name"])
            for d in comp["data"]:
                if d.get("scheme") and d.get("host"):
                    out["deep_links"].append({
                        "component": comp["name"],
                        "uri": f"{d['scheme']}://{d['host']}{d.get('path') or ''}",
                        "exported": comp["exported"],
                    })
    return out


def summary(m: dict[str, Any], *, limit: int = 40) -> dict[str, Any]:
    """Version compacta para prompts y para ANALYSIS.md."""
    perms = [p["name"] for p in m.get("permissions", [])]
    components = m.get("components", {})
    exported = [c["name"] for kind in components.values() for c in kind
                if str(c.get("exported")).lower() == "true"]
    return {
        "package": m.get("package"),
        "version": f"{m.get('version_name')} ({m.get('version_code')})".strip(" ()"),
        "sdk": f"min {m.get('min_sdk')} → target {m.get('target_sdk')}",
        "launcher": m.get("launcher", [])[:5],
        "counts": {k: len(v) for k, v in components.items()},
        "permissions": perms[:limit],
        "permissions_total": len(perms),
        "activities": [c["name"] for c in components.get("activity", [])][:limit],
        "services": [c["name"] for c in components.get("service", [])][:limit],
        "receivers": [c["name"] for c in components.get("receiver", [])][:limit],
        "providers": [c["name"] for c in components.get("provider", [])][:limit],
        "exported": exported[:limit],
        "deep_links": m.get("deep_links", [])[:limit],
        "features": [f["name"] for f in m.get("features", [])
                     if f.get("required") != "false"][:limit],
        "app_class": (m.get("application") or {}).get("name"),
    }


def permission_risks(perms: list[str]) -> list[str]:
    """Permisos que suelen implicar funciones que hay que decidir si se clonan."""
    risky = {
        "READ_SMS": "leer SMS", "RECEIVE_SMS": "recibir SMS", "SEND_SMS": "enviar SMS",
        "READ_CONTACTS": "leer contactos", "WRITE_CONTACTS": "modificar contactos",
        "ACCESS_FINE_LOCATION": "ubicacion precisa", "ACCESS_COARSE_LOCATION": "ubicacion aproximada",
        "CAMERA": "camara", "RECORD_AUDIO": "microfono", "READ_CALL_LOG": "registro de llamadas",
        "MANAGE_EXTERNAL_STORAGE": "acceso amplio a archivos",
        "QUERY_ALL_PACKAGES": "listar apps instaladas",
        "REQUEST_INSTALL_PACKAGES": "instalar APKs", "SYSTEM_ALERT_WINDOW": "superponerse",
        "READ_PHONE_STATE": "estado del telefono", "POST_NOTIFICATIONS": "notificaciones",
        "BIND_ACCESSIBILITY_SERVICE": "servicio de accesibilidad",
    }
    out = []
    for p in perms:
        short = p.split(".")[-1]
        if short in risky:
            out.append(f"`{short}` → {risky[short]}")
    return out
