"""Politica de reimplementacion.

Modo `replicate`: se puede clonar la FUNCIONALIDAD de cualquier aplicacion
(las funciones e ideas no son objeto de derechos de autor). Lo que queda
prohibido, y el programa lo aplica tecnicamente, es:

  * copiar codigo del APK original (nunca se extrae ni se reempaqueta),
  * reutilizar recursos propietarios: nombre, logo, iconos, tipografias,
    textos, sonidos,
  * reproducir marcas registradas en identificadores o recursos,
  * eludir DRM, paywalls, licencias o protecciones anticopia.

Se exige una atestacion explicita del usuario y queda registro en el
workspace. Esto no es asesoria legal.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Config

MODE_ANALYZE = "analyze"
MODE_REPLICATE = "replicate"


class PolicyError(RuntimeError):
    pass


@dataclass
class Declaration:
    package: str
    apk_path: str
    sha256: str
    mode: str
    attested: bool
    attested_text: str
    created: str
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


ATTESTATION = (
    "Reimplemento solo la funcionalidad (comportamiento e ideas). NO reutilizare "
    "codigo, recursos graficos, textos, sonidos, iconos, tipografias ni la marca "
    "de la aplicacion original. Mi app tendra nombre, identidad visual y recursos "
    "propios. No eludire DRM, paywalls ni protecciones de licencia."
)


def sha256_file(path: str | Path, *, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            b = fh.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def trademark_guard(cfg: Config, package: str) -> str | None:
    """Devuelve el nombre de marca si el paquete pertenece a uno vigilado."""
    pkg = (package or "").lower()
    for b in cfg.policy.blocked_packages:
        b = b.lower()
        if pkg == b or pkg.startswith(b + "."):
            return b
    return None


def warnings_for(cfg: Config, package: str) -> list[str]:
    out: list[str] = []
    brand = trademark_guard(cfg, package)
    if brand:
        out.append(
            f"'{package}' pertenece a una marca vigilada ({brand}). Puedes clonar "
            f"la funcionalidad, pero el generador sustituye nombre, package id y "
            f"recursos por otros propios, y no permite replicar su identidad "
            f"visual ni su marca."
        )
    return out


def check_declaration(
    cfg: Config, package: str, *, need_clone: bool
) -> list[str]:
    """Exige atestacion para reimplementar. Devuelve avisos (no bloquea)."""
    if need_clone and not cfg.policy.attested:
        raise PolicyError(
            "Falta la atestacion de uso responsable. Ejecuta:\n"
            "  rea-termux declare <app.apk> --clone\n"
            "o responde 'si' en la entrevista. Se reimplementa la funcionalidad "
            "con identidad y recursos propios; no se copia codigo ni assets."
        )
    return warnings_for(cfg, package)


def safe_identifier(
    package: str, app_name: str, cfg: Config, current_id: str = ""
) -> tuple[str, str]:
    """Identidad propia: solo interviene si la app origen es una marca vigilada.

    Sin marca de por medio se respeta lo que eligio el usuario en la entrevista.
    """
    brand = trademark_guard(cfg, package)
    if not brand:
        return current_id or app_name, app_name
    word = brand.split(".")[-1]
    slug = "".join(c if c.isalnum() else "_" for c in app_name.lower()).strip("_") or "app"
    name = app_name
    if word and word in slug:
        slug = "_".join(x for x in slug.replace(word, " ").split("_") if x) or "app"
    if word and word in app_name.lower():
        name = app_name.lower().replace(word, "").strip(" -_.").title() or "MiApp"
    return f"com.example.{slug}", name


# --------------------------------------------------------------------- registro
def write_declaration(cfg: Config, d: Declaration, out_dir: Path | None = None) -> Path:
    ws = out_dir or cfg.ws()
    p = ws / "DECLARATION.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        f"""# Declaracion de uso responsable

- **Fecha:** {d.created}
- **Modo:** {d.mode}
- **Paquete analizado:** `{d.package}`
- **APK:** `{d.apk_path}`
- **SHA-256:** `{d.sha256}`
- **Atestacion:** {'SI' if d.attested else 'NO'}

## Texto atestado

> {d.attested_text or ATTESTATION}

## Avisos

{chr(10).join('- ' + w for w in d.warnings) or '- ninguno'}

Registro generado por `rea-termux`. La herramienta no copia codigo ni recursos
del APK original; el proyecto generado se escribe de cero.
""",
        encoding="utf-8",
    )
    (ws / "DECLARATION.json").write_text(
        json.dumps(d.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return p


def load_declaration(ws: Path) -> dict[str, Any] | None:
    f = ws / "DECLARATION.json"
    if not f.is_file():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def authorize_from_workspace(cfg: Config, ws: Path) -> bool:
    d = load_declaration(ws)
    if d and d.get("attested"):
        cfg.policy.attested = True
        cfg.policy.legal_basis = d.get("attested_text") or cfg.policy.legal_basis
        return True
    return bool(cfg.policy.attested)


def make_declaration(
    apk: str | Path,
    package: str,
    *,
    attested: bool,
    legal_basis: str = "",
    mode: str = MODE_REPLICATE,
    cfg: Config | None = None,
) -> Declaration:
    return Declaration(
        package=package,
        apk_path=str(apk),
        sha256=sha256_file(apk) if Path(apk).is_file() else "",
        mode=mode,
        attested=attested,
        attested_text=legal_basis or ATTESTATION,
        created=_dt.datetime.now().isoformat(timespec="seconds"),
        warnings=warnings_for(cfg, package) if cfg else [],
    )
