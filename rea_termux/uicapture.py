"""Captura de la UI real del telefono: screenshot + jerarquia de vistas.

Se usa `adb` (android-tools en Termux):
  * `adb exec-out screencap -p`            -> PNG de la pantalla
  * `adb shell uiautomator dump`           -> XML con el arbol de vistas
  * `adb shell dumpsys window`             -> tamano de pantalla / densidad

Con eso se construye, por pantalla: PNG, XML, wireframe SVG y una lista de
nodos normalizada (clase, texto, id, bounds, clickable...). Es la base para
generar una interfaz parecida (o mejor) sin copiar recursos del original.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from . import pngpalette

BOUNDS_RE = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")


class ADBError(RuntimeError):
    pass


# --------------------------------------------------------------------- adb
def adb_path() -> str | None:
    return shutil.which("adb")


def adb(*args: str, timeout: float = 60.0, binary: bool = False):
    exe = adb_path()
    if not exe:
        raise ADBError("adb no instalado (Termux: pkg install android-tools)")
    cmd = [exe, *args]
    proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
    if proc.returncode != 0:
        raise ADBError(
            f"{' '.join(cmd)} -> rc={proc.returncode}: "
            f"{(proc.stderr or b'').decode('utf-8', 'replace')[:300]}"
        )
    return proc.stdout if binary else proc.stdout.decode("utf-8", "replace")


def devices() -> list[str]:
    try:
        out = adb("devices")
    except ADBError:
        return []
    return [l.split("\t")[0] for l in out.splitlines()[1:]
            if l.strip() and l.endswith("device")]


def screen_size(serial: str | None = None) -> tuple[int, int]:
    try:
        prefix = ["-s", serial] if serial else []
        out = adb(*prefix, "shell", "wm", "size")
        m = re.search(r"(\d+)x(\d+)", out)
        if m:
            return int(m.group(1)), int(m.group(2))
    except ADBError:
        pass
    return 1080, 1920


# -------------------------------------------------------------------- nodos
@dataclass
class Node:
    index: int
    cls: str
    text: str = ""
    res_id: str = ""
    desc: str = ""
    bounds: tuple[int, int, int, int] = (0, 0, 0, 0)
    clickable: bool = False
    scrollable: bool = False
    editable: bool = False
    checked: bool = False
    enabled: bool = True
    depth: int = 0
    children: list["Node"] = field(default_factory=list)

    @property
    def short(self) -> str:
        return self.cls.split(".")[-1]

    @property
    def w(self) -> int:
        return self.bounds[2] - self.bounds[0]

    @property
    def h(self) -> int:
        return self.bounds[3] - self.bounds[1]

    def flat(self) -> list["Node"]:
        out = [self]
        for c in self.children:
            out.extend(c.flat())
        return out

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("children", None)
        d["short"] = self.short
        d["children"] = [c.to_dict() for c in self.children]
        return d


def _parse_bounds(raw: str) -> tuple[int, int, int, int]:
    m = BOUNDS_RE.search(raw or "")
    if not m:
        return (0, 0, 0, 0)
    return tuple(int(x) for x in m.groups())  # type: ignore[return-value]


def parse_hierarchy(xml_text: str) -> Node | None:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        start = xml_text.find("<?xml")
        if start < 0:
            return None
        try:
            root = ET.fromstring(xml_text[start:])
        except ET.ParseError:
            return None

    counter = {"n": 0}

    def build(el: ET.Element, depth: int) -> Node:
        counter["n"] += 1
        cls = el.get("class") or el.tag
        node = Node(
            index=counter["n"],
            cls=cls,
            text=el.get("text") or "",
            res_id=el.get("resource-id") or "",
            desc=el.get("content-desc") or "",
            bounds=_parse_bounds(el.get("bounds") or ""),
            clickable=el.get("clickable") == "true",
            scrollable=el.get("scrollable") == "true",
            editable=("EditText" in cls) or ("TextField" in cls),
            checked=el.get("checked") == "true",
            enabled=el.get("enabled") != "false",
            depth=depth,
        )
        for child in el:
            node.children.append(build(child, depth + 1))
        return node

    return build(root, 0)


VISUAL_HINTS = (
    "ImageView", "ImageButton", "VideoView", "WebView", "MapView", "ProgressBar",
    "RecyclerView", "ListView", "GridView", "CardView", "FloatingActionButton",
    "BottomNavigation", "TabLayout", "ViewPager", "Switch", "CheckBox", "RadioButton",
)


def spec_nodes(root: Node) -> list[Node]:
    """Nodos relevantes para reconstruir el layout (incluye visuales sin texto)."""
    out = []
    for n in root.flat():
        if (n.text or n.desc or n.clickable or n.editable or n.scrollable
                or any(h in n.cls for h in VISUAL_HINTS)):
            out.append(n)
    return out


def interactive_nodes(root: Node) -> list[Node]:
    out = []
    for n in root.flat():
        if n.text or n.desc or n.clickable or n.editable or n.scrollable:
            out.append(n)
    return out


# ------------------------------------------------------------------ wireframe
def _esc(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace('"', "&quot;"))


def wireframe_svg(root: Node, width: int, height: int) -> str:
    """Dibuja cajas etiquetadas: sirve para razonar sobre el layout."""
    scale = 360 / max(width, 1)
    w, h = width * scale, height * scale
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w:.0f}" height="{h:.0f}" '
        f'viewBox="0 0 {w:.0f} {h:.0f}">',
        f'<rect width="{w:.0f}" height="{h:.0f}" fill="#fafafa" stroke="#bbb"/>',
    ]
    palette = ["#c5cae9", "#b2dfdb", "#ffe0b2", "#f8bbd0", "#d1c4e9", "#c8e6c9"]
    for n in interactive_nodes(root):
        x1, y1, x2, y2 = n.bounds
        if x2 <= x1 or y2 <= y1:
            continue
        x, y = x1 * scale, y1 * scale
        bw, bh = (x2 - x1) * scale, (y2 - y1) * scale
        color = palette[(n.depth + n.index) % len(palette)]
        stroke = "#1a237e" if n.clickable else "#546e7a"
        parts.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" height="{bh:.1f}" '
            f'fill="{color}" fill-opacity="0.55" stroke="{stroke}" stroke-width="0.8"/>'
        )
        label = (n.text or n.desc or n.res_id.split("/")[-1] or n.short)[:22]
        size = max(4.5, min(9.0, bh * 0.6))
        if bh > 6:
            parts.append(
                f'<text x="{x + 2:.1f}" y="{y + size:.1f}" font-size="{size:.1f}" '
                f'font-family="monospace" fill="#102027">{_esc(label)}</text>'
            )
    parts.append("</svg>")
    return "\n".join(parts)


# ------------------------------------------------------------------- tokens
def tokens_from_resources(dirs: list[Path]) -> dict[str, Any]:
    """Busca definiciones de color/tipografia en recursos ya descompilados."""
    colors: dict[str, str] = {}
    for d in dirs:
        if not d.is_dir():
            continue
        for f in d.rglob("*.xml"):
            if f.stat().st_size > 400_000:
                continue
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if "<color" not in text and "color" not in f.name:
                continue
            for m in re.finditer(
                r'<(?:color|item)[^>]*name="([^"]+)"[^>]*>\s*(#[0-9a-fA-F]{3,8})',
                text,
            ):
                colors.setdefault(m.group(1), m.group(2).lower())
    return {"resource_colors": colors}


def capture(workspace: str | Path, name: str, *,
            serial: str | None = None, screenshot_bytes: bytes | None = None,
            screenshot_source: str = "adb") -> dict[str, Any]:
    """Screenshot + dump + wireframe de la pantalla actual del telefono."""
    ws = Path(workspace).resolve()
    ui = ws / "ui"
    ui.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^a-zA-Z0-9_.-]+", "_", name).strip("_") or "pantalla"

    devs = devices()
    if not devs:
        return {"ok": False, "error": "sin dispositivos adb (conecta el telefono o "
                                      "activa la depuracion USB / wireless)"}
    prefix = ["-s", serial] if serial else []
    width, height = screen_size(serial)

    png = ui / f"{safe}.png"
    try:
        raw = screenshot_bytes
        if raw is None:
            raw = adb(*prefix, "exec-out", "screencap", "-p", binary=True, timeout=90)
            screenshot_source = "adb local"
        if not raw.startswith(b"\x89PNG\r\n\x1a\n"):
            return {"ok": False, "error": "screenshot no es un PNG valido"}
        png.write_bytes(raw)
    except (ADBError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "error": f"screencap fallo: {exc}"}

    xml_path = ui / f"{safe}.xml"
    xml_text = ""
    try:
        remote = f"/sdcard/{safe}.uidump.xml"
        adb(*prefix, "shell", "uiautomator", "dump", remote, timeout=90)
        xml_text = adb(*prefix, "shell", "cat", remote, timeout=60)
        xml_path.write_text(xml_text, encoding="utf-8")
    except (ADBError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "error": f"uiautomator dump fallo: {exc}",
                "png": _rel(ws, png)}

    root = parse_hierarchy(xml_text)
    if root is None:
        return {"ok": False, "error": "no pude parsear la jerarquia de vistas",
                "png": _rel(ws, png)}

    svg = ui / f"{safe}.wireframe.svg"
    svg.write_text(wireframe_svg(root, width, height), encoding="utf-8")

    nodes = spec_nodes(root)
    spec = {
        "name": safe,
        "captured": True,
        "screen": {"width": width, "height": height},
        "png": _rel(ws, png),
        "xml": _rel(ws, xml_path),
        "wireframe": _rel(ws, svg),
        "nodes": [n.to_dict() for n in nodes][:400],
        "density_grid": "8dp",
        "screenshot_source": screenshot_source,
    }
    (ui / f"{safe}.json").write_text(
        __import__("json").dumps(spec, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    colors: list[dict[str, Any]] = []
    theme: dict[str, Any] = {}
    try:
        colors = pngpalette.palette(png, top=8)
        theme = pngpalette.suggest_theme(colors)
    except Exception as exc:  # PNG raro: no es fatal
        theme = {"error": str(exc)}

    spec["palette"] = colors
    spec["theme"] = theme
    (ui / f"{safe}.json").write_text(
        __import__("json").dumps(spec, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    return {
        "ok": True,
        "name": safe,
        "png": _rel(ws, png),
        "xml": _rel(ws, xml_path),
        "wireframe": _rel(ws, svg),
        "spec": _rel(ws, ui / f"{safe}.json"),
        "nodes": len(nodes),
        "size": f"{width}x{height}",
        "screenshot_source": screenshot_source,
        "colors": colors,
        "theme": theme,
        "texts": [n.text or n.desc for n in nodes if (n.text or n.desc)][:40],
        "actions": [
            {"cls": n.short, "id": n.res_id, "clickable": n.clickable,
             "editable": n.editable, "bounds": n.bounds}
            for n in nodes if n.clickable or n.editable
        ][:40],
    }


def _rel(ws: Path, p: Path) -> str:
    try:
        return str(p.relative_to(ws))
    except ValueError:
        return str(p)


def load_spec(workspace: str | Path, screen: str) -> dict[str, Any] | None:
    p = Path(workspace) / "ui" / f"{screen}.json"
    if not p.is_file():
        return None
    import json
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def list_screens(workspace: str | Path) -> list[str]:
    """Pantallas capturadas (ignora tokens.json y JSON que no son capturas)."""
    import json

    d = Path(workspace) / "ui"
    if not d.is_dir():
        return []
    out: list[str] = []
    for p in sorted(d.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict) and ("nodes" in data or "screen" in data):
            out.append(p.stem)
    return out
