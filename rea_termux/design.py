"""De la captura de UI a sistema de diseno + codigo de pantallas.

Reglas de "parecida o mejor":
  * se conserva la ESTRUCTURA (que hay arriba, que es accionable, jerarquia),
  * se rehace la identidad (colores derivados, tipografia, radios, iconos),
  * se aplican mejoras medibles: rejilla de 8dp, objetivos tactiles >= 48dp,
    un CTA primario por pantalla, etiquetas de accesibilidad, modo oscuro.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .interview import Answers

# ------------------------------------------------------------------ mapeo
#: fragmento de clase Android -> tipo semantico de componente
CLASS_MAP: list[tuple[str, str]] = [
    ("FloatingActionButton", "fab"),
    ("BottomNavigation", "nav"),
    ("NavigationBar", "nav"),
    ("TabLayout", "nav"),
    ("Toolbar", "appbar"),
    ("ActionBar", "appbar"),
    ("RecyclerView", "list"),
    ("ListView", "list"),
    ("GridView", "list"),
    ("ScrollView", "scroll"),
    ("NestedScroll", "scroll"),
    ("Switch", "toggle"),
    ("CheckBox", "toggle"),
    ("ToggleButton", "toggle"),
    ("RadioButton", "toggle"),
    ("EditText", "input"),
    ("TextField", "input"),
    ("SearchView", "input"),
    ("Button", "button"),
    ("ImageView", "image"),
    ("ImageButton", "image"),
    ("ProgressBar", "progress"),
    ("CardView", "card"),
    ("TextView", "text"),
]

DENSITIES = {"ldpi": 120, "mdpi": 160, "hdpi": 240, "xhdpi": 320, "xxhdpi": 480, "xxxhdpi": 640}


def semantic_type(cls: str) -> str:
    for frag, kind in CLASS_MAP:
        if frag in cls:
            return kind
    return "container"


def dp_to_px(dp: float, density: int = 480) -> float:
    return dp * density / 160


def px_to_dp(px: float, density: int = 480) -> float:
    return px * 160 / density


# --------------------------------------------------------- plan de pantalla
def screen_plan(spec: dict[str, Any]) -> dict[str, Any]:
    """Convierte los nodos capturados en un plan de componentes ordenado."""
    useful = {"image", "list", "progress", "nav", "fab", "card", "toggle", "input",
              "button", "text"}
    nodes = [
        n for n in spec.get("nodes", [])
        if semantic_type(n.get("cls", "")) in useful
    ]
    # los nodos anidados se solapan: nos quedamos con el conjunto visible y
    # eliminamos contenedores cuyos hijos ya estan representados
    nodes.sort(key=lambda n: (n["bounds"][1], n["bounds"][0]))
    kept: list[dict[str, Any]] = []
    for n in nodes:
        x1, y1, x2, y2 = n["bounds"]
        area = max(1, (x2 - x1) * (y2 - y1))
        inside = any(
            k["bounds"][0] <= x1 and k["bounds"][1] <= y1
            and k["bounds"][2] >= x2 and k["bounds"][3] >= y2
            and k is not n and (k["bounds"][2] - k["bounds"][0]) * (k["bounds"][3] - k["bounds"][1]) > area
            for k in kept
        )
        if inside:
            continue
        kept.append(n)
    components = []
    for n in kept:
        kind = semantic_type(n.get("cls", ""))
        if kind == "container":
            continue
        components.append({
            "type": kind,
            "label": (n.get("text") or n.get("desc") or "").strip(),
            "id": n.get("res_id", ""),
            "bounds": n.get("bounds"),
            "clickable": bool(n.get("clickable")),
            "editable": bool(n.get("editable")),
        })
    # heuristica: el primer texto de la zona superior es el titulo
    title = next((c["label"] for c in components if c["type"] == "text" and c["label"]), spec.get("name", ""))
    return {
        "name": spec.get("name", "pantalla"),
        "title": title,
        "components": components[:40],
        "screen": spec.get("screen", {}),
        "colors": spec.get("theme", {}),
        "palette": spec.get("palette", []),
        "png": spec.get("png", ""),
        "wireframe": spec.get("wireframe", ""),
        "vision": spec.get("vision", {}),
    }


def improvements(plan: dict[str, Any], density: int = 480) -> list[str]:
    """Lista de mejoras concretas aplicadas/propuestas para esta pantalla."""
    out: list[str] = []
    buttons = [c for c in plan["components"] if c["type"] == "button"]
    if len(buttons) > 1:
        out.append(f"{len(buttons)} botones compiten: deja 1 accion primaria y el resto como "
                   f"secundarias (TextButton/OutlinedButton).")
    for c in plan["components"]:
        if c["clickable"] and c["bounds"]:
            x1, y1, x2, y2 = c["bounds"]
            h_dp = px_to_dp(y2 - y1, density)
            if h_dp < 44:
                out.append(f"Objetivo tactil de {h_dp:.0f}dp en '{c['label'][:24] or c['type']}'"
                           f" → subir a 48dp minimo (Material).")
        if c["type"] == "input" and not c["label"]:
            out.append("Campo de texto sin etiqueta visible → anadir label + placeholder + "
                       "tipo de teclado adecuado.")
        if c["type"] == "image" and not c["label"]:
            out.append("Imagen sin descripcion → contentDescription para accesibilidad.")
    if not any(c["type"] == "nav" for c in plan["components"]):
        out.append("Sin navegacion persistente: valora barra inferior si hay 3+ destinos.")
    out.append("Rejilla de 8dp para margenes/espaciados y radios coherentes (12/16dp).")
    out.append("Modo oscuro con los mismos roles de color (primary/surface/onSurface).")
    out.append("Estado de carga, vacio y error en cada lista (no solo el caso feliz).")
    return out[:14]


# ----------------------------------------------------------------- DESIGN.md
def build_design_md(plans: list[dict[str, Any]], tokens: dict[str, Any],
                    answers: Answers | None = None) -> str:
    colors = tokens.get("colors") or []
    theme = tokens.get("theme") or {}
    names = tokens.get("names") or {}
    lines = ["# Sistema de diseno", ""]
    if answers:
        lines += [f"- App: **{answers.app_name}** (`{answers.package_id}`)",
                  f"- Stack: `{answers.stack}` · idioma UI: `{answers.ui_language}`", ""]
    lines += ["## Color", "",
              f"- Primario derivado: `{theme.get('primary', '#3D5AFE')}`",
              f"- Superficie: `{theme.get('surface', '#FFFFFF')}` · texto: `{theme.get('on_surface', '#111111')}`",
              f"- Tema oscuro sugerido: {'si' if theme.get('dark_ui') else 'si (variante)'}", "",
              "| Rol | Hex | Uso |", "|---|---|---|"]
    palette = colors or [{"hex": "#3D5AFE", "share": 1}]
    for i, c in enumerate(palette[:8]):
        role = ["surface", "primary", "secondary", "accent", "neutral", "neutral-variant",
                "outline", "extra"][i] if i < 8 else "extra"
        lines.append(f"| {role} | `{c['hex']}` | {'fondo' if i == 0 else 'componentes'} |")
    lines += ["", "> Los colores se derivan de la captura original como referencia de "
                  "contraste y jerarquia; el tema final usa estos roles (no se copian "
                  "assets ni estilos del APK).", ""]

    if names:
        lines += ["## Colores con nombre detectados en recursos", ""]
        for k, v in list(names.items())[:20]:
            lines.append(f"- `{k}` = `{v}`")
        lines.append("")

    lines += ["## Tipografia y forma", "",
              "- Escala: `headlineSmall` titulo, `titleMedium` seccion, `bodyMedium` cuerpo, "
              "`labelSmall` metadatos.",
              "- Radios: 12dp tarjetas, 16dp hojas/dialogos, 24dp FAB.",
              "- Espaciado: rejilla de 8dp (`4/8/16/24/32`).",
              "- Objetivos tactiles: minimo 48x48dp.", ""]

    lines += ["## Pantallas", ""]
    for p in plans:
        lines += [f"### {p['name']}", "",
                  f"- Titulo propuesto: **{p['title'] or p['name']}**",
                  f"- Referencia: `{p['wireframe'] or p['png']}`", "",
                  "| # | Tipo | Texto/rol | Accionable |", "|---|---|---|---|"]
        for i, c in enumerate(p["components"][:24], 1):
            lines.append(f"| {i} | {c['type']} | {c['label'][:40] or '—'} | "
                         f"{'si' if c['clickable'] or c['editable'] else 'no'} |")
        vision = p.get("vision") or {}
        if vision:
            lines += ["", "**Lectura visual asistida**", "",
                      f"- Tipo: {vision.get('screen_type', 'no identificado')}",
                      f"- Layout: {vision.get('layout_description', '—')}",
                      f"- Accion principal: {vision.get('primary_action', '—')}",
                      f"- Confianza: {vision.get('confidence', '—')}"]
            for issue in vision.get("accessibility_issues", [])[:8]:
                lines.append(f"- Accesibilidad: {issue}")
            for item in vision.get("components", [])[:16]:
                lines.append(f"- Componente visual: {item.get('type', '?')} — "
                             f"{item.get('label', 'sin etiqueta')} ({item.get('position', '?')})")
        lines += ["", "**Mejoras propuestas**", ""]
        for imp in improvements(p):
            lines.append(f"- {imp}")
        lines.append("")
    return "\n".join(lines)


# ------------------------------------------------------------------- codigo
def _q(s: str) -> str:
    return s.replace("\\", "\\\\").replace("'", "\\'").replace("\n", " ")


def flutter_screen(plan: dict[str, Any], *, primary: str = "0xFF3D5AFE") -> str:
    name = plan["name"]
    cls = "".join(p[:1].upper() + p[1:] for p in re.split(r"[^a-zA-Z0-9]+", name) if p) or "Screen"
    rows: list[str] = []
    for c in plan["components"]:
        label = _q(c["label"])
        t = c["type"]
        if t == "input":
            rows.append(f"          TextField(decoration: InputDecoration(labelText: '{label or 'Campo'}', border: OutlineInputBorder())),")
        elif t == "button":
            rows.append(f"          SizedBox(width: double.infinity, height: 48, child: FilledButton(onPressed: () {{}}, child: const Text('{label or 'Continuar'}'))),")
        elif t == "toggle":
            rows.append(f"          SwitchListTile(title: Text('{label or 'Opcion'}'), value: false, onChanged: (v) {{}}),")
        elif t == "image":
            rows.append("          Container(height: 160, decoration: BoxDecoration(color: Colors.black12, borderRadius: BorderRadius.circular(12)), child: const Center(child: Icon(Icons.image_outlined, size: 40))),")
        elif t == "list":
            rows.append(f"          Card(child: ListTile(leading: const Icon(Icons.list), title: const Text('{label or 'Lista'}'), subtitle: const Text('TODO: datos reales'), onTap: () {{}})),")
        elif t == "nav":
            rows.append("          // TODO: NavigationBar con los destinos detectados")
        elif t == "fab":
            rows.append("          // TODO: FloatingActionButton para la accion principal")
        elif t == "progress":
            rows.append("          const LinearProgressIndicator(),")
        else:
            if label:
                rows.append(f"          Text('{label}', style: Theme.of(context).textTheme.bodyLarge),")
    if not rows:
        rows = ["          const Text('TODO: construir esta pantalla'),"]
    return f"""\
import 'package:flutter/material.dart';

/// Pantalla {name} — generada por rea-termux a partir de la captura de UI.
/// Estructura inspirada en el flujo original; identidad y assets son propios.
class {cls}Screen extends StatelessWidget {{
  const {cls}Screen({{super.key}});

  @override
  Widget build(BuildContext context) {{
    return Scaffold(
      appBar: AppBar(title: const Text('{_q(plan["title"] or name)}')),
      body: SafeArea(
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
{chr(10).join(rows)}
          ],
        ),
      ),
    );
  }}
}}
"""


def compose_screen(plan: dict[str, Any], package: str) -> str:
    name = plan["name"]
    cls = "".join(p[:1].upper() + p[1:] for p in re.split(r"[^a-zA-Z0-9]+", name) if p) or "Screen"
    rows: list[str] = []
    for c in plan["components"]:
        label = _q(c["label"])
        t = c["type"]
        if t == "input":
            rows.append(f'        OutlinedTextField(value = "", onValueChange = {{}}, label = {{ Text("{label or "Campo"}") }}, modifier = Modifier.fillMaxWidth())')
        elif t == "button":
            rows.append(f'        Button(onClick = {{}}, modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp)) {{ Text("{label or "Continuar"}") }}')
        elif t == "toggle":
            rows.append(f'        Row(verticalAlignment = Alignment.CenterVertically) {{ Switch(checked = false, onCheckedChange = {{}}); Text("  {label or "Opcion"}") }}')
        elif t == "image":
            rows.append('        Box(Modifier.fillMaxWidth().height(160.dp).clip(RoundedCornerShape(12.dp)).background(MaterialTheme.colorScheme.surfaceVariant), contentAlignment = Alignment.Center) { Icon(Icons.Image, contentDescription = null) }')
        elif t == "list":
            rows.append(f'        Card {{ ListTile(title = {{ Text("{label or "Lista"}") }}, subtitle = {{ Text("TODO: datos reales") }}, onClick = {{}}) }}')
        elif t == "progress":
            rows.append('        LinearProgressIndicator(Modifier.fillMaxWidth())')
        elif t == "nav":
            rows.append('        // TODO: NavigationBar con los destinos detectados')
        else:
            if label:
                rows.append(f'        Text("{label}", style = MaterialTheme.typography.bodyLarge)')
    body = "\n".join(rows) or '            Text("TODO: construir esta pantalla")'
    body = "\n".join("    " + line if line.strip() else line
                     for line in body.splitlines())
    return f"""\
package {package}.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.unit.dp
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Image

/**
 * Pantalla {name} — generada por rea-termux desde la captura de UI.
 * Estructura inspirada en el flujo original; identidad y recursos propios.
 */
@Composable
fun {cls}Screen() {{
    Scaffold(topBar = {{ TopAppBar(title = {{ Text("{_q(plan["title"] or name)}") }}) }}) {{ padding ->
        Column(
            Modifier.padding(padding).fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp)
        ) {{
{body}
        }}
    }}
}}
"""


def react_screen(plan: dict[str, Any]) -> str:
    name = plan["name"]
    cls = "".join(p[:1].upper() + p[1:] for p in re.split(r"[^a-zA-Z0-9]+", name) if p) or "Screen"
    rows: list[str] = []
    for c in plan["components"]:
        label = _q(c["label"])
        t = c["type"]
        if t == "input":
            rows.append(f"      <input placeholder=\"{label or 'Campo'}\" style={{{{ padding: 12, borderRadius: 12, border: '1px solid #ccc' }}}} />")
        elif t == "button":
            rows.append(f"      <button style={{{{ minHeight: 48, borderRadius: 12 }}}}>{label or 'Continuar'}</button>")
        elif t == "toggle":
            rows.append(f"      <label><input type=\"checkbox\" /> {label or 'Opcion'}</label>")
        elif t == "image":
            rows.append("      <div style={{ height: 160, background: '#eee', borderRadius: 12 }} />")
        elif t == "list":
            rows.append(f"      <div style={{{{ padding: 16, borderRadius: 12, background: '#f5f5f5' }}}}>{label or 'Lista'} <small>· TODO datos reales</small></div>")
        elif t == "nav":
            rows.append("      {/* TODO: barra de navegacion inferior */}")
        else:
            if label:
                rows.append(f"      <p>{label}</p>")
    body = "\n".join(rows) or "      <p>TODO: construir esta pantalla</p>"
    return f"""\
import React from 'react';

/** Pantalla {name} — generada por rea-termux desde la captura de UI. */
export default function {cls}Screen() {{
  return (
    <section style={{{{ display: 'grid', gap: 12, padding: 16 }}}}>
      <h2>{_q(plan["title"] or name)}</h2>
{body}
    </section>
  );
}}
"""


def write_ui(plans: list[dict[str, Any]], answers: Answers, out_dir: Path) -> list[Path]:
    """Escribe las pantallas generadas en el proyecto segun el stack."""
    written: list[Path] = []
    pkg = answers.package_id or "com.example.app"
    for plan in plans:
        cls = "".join(p[:1].upper() + p[1:] for p in re.split(r"[^a-zA-Z0-9]+", plan["name"]) if p) or "Screen"
        if answers.stack == "flutter":
            rel = f"lib/screens/{plan['name']}.dart"
            content = flutter_screen(plan)
        elif answers.stack == "android-kotlin":
            rel = f"app/src/main/java/{pkg.replace('.', '/')}/ui/screens/{cls}.kt"
            content = compose_screen(plan, pkg)
        elif answers.stack == "react-native":
            rel = f"src/screens/{cls}.tsx"
            content = react_screen(plan)
        else:
            rel = f"src/screens/{cls}.tsx"
            content = react_screen(plan)
        p = out_dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        written.append(p)
    return written


def save_tokens(ws: Path, tokens: dict[str, Any]) -> Path:
    p = ws / "ui" / "tokens.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(tokens, indent=2, ensure_ascii=False), encoding="utf-8")
    return p
