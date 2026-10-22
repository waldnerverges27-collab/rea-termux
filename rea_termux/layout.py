"""Inferencia geometrica conservadora del arbol de layout de una pantalla."""
from __future__ import annotations

from statistics import median
from typing import Any


def _screen_size(plan: dict[str, Any]) -> tuple[int, int]:
    screen = plan.get("screen") or {}
    try:
        width, height = int(screen.get("width", 0)), int(screen.get("height", 0))
    except (TypeError, ValueError):
        return 0, 0
    return (width, height) if width > 0 and height > 0 else (0, 0)


def _bbox(component: dict[str, Any]) -> tuple[float, float, float, float] | None:
    raw = component.get("bounds")
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        return None
    try:
        x1, y1, x2, y2 = (float(x) for x in raw)
    except (TypeError, ValueError):
        return None
    if x2 <= x1 or y2 <= y1:
        return None
    return x1, y1, x2, y2


def _contains(outer: tuple[float, float, float, float],
              inner: tuple[float, float, float, float]) -> bool:
    ox1, oy1, ox2, oy2 = outer
    ix1, iy1, ix2, iy2 = inner
    return ox1 <= ix1 and oy1 <= iy1 and ox2 >= ix2 and oy2 >= iy2


def _group_rows(nodes: list[dict[str, Any]], screen_height: int) -> list[list[dict[str, Any]]]:
    """Agrupa elementos que comparten una banda vertical, sin exigir XML perfecto."""
    valid = [(n, _bbox(n["component"])) for n in nodes]
    valid.sort(key=lambda pair: (
        pair[1][1] if pair[1] else 10**12,
        pair[1][0] if pair[1] else 10**12,
    ))
    rows: list[list[dict[str, Any]]] = []
    row_bounds: list[tuple[float, float] | None] = []
    for node, box in valid:
        if box is None:
            rows.append([node])
            row_bounds.append(None)
            continue
        y1, y2 = box[1], box[3]
        center = (y1 + y2) / 2
        best = None
        best_delta = float("inf")
        for i, band in enumerate(row_bounds):
            if band is None:
                continue
            by1, by2 = band
            overlap = max(0.0, min(y2, by2) - max(y1, by1))
            min_h = max(1.0, min(y2 - y1, by2 - by1))
            delta = abs(center - (by1 + by2) / 2)
            # Umbral relativo para que icono+etiqueta puedan agruparse en fila.
            if overlap / min_h >= 0.35 or delta <= max(10.0, 0.45 * min_h):
                if delta < best_delta:
                    best, best_delta = i, delta
        if best is None:
            rows.append([node])
            row_bounds.append((y1, y2))
        else:
            rows[best].append(node)
            old = row_bounds[best]
            assert old is not None
            row_bounds[best] = (min(old[0], y1), max(old[1], y2))
    for row in rows:
        row.sort(key=lambda n: (_bbox(n["component"]) or (0,))[0])
    return rows


def _layout_group(nodes: list[dict[str, Any]], screen_height: int) -> dict[str, Any]:
    rows = _group_rows(nodes, screen_height)
    valid_rows = [r for r in rows if r and all(_bbox(n["component"]) for n in r)]
    grid_rows = [r for r in valid_rows if len(r) >= 2]
    if len(grid_rows) >= 2:
        widths = [(_bbox(n["component"])[2] - _bbox(n["component"])[0])
                  for row in grid_rows for n in row]  # type: ignore[index]
        center_width = median(widths) if widths else 0
        compatible = center_width <= 0 or all(0.55 * center_width <= w <= 1.65 * center_width
                                               for w in widths)
        layout = "grid" if compatible else "rows"
    elif len(rows) >= 3 and all(len(r) == 1 for r in rows):
        types = [r[0]["component"].get("type") for r in rows]
        layout = "list" if len(set(types)) <= max(1, len(types) // 3) else "column"
    elif any(len(row) >= 2 for row in rows):
        layout = "rows"
    else:
        layout = "column"
    return {
        "layout": layout,
        "columns": max((len(row) for row in rows), default=1) if layout == "grid" else 1,
        "rows": [{"children": row} for row in rows],
    }


def infer_layout(plan: dict[str, Any]) -> dict[str, Any]:
    """Devuelve un arbol simple de regiones y agrupaciones; nunca inventa acciones.

    La deteccion se basa en bounds, semantica ya normalizada y tamaño de pantalla.
    Si faltan coordenadas, conserva el orden y reporta que la inferencia es limitada.
    """
    components = [c for c in plan.get("components", []) if isinstance(c, dict)]
    width, height = _screen_size(plan)
    diagnostics: list[str] = []
    if not width or not height:
        diagnostics.append("Sin dimensiones de pantalla: orden de flujo conservado, layout aproximado.")
    if components and not any(_bbox(c) for c in components):
        diagnostics.append("Los componentes no incluyen bounds validos; no se infiere grid ni regiones.")

    excluded: set[int] = set()
    app_indices: list[int] = []
    nav_indices: list[int] = []
    fab_indices: list[int] = []
    if height:
        for i, comp in enumerate(components):
            box = _bbox(comp)
            if not box:
                continue
            x1, y1, x2, y2 = box
            cy = (y1 + y2) / 2 / height
            if comp.get("type") == "fab":
                fab_indices.append(i)
            elif comp.get("type") == "appbar" and cy <= 0.30:
                app_indices.append(i)
            elif comp.get("type") == "nav" and cy >= 0.62:
                nav_indices.append(i)

        # Una cabecera se infiere solo desde un titulo textual visible en la banda superior.
        if not app_indices:
            title = (plan.get("title") or "").strip()
            candidates = [i for i, c in enumerate(components)
                          if c.get("type") == "text" and c.get("label", "").strip() == title
                          and (b := _bbox(c)) and (b[1] + b[3]) / 2 <= height * 0.12]
            if candidates:
                app_indices = [min(candidates, key=lambda i: _bbox(components[i])[1])]  # type: ignore[index]
        # Si el dump ofrece un contenedor appbar/nav, incorpora las etiquetas
        # y acciones contenidas para que no terminen como contenido central.
        for region in (app_indices, nav_indices):
            region_boxes = [_bbox(components[i]) for i in region]
            for i, comp in enumerate(components):
                if i in region or not _bbox(comp):
                    continue
                box = _bbox(comp)
                if any(rb and _contains(rb, box) for rb in region_boxes):
                    region.append(i)

        # Fallback prudente para NavigationBar sin clase semantica en el dump:
        # tres o mas controles pequeños en el borde inferior y repartidos horizontalmente.
        if not nav_indices:
            bottom = []
            for i, comp in enumerate(components):
                box = _bbox(comp)
                if not box or i in app_indices or i in fab_indices:
                    continue
                x1, y1, x2, y2 = box
                if y1 / height >= 0.80 and (y2 - y1) / height <= 0.09:
                    bottom.append(i)
            centers = sorted(((sum(_bbox(components[i])[::2]) / 2) / width)  # type: ignore[index]
                             for i in bottom)
            spread = centers[-1] - centers[0] if centers else 0
            clickables = sum(bool(components[i].get("clickable")) for i in bottom)
            if len(bottom) >= 3 and spread >= 0.40 and clickables >= 2:
                nav_indices = bottom
                diagnostics.append("Navegacion inferior inferida por la banda de controles.")

    excluded.update(app_indices)
    excluded.update(nav_indices)
    excluded.update(fab_indices)

    app_bar = None
    if app_indices:
        app_bar = {"title": plan.get("title", ""), "source": "semantic" if any(
            components[i].get("type") == "appbar" for i in app_indices) else "title-band"}
    bottom_nav = None
    if nav_indices:
        bottom_nav = {
            "items": [components[i].get("label", "").strip() for i in nav_indices
                      if components[i].get("label", "").strip()],
            "source": "semantic" if any(components[i].get("type") == "nav"
                                          for i in nav_indices) else "geometry",
        }
    fab = None
    if fab_indices:
        fab = {"label": components[fab_indices[0]].get("label", ""), "source": "semantic"}

    remaining = [i for i in range(len(components)) if i not in excluded]
    container_ids = [i for i in remaining if components[i].get("type") in {"card", "list"}
                     and _bbox(components[i])]
    parent: dict[int, int] = {}
    for child_i in remaining:
        child_box = _bbox(components[child_i])
        if not child_box:
            continue
        possible = []
        for container_i in container_ids:
            if container_i == child_i:
                continue
            container_box = _bbox(components[container_i])
            if container_box and _contains(container_box, child_box):
                ca = (container_box[2] - container_box[0]) * (container_box[3] - container_box[1])
                ia = (child_box[2] - child_box[0]) * (child_box[3] - child_box[1])
                if ca > ia * 1.12:
                    possible.append((ca, container_i))
        if possible:
            parent[child_i] = min(possible)[1]

    children_by_parent: dict[int, list[int]] = {}
    for child_i, parent_i in parent.items():
        children_by_parent.setdefault(parent_i, []).append(child_i)

    def make_node(i: int) -> dict[str, Any]:
        comp = components[i]
        kind = comp.get("type") if comp.get("type") in {"card", "list"} else "component"
        children = [make_node(j) for j in sorted(children_by_parent.get(i, []),
                                                 key=lambda j: (_bbox(components[j]) or (0, 0, 0, 0))[1])]
        node = {"kind": kind, "component": comp}
        if kind in {"card", "list"}:
            grouped = _layout_group(children, height)
            node["children_layout"] = grouped["layout"]
            node["children_rows"] = grouped["rows"]
        return node

    top_nodes = [make_node(i) for i in remaining if i not in parent]
    grouped = _layout_group(top_nodes, height)
    content = {
        "layout": grouped["layout"],
        "columns": grouped["columns"],
        "rows": grouped["rows"],
        "component_count": len(remaining),
    }
    if grouped["layout"] == "grid":
        diagnostics.append(f"Cuadricula inferida con hasta {grouped['columns']} columnas.")
    elif grouped["layout"] == "list":
        diagnostics.append("Lista vertical inferida por repeticion y alineacion de elementos.")
    if any(n.get("kind") == "card" for n in top_nodes):
        diagnostics.append("Tarjetas agrupadas por contencion geometrica.")
    return {
        "version": 1,
        "orientation": ("landscape" if width > height else "portrait") if width and height else "unknown",
        "app_bar": app_bar,
        "content": content,
        "bottom_navigation": bottom_nav,
        "fab": fab,
        "diagnostics": diagnostics,
    }
