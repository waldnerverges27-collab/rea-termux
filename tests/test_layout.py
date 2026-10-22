"""Inferencia de layout y propagacion a generadores multi-stack."""
from __future__ import annotations

from rea_termux.design import compose_screen, flutter_screen, react_screen, screen_plan
from rea_termux.layout import infer_layout
from rea_termux.verify import static_checks


def node(cls, bounds, text="", clickable=False, depth=1):
    return {"cls": cls, "bounds": bounds, "text": text, "desc": "", "res_id": "",
            "clickable": clickable, "editable": False, "depth": depth}


def dashboard_spec():
    nodes = [node("android.widget.TextView", [24, 24, 500, 90], "Panel")]
    for x, y, label in [
        (24, 150, "Uno"), (524, 150, "Dos"),
        (24, 470, "Tres"), (524, 470, "Cuatro"),
    ]:
        nodes.append(node("androidx.cardview.widget.CardView", [x, y, x + 450, y + 250], depth=2))
        nodes.append(node("android.widget.TextView", [x + 20, y + 20, x + 320, y + 72], label, depth=3))
    nodes.append(node("com.google.android.material.floatingactionbutton.FloatingActionButton",
                      [850, 760, 950, 860], "Crear", clickable=True, depth=2))
    nodes.append(node("com.google.android.material.bottomnavigation.BottomNavigationView",
                      [0, 900, 1000, 1000], depth=2))
    for x, label in [(50, "Inicio"), (360, "Buscar"), (680, "Cuenta")]:
        nodes.append(node("android.widget.TextView", [x, 930, x + 240, 980], label, depth=3))
    return {"name": "dashboard", "screen": {"width": 1000, "height": 1000}, "nodes": nodes}


def test_screen_plan_infers_grid_cards_and_regions():
    plan = screen_plan(dashboard_spec())
    layout = plan["layout"]
    assert layout["orientation"] == "portrait"
    assert layout["content"]["layout"] == "grid"
    assert layout["content"]["columns"] == 2
    assert layout["app_bar"]["title"] == "Panel"
    assert layout["fab"] and layout["bottom_navigation"]
    assert layout["bottom_navigation"]["items"] == ["Inicio", "Buscar", "Cuenta"]
    assert all(n["kind"] == "card" for row in layout["content"]["rows"]
               for n in row["children"])
    assert all(n["children_rows"] for row in layout["content"]["rows"]
               for n in row["children"])


def test_generators_consume_inferred_layout():
    plan = screen_plan(dashboard_spec())
    fl = flutter_screen(plan)
    kt = compose_screen(plan, "com.example.app")
    tsx = react_screen(plan)
    assert "GridView.count(crossAxisCount: 2" in fl
    assert "NavigationBar(destinations" in fl and "floatingActionButton" in fl
    assert "Row(Modifier.fillMaxWidth()" in kt
    assert "bottomBar = { NavigationBar" in kt and "floatingActionButton" in kt
    assert "gridTemplateColumns: 'repeat(2, minmax(0, 1fr))'" in tsx
    assert "aria-label=\"Navegación principal\"" in tsx
    assert "Acción principal" in tsx
    assert "{{}}" not in fl + kt + tsx


def test_generated_layout_sources_pass_static_brace_checks(tmp_path):
    from rea_termux.interview import Answers
    from rea_termux.design import write_ui

    plan = screen_plan(dashboard_spec())
    for stack in ("flutter", "android-kotlin", "web-pwa"):
        root = tmp_path / stack
        answers = Answers(app_name="Demo", package_id="com.example.demo", stack=stack)
        write_ui([plan], answers, root)
        errors = [f for f in static_checks(root, stack) if f.severity == "error"]
        assert not errors, (stack, errors)


def test_repeated_vertical_items_are_classified_as_list():
    plan = {"screen": {"width": 400, "height": 900}, "components": [
        {"type": "card", "label": "A", "bounds": [16, 100, 384, 200]},
        {"type": "card", "label": "B", "bounds": [16, 230, 384, 330]},
        {"type": "card", "label": "C", "bounds": [16, 360, 384, 460]},
    ]}
    layout = infer_layout(plan)
    assert layout["content"]["layout"] == "list"
    assert any("Lista vertical" in msg for msg in layout["diagnostics"])


def test_row_layout_and_unknown_geometry_are_conservative():
    plan = {"screen": {"width": 600, "height": 900}, "title": "",
            "components": [
                {"type": "button", "label": "A", "bounds": [0, 100, 280, 160]},
                {"type": "button", "label": "B", "bounds": [320, 100, 600, 160]},
            ]}
    layout = infer_layout(plan)
    assert layout["content"]["layout"] == "rows"
    assert len(layout["content"]["rows"][0]["children"]) == 2

    unknown = infer_layout({"components": [{"type": "text", "label": "Hola"}]})
    assert unknown["content"]["layout"] == "column"
    assert any("bounds" in msg for msg in unknown["diagnostics"])
