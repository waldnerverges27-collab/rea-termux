"""Genera ANALYSIS.md a partir de los artefactos de REA + notas del agente."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import manifest as manifest_mod

MAX_PERMS = 60


def _walk_strings(node: Any, key_hint: str, out: list[str], depth: int = 0) -> None:
    if depth > 10 or len(out) > 400:
        return
    if isinstance(node, dict):
        for k, v in node.items():
            if isinstance(v, str) and key_hint in k.lower():
                out.append(v)
            _walk_strings(v, key_hint, out, depth + 1)
    elif isinstance(node, list):
        for v in node[:500]:
            _walk_strings(v, key_hint, out, depth + 1)


def _uniq(seq: list[str]) -> list[str]:
    seen, out = set(), []
    for s in seq:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _load(p: Path) -> Any:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


SECRET_KEYS = ("key", "token", "secret", "password", "apikey", "api_key", "client_id")


def _license_findings(data: Any) -> list[str]:
    hits: list[str] = []
    _walk_strings(data, "license", hits)
    return _uniq([h for h in hits if 10 < len(h) < 200])[:20]


def _urls(data: Any) -> list[str]:
    hits: list[str] = []
    _walk_strings(data, "url", hits)
    _walk_strings(data, "endpoint", hits)
    _walk_strings(data, "host", hits)
    return _uniq([h for h in hits if h.startswith("http") or "." in h])[:40]


def _secretish(data: Any) -> list[str]:
    found: list[str] = []
    def rec(node: Any, depth: int = 0) -> None:
        if depth > 10:
            return
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(v, str) and any(s in k.lower() for s in SECRET_KEYS) and len(v) >= 16:
                    found.append(f"{k} = {v[:6]}… ({len(v)} chars)")
                rec(v, depth + 1)
        elif isinstance(node, list):
            for v in node[:300]:
                rec(v, depth + 1)
    rec(data)
    return _uniq(found)[:20]


def normalized(data: Any) -> dict[str, Any]:
    """Extrae `data.normalized_result` del sobre real de REA.

    Forma real (v6.7): {ok, data: {evidence_id, subject, provider,
    normalized_result: {package_name, permissions[], manifest{}, class_count,
    resource_count, signature_verification, ...}, limitations[], confidence}}
    """
    if not isinstance(data, dict):
        return {}
    node = data.get("data") if isinstance(data.get("data"), dict) else data
    norm = node.get("normalized_result")
    return norm if isinstance(norm, dict) else {}


def evidence_meta(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    node = data.get("data") if isinstance(data.get("data"), dict) else data
    return {
        "evidence_id": node.get("evidence_id"),
        "provider": (node.get("provider") or {}).get("name")
        if isinstance(node.get("provider"), dict) else node.get("provider"),
        "provider_version": (node.get("provider") or {}).get("version")
        if isinstance(node.get("provider"), dict) else None,
        "confidence": node.get("confidence"),
        "authority": node.get("authority"),
        "limitations": node.get("limitations") or [],
        "duration": (data.get("meta") or {}).get("duration") if isinstance(
            data.get("meta"), dict) else None,
    }


def build_analysis(
    out_dir: Path,
    *,
    package: str,
    apk_name: str,
    agent_notes: str = "",
    unknowns: list[str] | None = None,
    artifacts: dict[str, Path] | None = None,
) -> Path:
    artifacts = artifacts or {}
    manifest_path = artifacts.get("inspect")
    manifest = _load(manifest_path) if manifest_path else None
    norm = normalized(manifest)
    emeta = evidence_meta(manifest)
    sig = _load(artifacts["signature"]) if artifacts.get("signature") else None

    pkg_name = norm.get("package_name") or package
    version = " ".join(str(x) for x in (norm.get("version_name"),
                                        norm.get("version_code")) if x)
    # parser real del XML decodificado (mucho mas fiable que buscar claves)
    mxml = manifest_mod.parse_manifest(manifest_mod.manifest_xml_from(norm)) if norm else {}
    msum = manifest_mod.summary(mxml) if mxml else {}

    perms = _uniq([p["name"] for p in (mxml.get("permissions") or [])]
                  + [p["name"] for p in (mxml.get("permissions_sdk23") or [])]
                  or [p.split(".")[-1] for p in (norm.get("permissions") or [])]
                  or [p.split(".")[-1] for p in (_collect(manifest, "permission") or [])])
    acts = _uniq([c["name"] for c in (mxml.get("components", {}).get("activity") or [])])
    services = _uniq([c["name"] for c in (mxml.get("components", {}).get("service") or [])])
    receivers = _uniq([c["name"] for c in (mxml.get("components", {}).get("receiver") or [])])
    providers = _uniq([c["name"] for c in (mxml.get("components", {}).get("provider") or [])])
    exported = [c["name"] for kind in (mxml.get("components") or {}).values()
                for c in kind if str(c.get("exported")).lower() == "true"]
    links = mxml.get("deep_links") or []
    libs = _uniq(_collect(norm.get("manifest") or manifest, "native")
                 + _collect(norm.get("manifest") or manifest, "lib"))
    urls = _urls(norm) if norm else (_urls(manifest) if manifest else [])
    licenses = _license_findings(norm) if norm else []
    secrets = _secretish(mxml.get("application") or {}) if mxml else []
    counts = {k: norm.get(k) for k in ("class_count", "resource_count") if norm.get(k) is not None}

    md: list[str] = []
    md.append(f"# Analisis tecnico — `{pkg_name}`\n")
    md.append(f"- **APK:** `{apk_name}`")
    if version:
        md.append(f"- **Version:** {version} · minSdk {norm.get('min_sdk') or '?'}"
                  f" · targetSdk {norm.get('target_sdk') or '?'}")
    if counts:
        md.append("- **Tamano del codigo:** " + " · ".join(
            f"{k.replace('_count', '')}: {v}" for k, v in counts.items()))
    if emeta.get("evidence_id"):
        md.append(f"- **Evidencia:** `{emeta['evidence_id']}` · proveedor "
                  f"{emeta.get('provider')} {emeta.get('provider_version') or ''}"
                  f" · confianza {emeta.get('confidence') or '?'}"
                  f" · autoridad {emeta.get('authority') or '?'}")
    if artifacts:
        md.append("- **Artefactos:**")
        for k, v in artifacts.items():
            if v:
                md.append(f"  - {k}: `{v.relative_to(out_dir.parent)}`")
    md.append("")

    md.append("## 1. Superficie declarada (manifest)\n")
    if msum:
        md.append(f"- Launcher: {', '.join(f'`{x}`' for x in msum['launcher']) or '—'}")
        md.append(f"- Clase Application: `{msum.get('app_class') or '—'}`")
        md.append(f"- Componentes: {msum['counts']}")
        if exported:
            md.append(f"- **Exportados ({len(exported)})** — superficie de ataque, "
                      f"decide si los necesitas: {', '.join(f'`{x}`' for x in exported[:15])}")
    else:
        md.append(f"- Actividades: {len(acts)}")
        md.append(f"- Servicios: {len(services)}")
        md.append(f"- Receptores: {len(receivers)}")
    md.append(f"- Librerias nativas: {len(libs)}")
    md.append("")
    if perms:
        md.append("### Permisos\n")
        md.extend(f"- `{p}`" for p in perms[:MAX_PERMS])
        if len(perms) > MAX_PERMS:
            md.append(f"- … y {len(perms) - MAX_PERMS} mas")
        risks = manifest_mod.permission_risks(perms)
        if risks:
            md.append("\n**Permisos sensibles** (decide en la entrevista si tu clon los "
                      "necesita):")
            md.extend(f"- {r}" for r in risks)
        md.append("")

    if acts:
        md.append("### Actividades\n")
        md.extend(f"- `{a}`" for a in acts[:40])
        if len(acts) > 40:
            md.append(f"- … y {len(acts) - 40} mas")
        md.append("")
    for label, items in (("Servicios", services), ("Receptores", receivers),
                         ("Providers", providers)):
        if items:
            md.append(f"### {label}\n")
            md.extend(f"- `{x}`" for x in items[:20])
            md.append("")
    if libs:
        md.append("### Librerias nativas presentes\n")
        md.extend(f"- `{l}`" for l in libs[:30])
        md.append("")

    if links:
        md.append("### Deep links / intent filters\n")
        for l in links[:25]:
            md.append(f"- `{l['uri']}` → `{l['component']}` (exported={l['exported']})")
        md.append("")

    md.append("## 2. Endpoints y cadenas relevantes\n")
    if urls:
        md.extend(f"- `{u}`" for u in urls)
    else:
        md.append("_No se detectaron URLs en el JSON de REA; revisar "
                  "`evidence/` y pedir al agente `search` sobre strings._")
    md.append("")

    md.append("## 3. Hallazgos de seguridad (revisar, no copiar)\n")
    if secrets:
        md.append("Posibles secretos embebidos — **se documentan, no se reutilizan**:\n")
        md.extend(f"- `{s}`" for s in secrets)
    else:
        md.append("- Sin cadenas tipo secreto en el JSON de alto nivel.")
    sigv = norm.get("signature_verification")
    if sigv:
        md.append(f"\n### Firma\n")
        md.append(f"- verificacion: `{sigv}`" if isinstance(sigv, str)
                  else f"```json\n{json.dumps(sigv, indent=2, ensure_ascii=False)[:1500]}\n```")
    elif sig:
        md.append("\n### Firma\n")
        md.append(f"```json\n{json.dumps(sig, indent=2, ensure_ascii=False)[:1500]}\n```")
    if licenses:
        md.append("\n### Licencias de terceros detectadas\n")
        md.extend(f"- `{l}`" for l in licenses)
    md.append("")

    md.append("## 4. Notas del agente (evidencia)\n")
    md.append(_demote(agent_notes).strip() or "_El analisis con agente no se ejecuto._")
    md.append("")

    md.append("## 5. Huecos sin resolver\n")
    rea_lims = [str(x) for x in (emeta.get("limitations") or [])]
    unknowns = list(unknowns or []) + [f"(REA) {x}" for x in rea_lims]
    if unknowns:
        md.extend(f"- {u}" for u in unknowns)
    else:
        md.append("- (vacio) confirmar con `audit_residual_unknowns` si existe.")
    md.append("")

    md.append("---\n")
    md.append("_Generado por rea-termux. El analisis describe comportamiento; "
              "el codigo de la reimplementacion se escribe de nuevo._")

    target = out_dir / "ANALYSIS.md"
    target.write_text("\n".join(md), encoding="utf-8")
    return target


def _demote(md_text: str) -> str:
    """Baja un nivel los titulos para que encajen bajo '## 4'."""
    out = []
    for line in md_text.splitlines():
        if line.startswith("## ") and not line.startswith("### "):
            out.append("### " + line[3:])
        elif line.startswith("# "):
            out.append("## " + line[2:])
        else:
            out.append(line)
    return "\n".join(out)


def _collect(node: Any, key_hint: str, depth: int = 0) -> list[str]:
    """Recolecta valores string bajo claves que contengan key_hint."""
    out: list[str] = []
    if depth > 9:
        return out

    def rec(n: Any, d: int) -> None:
        if d > 9 or len(out) > 800:
            return
        if isinstance(n, dict):
            for k, v in n.items():
                kl = k.lower()
                if isinstance(v, str) and key_hint in kl and len(v) < 200:
                    out.append(v)
                elif isinstance(v, list) and key_hint in kl:
                    for item in v[:200]:
                        if isinstance(item, str):
                            out.append(item)
                        elif isinstance(item, dict):
                            nm = item.get("name") or item.get("className")
                            if isinstance(nm, str):
                                out.append(nm)
                rec(v, d + 1)
        elif isinstance(n, list):
            for v in n[:400]:
                rec(v, d + 1)

    rec(node, depth)
    return _uniq(out)


def summary_for_prompt(out_dir: Path, limit: int = 6000) -> str:
    p = out_dir / "ANALYSIS.md"
    if not p.is_file():
        return ""
    txt = p.read_text(encoding="utf-8")
    return txt[:limit]
