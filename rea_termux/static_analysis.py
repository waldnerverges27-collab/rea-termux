"""Analisis estatico local de APKs: strings, endpoints, secretos y licencias.

No ejecuta la app. El escaneo esta limitado por presupuesto de lectura, y nunca
persiste el valor de un posible secreto: guarda solo tipo, ubicacion y longitud.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from . import manifest as manifest_mod

MAX_MEMBER_BYTES = 24 * 1024 * 1024
MAX_TOTAL_BYTES = 160 * 1024 * 1024
MAX_ENTRIES = 12_000
MAX_STRINGS = 240_000
MAX_FINDINGS = 120

URL_RE = re.compile(r"(?i)\b(?:https?|wss?)://[^\s\"'<>\\]{5,}")
DOMAIN_RE = re.compile(
    r"(?i)(?<![@\w/-])(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"(?:com|net|org|io|dev|app|co|edu|gov|info|biz|me|ai|cloud|tech|us|uk|de|fr|es|ru|jp|cn|in|ca|au|xyz|site|online|store|finance)\b"
)
KNOWN_SECRETS = [
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
    ("openai_style_key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b")),
]
CREDENTIAL_RE = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|"
    r"secret|password|passwd|authorization|private[_-]?key)\b\s*[:=]\s*"
    r"[\"']?([A-Za-z0-9_./+=-]{8,})"
)
TOKEN_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9_+/=-]{28,}(?![A-Za-z0-9])")
PEM_RE = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")

SDK_HINTS: dict[str, tuple[str, ...]] = {
    "Firebase": ("com/google/firebase/", "firebase_", "firebaseio.com", "google-services.json"),
    "Google Play services": ("com/google/android/gms/", "play-services-"),
    "OkHttp": ("okhttp3/", "okhttp/"),
    "Retrofit": ("retrofit2/", "retrofit/"),
    "AndroidX Compose": ("androidx/compose/", "androidx.compose."),
    "Flutter": ("flutter_assets/", "io/flutter/", "flutterembedding"),
    "React Native": ("com/facebook/react/", "reactnative", "react-native"),
    "Sentry": ("io/sentry/", "sentry.io"),
    "Crashlytics": ("com/google/firebase/crashlytics/", "firebase-crashlytics"),
    "Google Play Billing": ("com/android/billingclient/", "billingclient"),
    "Gson": ("com/google/gson/", "gson"),
    "RxJava": ("io/reactivex/", "rxjava"),
    "SQLCipher": ("net/sqlcipher/", "sqlcipher"),
}
COPyleft_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("AGPL-3.0-or-later", re.compile(r"GNU AFFERO GENERAL PUBLIC LICENSE", re.I)),
    ("GPL-3.0-or-later", re.compile(r"GNU GENERAL PUBLIC LICENSE.{0,30}(?:VERSION 3|V3)", re.I | re.S)),
    ("GPL-2.0-or-later", re.compile(r"GNU GENERAL PUBLIC LICENSE.{0,30}(?:VERSION 2|V2)", re.I | re.S)),
    ("LGPL-3.0-or-later", re.compile(r"GNU LESSER GENERAL PUBLIC LICENSE.{0,30}(?:VERSION 3|V3)", re.I | re.S)),
    ("LGPL-2.1-or-later", re.compile(r"GNU LESSER GENERAL PUBLIC LICENSE.{0,40}(?:2\.1|VERSION 2)", re.I | re.S)),
    ("MPL-2.0", re.compile(r"MOZILLA PUBLIC LICENSE.{0,20}2\.0", re.I | re.S)),
    ("EPL-2.0", re.compile(r"ECLIPSE PUBLIC LICENSE.{0,20}2\.0", re.I | re.S)),
    ("CDDL-1.0", re.compile(r"COMMON DEVELOPMENT AND DISTRIBUTION LICENSE", re.I)),
]
LICENSE_NAME_RE = re.compile(r"(^|/)(license|licence|copying|notice)(\.|$|[-_])", re.I)


def shannon_entropy(value: str) -> float:
    """Entropia en bits/caracter, sin contar el secreto fuera de memoria."""
    if not value:
        return 0.0
    counts: dict[str, int] = defaultdict(int)
    for char in value:
        counts[char] += 1
    n = len(value)
    return -sum((count / n) * math.log2(count / n) for count in counts.values())


def _strings(data: bytes, limit: int = MAX_STRINGS) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for pattern, encoding in ((rb"[\x20-\x7e]{6,}", "ascii"),
                              (rb"(?:[\x20-\x7e]\x00){6,}", "utf-16le")):
        for match in re.finditer(pattern, data):
            try:
                value = match.group(0).decode(encoding, "ignore").strip()
            except (UnicodeDecodeError, LookupError):
                continue
            if value and value not in seen:
                seen.add(value)
                out.append(value)
                if len(out) >= limit:
                    return out
    return out


def _sanitize_url(raw: str) -> tuple[str, str] | None:
    candidate = raw.rstrip(".,);]}\n\r\t")
    try:
        parts = urlsplit(candidate)
        host = (parts.hostname or "").lower().rstrip(".")
        if not host or "." not in host:
            return None
        scheme = parts.scheme.lower()
        if scheme not in {"http", "https", "ws", "wss"}:
            return None
        # Never retain URL userinfo or query values: either may contain credentials/PII.
        safe_query = [(key, "<redacted>") for key, _ in parse_qsl(parts.query, keep_blank_values=True)]
        path_parts = []
        for segment in parts.path.split("/"):
            known = any(pattern.search(segment) for _kind, pattern in KNOWN_SECRETS)
            if len(segment) >= 24 and (known or shannon_entropy(segment) >= 5.0):
                path_parts.append("<redacted>")
            else:
                path_parts.append(segment)
        safe_path = "/".join(path_parts)
        safe_host = ".".join(
            "<redacted>" if len(label) >= 24 and shannon_entropy(label) >= 5.0 else label
            for label in host.split(".")
        )
        safe = urlunsplit((scheme, safe_host, safe_path, urlencode(safe_query), ""))
        return safe, safe_host
    except (ValueError, UnicodeError):
        return None


def _secret_kind_and_confidence(strings: Iterable[tuple[str, str]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def add(kind: str, value: str, source: str, confidence: str, entropy: float | None = None):
        digest = hashlib.sha256(value.encode("utf-8", "ignore")).hexdigest()
        key = (kind, digest)
        if key in seen:
            return
        seen.add(key)
        finding: dict[str, Any] = {
            "type": kind, "source": source, "confidence": confidence,
            "length": len(value), "value": "<redacted>",
        }
        if entropy is not None:
            finding["entropy_bits_per_char"] = round(entropy, 2)
        results.append(finding)

    for source, text in strings:
        if PEM_RE.search(text):
            add("private_key_pem", "private-key-marker", source, "high")
        for kind, regex in KNOWN_SECRETS:
            for match in regex.finditer(text):
                add(kind, match.group(0), source, "high")
        contextual_spans: set[str] = set()
        for match in CREDENTIAL_RE.finditer(text):
            key, value = match.group(1), match.group(2).strip("'\".,;)")
            if value.lower() in {"null", "none", "undefined", "changeme", "placeholder", "your_api_key"}:
                continue
            contextual_spans.add(value)
            add("credential_assignment:" + key.lower(), value, source, "medium")
        for match in TOKEN_RE.finditer(text):
            value = match.group(0)
            if value in contextual_spans:
                continue
            entropy = shannon_entropy(value)
            if entropy >= 5.0 and len(set(value)) >= 12 and not re.fullmatch(r"[a-fA-F0-9]{32,}", value):
                add("high_entropy_candidate", value, source, "review", entropy)
        if len(results) >= MAX_FINDINGS:
            break
    return results[:MAX_FINDINGS]


def _copyleft_id(text: str) -> str | None:
    for license_id, pattern in COPyleft_RULES:
        if pattern.search(text):
            return license_id
    return None


def _manifest_info(manifest_data: Any) -> dict[str, Any]:
    if not isinstance(manifest_data, dict):
        return {}
    node = manifest_data.get("data") if isinstance(manifest_data.get("data"), dict) else manifest_data
    normalized = node.get("normalized_result") if isinstance(node.get("normalized_result"), dict) else node
    xml_text = manifest_mod.manifest_xml_from(normalized)
    return manifest_mod.parse_manifest(xml_text)


def analyze_apk(apk_path: str | Path, manifest_data: Any = None) -> dict[str, Any]:
    """Escanea un APK sin ejecutarlo y devuelve hallazgos redactados y limitaciones."""
    apk = Path(apk_path)
    report: dict[str, Any] = {
        "schema_version": 1, "apk": apk.name, "package": "", "version": "",
        "manifest_available": False,
        "urls": [], "hosts": [], "deep_links": [], "firebase": [], "permissions": {},
        "sdk_fingerprints": [], "secrets": [], "licenses": [], "license_files": [],
        "scan": {"entries_read": 0, "bytes_read": 0, "truncated_entries": [], "limitations": []},
    }
    parsed_manifest = _manifest_info(manifest_data)
    report["package"] = parsed_manifest.get("package", "")
    report["version"] = " ".join(str(x) for x in (
        parsed_manifest.get("version_name"), parsed_manifest.get("version_code")
    ) if x)
    report["manifest_available"] = bool(parsed_manifest)
    report["deep_links"] = parsed_manifest.get("deep_links", [])
    if not report["manifest_available"]:
        report["scan"]["limitations"].append(
            "No se recibio manifest XML decodificado; deep links y permisos declarados no se pueden comparar."
        )
    report["permissions"] = {
        "declared": sorted({p["name"] for p in parsed_manifest.get("permissions", [])}
                            | {p["name"] for p in parsed_manifest.get("permissions_sdk23", [])}),
        "observed_in_strings": [], "declared_not_observed": [],
        "interpretation": "La ausencia de una referencia textual no prueba que el permiso no se use; puede haber llamadas indirectas/reflexion.",
    }
    if not apk.is_file():
        report["scan"]["limitations"].append("APK inexistente.")
        return report

    entries: list[tuple[str, list[str]]] = []
    license_contents: list[tuple[str, str]] = []
    firebase_configs: list[dict[str, Any]] = []
    total_read = 0
    strings_read = 0
    try:
        with zipfile.ZipFile(apk) as zf:
            infos = zf.infolist()
            if len(infos) > MAX_ENTRIES:
                report["scan"]["limitations"].append(
                    f"El APK contiene {len(infos)} entradas; se analizaron como maximo {MAX_ENTRIES}."
                )
            for info in infos[:MAX_ENTRIES]:
                if info.is_dir():
                    continue
                name = "".join(ch if ch.isprintable() else "?"
                               for ch in info.filename.replace("\\", "/"))[:300]
                low_name = name.lower()
                is_license = bool(LICENSE_NAME_RE.search(low_name))
                is_candidate = (low_name.endswith((".dex", ".xml", ".json", ".txt", ".properties",
                                                   ".html", ".js", ".kt", ".java", ".yaml", ".yml",
                                                   ".plist", ".strings", ".cfg", ".ini", ".so", ".arsc"))
                                or is_license or "google-services.json" in low_name)
                if not is_candidate:
                    continue
                if info.file_size > MAX_MEMBER_BYTES:
                    report["scan"]["truncated_entries"].append(name)
                    continue
                if total_read >= MAX_TOTAL_BYTES:
                    report["scan"]["limitations"].append("Alcanzado el limite total de bytes del escaneo.")
                    break
                try:
                    with zf.open(info) as fh:
                        raw = fh.read(min(MAX_MEMBER_BYTES, MAX_TOTAL_BYTES - total_read) + 1)
                except (OSError, RuntimeError, zipfile.BadZipFile):
                    report["scan"]["limitations"].append(f"No se pudo leer la entrada {name}.")
                    continue
                if len(raw) > MAX_MEMBER_BYTES or len(raw) > MAX_TOTAL_BYTES - total_read:
                    report["scan"]["truncated_entries"].append(name)
                    raw = raw[:max(0, min(MAX_MEMBER_BYTES, MAX_TOTAL_BYTES - total_read))]
                total_read += len(raw)
                report["scan"]["entries_read"] += 1
                strings = _strings(raw, max(0, MAX_STRINGS - strings_read))
                strings_read += len(strings)
                entries.append((name, strings))
                if is_license:
                    decoded = raw[:2_000_000].decode("utf-8", "ignore")
                    license_contents.append((name, decoded))
                if "google-services.json" in low_name or (low_name.endswith(".json") and "firebase" in low_name):
                    try:
                        cfg = json.loads(raw.decode("utf-8", "replace"))
                    except json.JSONDecodeError:
                        cfg = None
                    if isinstance(cfg, dict):
                        project = cfg.get("project_info") or {}
                        apps = cfg.get("client") or []
                        api_count = sum(
                            len(c.get("api_key") or []) if isinstance(c.get("api_key"), list)
                            else int(bool(c.get("api_key")))
                            for c in apps if isinstance(c, dict)
                        )
                        firebase_configs.append({
                            "file": name,
                            "project_id": project.get("project_id"),
                            "project_number_present": bool(project.get("project_number")),
                            "firebase_url_present": bool(project.get("firebase_url")),
                            "app_count": len(apps), "api_key_entries": api_count,
                            "api_keys_redacted": True,
                        })
    except (zipfile.BadZipFile, OSError, ValueError) as exc:
        report["scan"]["limitations"].append(f"No se pudo inspeccionar el contenedor APK ({type(exc).__name__}).")
        return report

    report["scan"]["bytes_read"] = total_read
    if strings_read >= MAX_STRINGS:
        report["scan"]["limitations"].append(f"Alcanzado el limite de {MAX_STRINGS} cadenas.")

    all_strings = [(name, text) for name, values in entries for text in values]
    url_rows: dict[str, set[str]] = defaultdict(set)
    host_rows: dict[str, set[str]] = defaultdict(set)
    for source, text in all_strings:
        url_matches = list(URL_RE.finditer(text))
        for match in url_matches:
            parsed = _sanitize_url(match.group(0))
            if parsed:
                url, host = parsed
                url_rows[url].add(source)
                host_rows[host].add(source)
        for match in DOMAIN_RE.finditer(text):
            if any(url_match.start() <= match.start() < url_match.end() for url_match in url_matches):
                continue  # ya se incorporo mediante URL sanitizada
            host = match.group(0).lower().rstrip(".")
            # Exclude host fragments that appear inside Java/Kotlin package names.
            if host.count(".") >= 1 and not host.startswith(("android.", "java.", "kotlin.")):
                host_rows[host].add(source)
    report["urls"] = [{"url": url, "sources": sorted(src)[:5]} for url, src in sorted(url_rows.items())[:300]]
    report["hosts"] = [{"host": host, "sources": sorted(src)[:5]} for host, src in sorted(host_rows.items())[:300]]

    # Evidencia de permisos: solo referencias literales en strings/bytecode extraido.
    declared = report["permissions"]["declared"]
    observed: dict[str, set[str]] = defaultdict(set)
    permission_names = set(declared)
    permission_names.update({
        "android.permission.INTERNET", "android.permission.ACCESS_NETWORK_STATE",
        "android.permission.CAMERA", "android.permission.RECORD_AUDIO",
        "android.permission.ACCESS_FINE_LOCATION", "android.permission.ACCESS_COARSE_LOCATION",
        "android.permission.READ_CONTACTS", "android.permission.WRITE_CONTACTS",
        "android.permission.READ_EXTERNAL_STORAGE", "android.permission.WRITE_EXTERNAL_STORAGE",
        "android.permission.POST_NOTIFICATIONS", "android.permission.READ_SMS",
        "android.permission.SEND_SMS", "android.permission.RECEIVE_SMS",
    })
    for source, text in all_strings:
        low_source = source.lower()
        if not (low_source.endswith((".dex", ".so", ".jar", ".aar", ".js", ".bundle", ".mjs"))
                or low_source.startswith("classes")):
            continue  # el manifest/strings.xml no prueban uso en codigo
        for permission in permission_names:
            short = permission.rsplit(".", 1)[-1]
            if permission in text or f"Manifest.permission.{short}" in text:
                observed[permission].add(source)
    report["permissions"]["observed_in_strings"] = [
        {"name": name, "sources": sorted(src)[:5]} for name, src in sorted(observed.items())]
    report["permissions"]["declared_not_observed"] = sorted(set(declared) - set(observed))

    report["firebase"] = firebase_configs
    if not firebase_configs:
        firebase_sources = sorted({name for name, vals in entries
                                   if "firebase" in name.lower() or "google-services" in name.lower()
                                   or any("firebase" in s.lower() for s in vals[:100])})
        if firebase_sources:
            report["firebase"].append({"config_detected": True, "sources": firebase_sources[:20],
                                       "structured_config": False})

    fingerprints: dict[str, set[str]] = defaultdict(set)
    for name, values in entries:
        haystack = name.lower() + "\n" + "\n".join(values[:8000]).lower()
        for sdk, hints in SDK_HINTS.items():
            if any(hint in haystack for hint in hints):
                fingerprints[sdk].add(name)
    report["sdk_fingerprints"] = [
        {"name": sdk, "evidence": sorted(sources)[:8]}
        for sdk, sources in sorted(fingerprints.items())]

    report["secrets"] = _secret_kind_and_confidence(all_strings)
    licenses: dict[tuple[str, str], str] = {}
    license_files: set[str] = set()
    for name, text in license_contents:
        license_files.add(name)
        license_id = _copyleft_id(text) or ("License file; type unknown" if LICENSE_NAME_RE.search(name.lower()) else "")
        if license_id:
            licenses[(name, license_id)] = "identified_from_text" if license_id != "License file; type unknown" else "filename_only"
    # Some AARs embed the license name in the path but omit the full text.
    for name, values in entries:
        low = name.lower()
        if LICENSE_NAME_RE.search(low):
            license_files.add(name)
            combined = "\n".join(values)
            license_id = _copyleft_id(combined)
            if license_id:
                licenses[(name, license_id)] = "identified_from_text"
    report["license_files"] = sorted(license_files)[:100]
    report["licenses"] = [
        {"file": path, "license": license_id, "evidence": evidence}
        for (path, license_id), evidence in sorted(licenses.items())[:100]]
    report["scan"]["limitations"].append(
        "Analisis estatico por strings/paths; no ejecuta codigo ni demuestra uso efectivo de un endpoint, permiso o SDK."
    )
    return report


def compare_reports(current: dict[str, Any], other: dict[str, Any]) -> dict[str, Any]:
    """Compara dos informes; secretos solo se comparan por cantidad/tipo, nunca por valor."""
    def values(report: dict[str, Any], key: str, field: str) -> set[str]:
        return {str(item.get(field)) for item in report.get(key, []) if isinstance(item, dict) and item.get(field)}

    categories = {
        "urls": (values(current, "urls", "url"), values(other, "urls", "url")),
        "hosts": (values(current, "hosts", "host"), values(other, "hosts", "host")),
        "deep_links": ({str(x.get("uri")) for x in current.get("deep_links", [])},
                       {str(x.get("uri")) for x in other.get("deep_links", [])}),
        "permissions": (set(current.get("permissions", {}).get("declared", [])),
                        set(other.get("permissions", {}).get("declared", []))),
        "sdk_fingerprints": (values(current, "sdk_fingerprints", "name"),
                              values(other, "sdk_fingerprints", "name")),
        "licenses": (values(current, "licenses", "license"), values(other, "licenses", "license")),
    }
    diff: dict[str, Any] = {
        "schema_version": 1,
        "current": {"apk": current.get("apk"), "package": current.get("package")},
        "other": {"apk": other.get("apk"), "package": other.get("package")},
        "categories": {},
        "secrets": {
            "current_count": len(current.get("secrets", [])),
            "other_count": len(other.get("secrets", [])),
            "note": "No se guardan ni comparan valores secretos.",
        },
    }
    for name, (cur, old) in categories.items():
        diff["categories"][name] = {"added_in_current": sorted(cur - old),
                                    "removed_from_current": sorted(old - cur),
                                    "comparable": True}
    if not (current.get("manifest_available") and other.get("manifest_available")):
        for name in ("deep_links", "permissions"):
            diff["categories"][name] = {
                "added_in_current": [], "removed_from_current": [], "comparable": False,
                "reason": "manifest XML no disponible en uno de los APKs",
            }
    return diff


def render_markdown(report: dict[str, Any]) -> str:
    """Informe humano sin valores secretos."""
    lines = [f"# Analisis estatico — `{report.get('package') or report.get('apk')}`", "",
             f"- APK: `{report.get('apk')}`", "",
             "> Escaneo local; el APK no se ejecuta. Los permisos y SDKs observados son evidencia textual, no prueba de comportamiento.", ""]
    for title, key, field in (("URLs/endpoints", "urls", "url"), ("Hosts", "hosts", "host")):
        lines += [f"## {title}", ""]
        items = report.get(key) or []
        lines += [f"- `{x.get(field)}` (fuentes: {', '.join('`'+s+'`' for s in x.get('sources', [])[:3])})"
                  for x in items[:100]] or ["- Sin hallazgos."]
        lines.append("")
    lines += ["## Deep links", ""]
    lines += [f"- `{x.get('uri')}` → `{x.get('component')}` (exported={x.get('exported')})"
              for x in report.get("deep_links", [])[:100]] or ["- Manifest decodificado no disponible o sin deep links."]
    lines += ["", "## Permisos: declarados y observados", ""]
    perms = report.get("permissions") or {}
    lines.append("- Declarados: " + (", ".join(f"`{x}`" for x in perms.get("declared", [])) or "ninguno/no disponible"))
    lines.append("- Referencias textuales encontradas: " + (", ".join(f"`{x['name']}`" for x in perms.get("observed_in_strings", [])) or "ninguna"))
    lines.append("- Declarados sin referencia textual directa: " + (", ".join(f"`{x}`" for x in perms.get("declared_not_observed", [])) or "ninguno"))
    lines += ["", f"> {perms.get('interpretation', '')}", "", "## Firebase", ""]
    lines += [f"- Configuracion/codigo detectado: `{json.dumps(x, ensure_ascii=False)}`" for x in report.get("firebase", [])] or ["- Sin configuracion reconocible."]
    lines += ["", "## SDKs / frameworks detectados", ""]
    lines += [f"- **{x['name']}**: {', '.join(f'`{p}`' for p in x.get('evidence', [])[:4])}"
              for x in report.get("sdk_fingerprints", [])] or ["- Sin huellas reconocidas."]
    lines += ["", "## Posibles secretos (valores siempre redactados)", ""]
    lines += [f"- `{x['type']}` · {x['confidence']} · {x['length']} caracteres · `{x['source']}`"
              for x in report.get("secrets", [])] or ["- Sin candidatos por patrones/entropia."]
    lines += ["", "## Licencias", ""]
    lines += [f"- `{x['license']}` · `{x['file']}` ({x['evidence']})" for x in report.get("licenses", [])] or ["- No se identificaron licencias copyleft en los archivos reconocidos."]
    lines += ["", "## Limites del escaneo", ""]
    lines += [f"- {x}" for x in report.get("scan", {}).get("limitations", [])]
    if report.get("scan", {}).get("truncated_entries"):
        lines.append("- Entradas omitidas por tamano: " + ", ".join(f"`{x}`" for x in report["scan"]["truncated_entries"][:20]))
    return "\n".join(lines) + "\n"


def render_diff_markdown(diff: dict[str, Any]) -> str:
    lines = ["# Diferencias entre APKs", "",
             f"- Actual: `{diff['current'].get('apk')}` ({diff['current'].get('package') or 'package desconocido'})",
             f"- Comparado: `{diff['other'].get('apk')}` ({diff['other'].get('package') or 'package desconocido'})", ""]
    for category, changes in diff.get("categories", {}).items():
        lines += [f"## {category}", ""]
        if changes.get("comparable") is False:
            lines += [f"- No comparable: {changes.get('reason', 'evidencia insuficiente')}.", ""]
            continue
        added = changes.get("added_in_current", [])
        removed = changes.get("removed_from_current", [])
        lines.append("- Añadido en el APK actual: " + (", ".join(f"`{x}`" for x in added) or "—"))
        lines.append("- Solo en el APK comparado: " + (", ".join(f"`{x}`" for x in removed) or "—"))
        lines.append("")
    secret = diff.get("secrets", {})
    lines += ["## Candidatos a secretos", "",
              f"- Actual: {secret.get('current_count', 0)} · comparado: {secret.get('other_count', 0)}",
              f"> {secret.get('note', '')}", ""]
    return "\n".join(lines)


def write_outputs(analysis_dir: str | Path, report: dict[str, Any]) -> dict[str, Path]:
    out = Path(analysis_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = {"static_json": out / "STATIC_ANALYSIS.json",
             "static_report": out / "STATIC_ANALYSIS.md",
             "license_notice": out / "NOTICE.md"}
    paths["static_json"].write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    paths["static_report"].write_text(render_markdown(report), encoding="utf-8")
    notice = ["# Inventario de licencias detectadas", "",
              "Este archivo registra candidatos encontrados en el APK analizado; no implica que se hayan copiado o redistribuido esos componentes.", ""]
    if report.get("licenses"):
        notice += ["## Archivos/licencias candidatos para revision", ""]
        for item in report["licenses"]:
            classification = ("tipo no determinado" if item["license"] == "License file; type unknown"
                              else f"candidato {item['license']}")
            notice.append(f"- `{item['file']}` — {classification} ({item['evidence']}). "
                          "Verifica la licencia y su alcance antes de cualquier redistribucion.")
    else:
        notice += ["No se identificaron licencias copyleft reconocibles en el escaneo limitado."]
    if report.get("license_files"):
        notice += ["", "## Archivos de licencia/NOTICE localizados", ""]
        notice += [f"- `{name}`" for name in report["license_files"]]
    notice += ["", "Esto no demuestra que el APK carezca de dependencias de terceros u obligaciones de licencia."]
    paths["license_notice"].write_text("\n".join(notice) + "\n", encoding="utf-8")
    return paths
