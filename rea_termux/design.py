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
from .layout import infer_layout

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
    useful = {"image", "list", "progress", "nav", "fab", "card", "appbar", "toggle", "input",
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
            and k is not n and semantic_type(k.get("cls", "")) not in {"card", "list", "nav", "appbar"}
            and (k["bounds"][2] - k["bounds"][0]) * (k["bounds"][3] - k["bounds"][1]) > area
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
            "depth": int(n.get("depth", 0) or 0),
        })
    # heuristica: el primer texto de la zona superior es el titulo
    title = next((c["label"] for c in components if c["type"] == "text" and c["label"]), spec.get("name", ""))
    plan = {
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
    plan["layout"] = infer_layout(plan)
    return plan


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
    layout = plan.get("layout") or {}
    content_layout = (layout.get("content") or {}).get("layout")
    if content_layout == "grid":
        out.append("La geometria sugiere una cuadricula: conservar columnas y probar reflow en pantallas estrechas.")
    elif content_layout == "list":
        out.append("La geometria sugiere una lista repetida: separar filas y anadir estados vacio/carga/error.")
    if not layout.get("bottom_navigation") and not any(c["type"] == "nav" for c in plan["components"]):
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
                  f"- Layout inferido: `{(p.get('layout') or {}).get('content', {}).get('layout', 'column')}`"
                  f" · orientacion `{(p.get('layout') or {}).get('orientation', 'unknown')}`", ""]
        layout = p.get("layout") or {}
        if layout.get("app_bar"):
            lines.append(f"- App bar: detectada (titulo `{layout['app_bar'].get('title', '') or p['title']}`)")
        if layout.get("bottom_navigation"):
            nav_items = layout["bottom_navigation"].get("items") or []
            lines.append(f"- Navegacion inferior: detectada ({len(nav_items)} destino(s) etiquetado(s))")
        if layout.get("fab"):
            lines.append("- Accion flotante: detectada")
        for diagnostic in layout.get("diagnostics", [])[:4]:
            lines.append(f"- Inferencia: {diagnostic}")
        lines += ["", "| # | Tipo | Texto/rol | Accionable |", "|---|---|---|---|"]
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
    return (str(s).replace("\\", "\\\\").replace("'", "\\'")
            .replace('"', '\\"').replace("\n", " "))


def _html(s: str) -> str:
    import html
    return html.escape(str(s), quote=True)


def _plan_layout(plan: dict[str, Any]) -> dict[str, Any]:
    current = plan.get("layout")
    return current if isinstance(current, dict) and current.get("version") == 1 else infer_layout(plan)


def _group_nodes(group: dict[str, Any]) -> list[dict[str, Any]]:
    return [node for row in group.get("rows", []) for node in row.get("children", [])]


def _flutter_component(node: dict[str, Any]) -> str:
    comp = node.get("component") or {}
    kind = node.get("kind", "component")
    label = _q(comp.get("label", ""))
    if kind == "card":
        rows = node.get("children_rows") or []
        children = _flutter_group({"layout": node.get("children_layout", "column"), "rows": rows})
        if not children:
            children = ["const ListTile(title: Text('Tarjeta')), "]
        return ("Card(child: Padding(padding: const EdgeInsets.all(12), child: Column("
                "crossAxisAlignment: CrossAxisAlignment.start, children: [\n"
                + "\n".join("  " + x for x in children)
                + "\n]))),")
    if kind == "list":
        rows = node.get("children_rows") or []
        children = _flutter_group({"layout": node.get("children_layout", "column"), "rows": rows})
        if children:
            return ("Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [\n"
                    + "\n".join("  " + x for x in children) + "\n]),")
        return f"Card(child: ListTile(leading: const Icon(Icons.list), title: const Text('{label or 'Lista'}'), subtitle: const Text('TODO: datos reales'))),"
    t = comp.get("type")
    if t == "input":
        return f"TextField(decoration: InputDecoration(labelText: '{label or 'Campo'}', border: const OutlineInputBorder())),"
    if t == "button":
        return f"SizedBox(width: double.infinity, height: 48, child: FilledButton(onPressed: () {{}}, child: const Text('{label or 'Continuar'}'))),"
    if t == "toggle":
        return f"SwitchListTile(title: Text('{label or 'Opcion'}'), value: false, onChanged: (v) {{}}),"
    if t == "image":
        return "Container(height: 160, decoration: BoxDecoration(color: Colors.black12, borderRadius: BorderRadius.circular(12)), child: const Center(child: Icon(Icons.image_outlined, size: 40))),"
    if t == "progress":
        return "const LinearProgressIndicator(),"
    if label:
        return f"Text('{label}', style: Theme.of(context).textTheme.bodyLarge),"
    return "const SizedBox.shrink(),"


def _flutter_group(group: dict[str, Any]) -> list[str]:
    rows = group.get("rows") or []
    if group.get("layout") == "grid":
        nodes = _group_nodes(group)
        cols = max(2, min(int(group.get("columns", 2) or 2), 4))
        children = "\n".join("  " + _flutter_component(n) for n in nodes)
        return [f"GridView.count(crossAxisCount: {cols}, shrinkWrap: true, "
                "physics: const NeverScrollableScrollPhysics(), crossAxisSpacing: 12, "
                "mainAxisSpacing: 12, children: [\n" + children + "\n]),"]
    out: list[str] = []
    for row in rows:
        children = row.get("children") or []
        if len(children) > 1:
            items = "\n".join("  Expanded(child: " + _flutter_component(n).rstrip(",") + "),"
                                for n in children)
            out.append("Row(crossAxisAlignment: CrossAxisAlignment.start, children: [\n"
                       + items + "\n]),")
        elif children:
            out.append(_flutter_component(children[0]))
    return out


def _nav_labels(layout: dict[str, Any]) -> list[str]:
    data = layout.get("bottom_navigation") or {}
    labels = [str(x).strip() for x in data.get("items", []) if str(x).strip()]
    if data and len(labels) < 2:
        labels = [f"Destino {i + 1}" for i in range(max(3, len(labels)))]
    return labels[:5]


def flutter_screen(plan: dict[str, Any], *, primary: str = "0xFF3D5AFE") -> str:
    name = plan["name"]
    cls = "".join(p[:1].upper() + p[1:] for p in re.split(r"[^a-zA-Z0-9]+", name) if p) or "Screen"
    layout = _plan_layout(plan)
    children = _flutter_group(layout.get("content") or {})
    if not children:
        children = ["const Text('TODO: construir esta pantalla'),"]
    body = "\n".join("          " + line for line in children)
    labels = _nav_labels(layout)
    bottom = ""
    if labels:
        items = ",\n".join(
            f"          NavigationDestination(icon: const Icon(Icons.circle_outlined), label: '{_q(label)}')"
            for label in labels)
        bottom = f"      bottomNavigationBar: NavigationBar(destinations: [\n{items}\n      ]),\n"
    fab = "      floatingActionButton: FloatingActionButton(onPressed: () {}, child: const Icon(Icons.add)),\n" if layout.get("fab") else ""
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
{bottom}{fab}      body: SafeArea(
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
{body}
          ],
        ),
      ),
    );
  }}
}}
"""


def _compose_component(node: dict[str, Any], weighted: bool = False) -> str:
    comp = node.get("component") or {}
    kind = node.get("kind", "component")
    label = _q(comp.get("label", ""))
    modifier = "Modifier.weight(1f)" if weighted else "Modifier.fillMaxWidth()"
    if kind in {"card", "list"}:
        children = _compose_group({"layout": node.get("children_layout", "column"),
                                   "rows": node.get("children_rows") or []})
        if kind == "card" and children:
            body = "\n".join("    " + x for x in children)
            return f"Card({modifier}) {{ Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {{\n{body}\n}} }}"
        if children:
            body = "\n".join("    " + x for x in children)
            return f"Column({modifier}, verticalArrangement = Arrangement.spacedBy(8.dp)) {{\n{body}\n}}"
        return f'Card({modifier}) {{ ListItem(headlineContent = {{ Text("{label or "Lista"}") }}, supportingContent = {{ Text("TODO: datos reales") }}) }}'
    t = comp.get("type")
    if t == "input":
        return f'OutlinedTextField(value = "", onValueChange = {{}}, label = {{ Text("{label or "Campo"}") }}, modifier = {modifier})'
    if t == "button":
        btn_modifier = f"{modifier}.heightIn(min = 48.dp)"
        return f'Button(onClick = {{}}, modifier = {btn_modifier}) {{ Text("{label or "Continuar"}") }}'
    if t == "toggle":
        return f'Row(verticalAlignment = Alignment.CenterVertically) {{ Switch(checked = false, onCheckedChange = {{}}); Text("  {label or "Opcion"}") }}'
    if t == "image":
        return 'Box(Modifier.fillMaxWidth().height(160.dp).clip(RoundedCornerShape(12.dp)).background(MaterialTheme.colorScheme.surfaceVariant), contentAlignment = Alignment.Center) { Icon(Icons.Filled.Image, contentDescription = null) }'
    if t == "progress":
        return 'LinearProgressIndicator(Modifier.fillMaxWidth())'
    return f'Text("{label}", style = MaterialTheme.typography.bodyLarge)' if label else 'Spacer(Modifier.height(1.dp))'


def _compose_group(group: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for row in group.get("rows") or []:
        nodes = row.get("children") or []
        if len(nodes) > 1:
            children = "\n".join("    " + _compose_component(n, weighted=True) for n in nodes)
            out.append("Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {\n"
                       + children + "\n}")
        elif nodes:
            out.append(_compose_component(nodes[0]))
    return out


def compose_screen(plan: dict[str, Any], package: str) -> str:
    name = plan["name"]
    cls = "".join(p[:1].upper() + p[1:] for p in re.split(r"[^a-zA-Z0-9]+", name) if p) or "Screen"
    layout = _plan_layout(plan)
    body_lines = _compose_group(layout.get("content") or {})
    if not body_lines:
        body_lines = ['Text("TODO: construir esta pantalla")']
    body = "\n".join("            " + line for line in body_lines)
    labels = _nav_labels(layout)
    bottom = ""
    if labels:
        items = "\n".join(
            f'            NavigationBarItem(selected = {str(i == 0).lower()}, onClick = {{}}, icon = {{ Text("•") }}, label = {{ Text("{_q(label)}") }})'
            for i, label in enumerate(labels))
        bottom = f"        bottomBar = {{ NavigationBar {{\n{items}\n        }} }},\n"
    fab = '        floatingActionButton = { FloatingActionButton(onClick = {}) { Text("+") } },\n' if layout.get("fab") else ""
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
    Scaffold(topBar = {{ TopAppBar(title = {{ Text("{_q(plan["title"] or name)}") }}) }},
{bottom}{fab}    ) {{ padding ->
        Column(
            Modifier.padding(padding).fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp)
        ) {{
{body}
        }}
    }}
}}
"""


def _react_component(node: dict[str, Any]) -> str:
    comp = node.get("component") or {}
    kind = node.get("kind", "component")
    label = _html(comp.get("label", ""))
    if kind in {"card", "list"}:
        children = _react_group({"layout": node.get("children_layout", "column"),
                                 "rows": node.get("children_rows") or []})
        if kind == "card":
            return "<article style={{ padding: 16, borderRadius: 12, background: '#f5f5f5' }}>" + children + "</article>"
        if children:
            return "<div role=\"list\" style={{ display: 'grid', gap: 12 }}>" + children + "</div>"
        return f"<div role=\"list\" style={{{{ padding: 16, borderRadius: 12, background: '#f5f5f5' }}}}>{label or 'Lista'} <small>· TODO datos reales</small></div>"
    t = comp.get("type")
    if t == "input":
        return f'<input aria-label="{label or "Campo"}" placeholder="{label or "Campo"}" style={{{{ padding: 12, borderRadius: 12, border: \'1px solid #ccc\' }}}} />'
    if t == "button":
        return f'<button style={{{{ minHeight: 48, borderRadius: 12 }}}}>{label or "Continuar"}</button>'
    if t == "toggle":
        return f'<label><input type="checkbox" /> {label or "Opcion"}</label>'
    if t == "image":
        return '<div role="img" aria-label="Imagen" style={{ height: 160, background: \'#eee\', borderRadius: 12 }} />'
    if t == "progress":
        return '<progress aria-label="Cargando" />'
    return f"<p>{label}</p>" if label else "<span />"


def _react_group(group: dict[str, Any]) -> str:
    layout = group.get("layout")
    rows = group.get("rows") or []
    if layout == "grid":
        cols = max(2, min(int(group.get("columns", 2) or 2), 4))
        children = "".join(_react_component(n) for n in _group_nodes(group))
        return f"<div style={{{{ display: 'grid', gridTemplateColumns: 'repeat({cols}, minmax(0, 1fr))', gap: 12 }}}}>{children}</div>"
    out = []
    for row in rows:
        nodes = row.get("children") or []
        children = "".join(_react_component(n) for n in nodes)
        if len(nodes) > 1:
            out.append(f"<div style={{{{ display: 'flex', gap: 12, alignItems: 'center' }}}}>{children}</div>")
        elif children:
            out.append(children)
    return "".join(out)


def react_screen(plan: dict[str, Any]) -> str:
    name = plan["name"]
    cls = "".join(p[:1].upper() + p[1:] for p in re.split(r"[^a-zA-Z0-9]+", name) if p) or "Screen"
    layout = _plan_layout(plan)
    body = _react_group(layout.get("content") or {}) or "<p>TODO: construir esta pantalla</p>"
    labels = _nav_labels(layout)
    nav = ("<nav aria-label=\"Navegación principal\" style={{ display: 'flex', gap: 16, "
           "justifyContent: 'space-around', padding: 12 }}>" +
           "".join(f"<button key=\"{i}\">{_html(label)}</button>" for i, label in enumerate(labels)) + "</nav>") if labels else ""
    fab = '<button aria-label="Acción principal" style={{ position: \'fixed\', right: 24, bottom: 24, minHeight: 48 }}>+</button>' if layout.get("fab") else ""
    return f"""\
import React from 'react';

/** Pantalla {name} — generada por rea-termux desde la captura de UI. */
export default function {cls}Screen() {{
  return (
    <section style={{{{ display: 'grid', gap: 12, padding: 16 }}}}>
      <h2>{_html(plan["title"] or name)}</h2>
      {body}
      {nav}
      {fab}
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
