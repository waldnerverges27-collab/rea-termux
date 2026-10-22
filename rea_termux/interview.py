"""Entrevista: rea-termux pregunta como quieres la reimplementacion.

Se puede responder interactivamente o pasar un JSON con las respuestas
(`--answers`), util para automatizar. Toda pregunta tiene un valor por defecto
derivado del analisis, asi que Enter-Enter-Enter ya produce un plan valido.
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable

STACKS = {
    "1": ("android-kotlin", "Android nativo · Kotlin + Jetpack Compose + Gradle"),
    "2": ("flutter", "Flutter (Dart) · un codigo, Android/iOS/desktop"),
    "3": ("react-native", "React Native / Expo (TypeScript)"),
    "4": ("web-pwa", "Web PWA (React + Vite + TS) instalable"),
}


@dataclass
class Answers:
    app_name: str = ""
    package_id: str = ""
    purpose: str = ""
    improvements: str = ""
    stack: str = "android-kotlin"
    screens: list[str] = field(default_factory=list)
    backend: str = "mock"          # mock | same-api | new-api
    backend_base_url: str = ""
    keep_offline: bool = True
    ui_language: str = "es"
    remove_features: str = ""
    monetization: str = "ninguna"
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _norm_pkg(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "", name.lower())
    return f"com.example.{slug or 'app'}"


def _ask(prompt: str, default: str, *, asker: Callable[[str], str] | None = None) -> str:
    asker = asker or (lambda q: input(q))
    suffix = f" [{default}]" if default else ""
    try:
        val = asker(f"{prompt}{suffix}: ").strip()
    except EOFError:
        val = ""
    return val or default


def _ask_list(prompt: str, default: list[str],
              *, asker: Callable[[str], str] | None = None) -> list[str]:
    raw = _ask(prompt + " (separadas por coma)", ", ".join(default), asker=asker)
    return [x.strip() for x in raw.split(",") if x.strip()]


def interview(
    defaults: dict[str, Any],
    *,
    asker: Callable[[str], str] | None = None,
    out: Callable[[str], None] = print,
) -> Answers:
    a = Answers(**{k: v for k, v in defaults.items() if k in Answers.__dataclass_fields__})
    interactive = asker is not None or sys.stdin.isatty()
    ask = asker or (lambda q: input(q))

    out("\n=== Entrevista de reimplementacion ===")
    out("Pulsa Enter para aceptar el valor sugerido.\n")

    if interactive:
        a.app_name = _ask("Nombre de TU app", a.app_name or "MiApp", asker=ask)
        a.package_id = _ask("Package id / bundle id (no uses el del original)",
                            _norm_pkg(a.app_name), asker=ask)
        a.purpose = _ask("Proposito en una frase", a.purpose or "reimplementacion funcional", asker=ask)
        a.improvements = _ask(
            "¿Que quieres cambiar o mejorar respecto al original?"
            " (separado por ';')", a.improvements or "ninguna", asker=ask
        )
        out("\nStack destino:")
        for k, (sid, desc) in STACKS.items():
            out(f"  {k}) {desc}")
        pick = _ask("Elige 1-4", "1", asker=ask)
        a.stack = STACKS.get(pick, STACKS["1"])[0]
        a.screens = _ask_list("Pantallas/flujos a replicar primero",
                              a.screens or ["login", "home"], asker=ask)
        out("\nBackend:")
        out("  1) mock      -> datos falsos locales, sin red")
        out("  2) same-api  -> usar el mismo endpoint que la app original")
        out("  3) new-api   -> API propia nueva")
        b = _ask("Elige 1-3", {"mock": "1", "same-api": "2", "new-api": "3"}.get(a.backend, "1"),
                 asker=ask)
        a.backend = {"1": "mock", "2": "same-api", "3": "new-api"}.get(b, "mock")
        if a.backend == "same-api":
            a.backend_base_url = _ask("URL base de la API", a.backend_base_url, asker=ask)
            out("  AVISO: reutilizar la API de un tercero suele violar sus ToS;")
            out("         solo valido si es tuya o tienes permiso.")
        elif a.backend == "new-api":
            a.backend_base_url = _ask("URL base de tu API", a.backend_base_url or "http://localhost:8080", asker=ask)
        a.keep_offline = _ask("¿Debe funcionar sin conexion? (s/n)", "s", asker=ask).lower().startswith("s")
        a.ui_language = _ask("Idioma de la interfaz", a.ui_language, asker=ask)
        a.remove_features = _ask("Funciones a ELIMINAR (coma o 'ninguna')",
                                 a.remove_features or "ninguna", asker=ask)
        a.monetization = _ask("Monetizacion (ninguna/ads/suscripcion)", a.monetization, asker=ask)
        a.notes = _ask("Notas libres", a.notes, asker=ask)
    return a


def build_plan_markdown(
    a: Answers,
    *,
    source_package: str,
    evidence_summary: str,
    structured: dict | None = None,
) -> str:
    stack_desc = dict(STACKS.values()).get(a.stack, a.stack)
    return f"""\
# Plan de reimplementacion

- **App origen analizada:** `{source_package}`
- **Nueva app:** {a.app_name}
- **Package/bundle:** `{a.package_id}`
- **Stack:** {stack_desc}
- **Proposito:** {a.purpose}
- **Mejoras deseadas:** {a.improvements}
- **Backend:** {a.backend}{f' ({a.backend_base_url})' if a.backend_base_url else ''}
- **Offline:** {'si' if a.keep_offline else 'no'}
- **Idioma UI:** {a.ui_language}
- **Funciones a quitar:** {a.remove_features}
- **Monetizacion:** {a.monetization}
- **Pantallas iniciales:** {', '.join(a.screens) if a.screens else '(por definir)'}
- **Notas:** {a.notes}

## Reglas de la reconstruccion

1. **Se reescribe, no se copia.** El codigo del original es evidencia para
   entender comportamiento; los archivos generados son esqueletos propios con
   TODOs que apuntan a la evidencia.
2. **Marca y recursos.** No reutilizar nombre, logo, iconos, tipografias ni
   textos del original. La app nueva usa recursos propios.
3. **Secretos.** Cualquier clave/token encontrado en el APK se documenta como
   hallazgo de seguridad y NO se copia; se reemplaza por configuracion propia.
4. **Datos de terceros.** Si el backend usado no es tuyo, se usa `mock`.

{_structured_section(structured)}

## Evidencia base

{evidence_summary.strip() or '(sin resumen)'}
"""


def _structured_section(data: dict | None) -> str:
    if not data:
        return ""
    out = ["## Plan estructurado (del modelo, validado)", ""]
    for s_ in data.get("screens", [])[:20]:
        comps = ", ".join(s_.get("components") or []) or "—"
        out.append(f"- **{s_.get('name')}** — {s_.get('purpose')} · componentes: {comps}")
    if data.get("data_model"):
        out += ["", "### Modelo de datos", ""]
        for e in data["data_model"][:15]:
            src = e.get("source")
            out.append(f"- `{e.get('entity')}`: {', '.join(e.get('fields') or [])}"
                       + (f" _({src})_" if src else ""))
    if data.get("risks"):
        out += ["", "### Riesgos", ""] + [f"- {r}" for r in data["risks"][:10]]
    return "\n".join(out) + "\n"


def save_answers(a: Answers, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(a.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def load_answers(path: str | Path) -> Answers:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return Answers(**{k: v for k, v in raw.items() if k in Answers.__dataclass_fields__})
