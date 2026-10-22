"""Memoria de proyecto persistente: la razon por la que el agente no olvida.

Se guarda en `<workspace>/.rea/memory.json` (+ `events.jsonl` append-only).

Contenido:
  * identidad del proyecto (nombre, package, stack, app origen)
  * `facts`: decisiones, restricciones, evidencia y hallazgos, con origen
  * `files`: inventario con sha256, rol y resumen de cada archivo que tocamos
  * `todos`: tareas abiertas/cerradas
  * `glossary`: vocabulario del dominio de la app
  * `ui`: tokens de diseno y pantallas capturadas
  * `timeline`: que se hizo y cuando

Cada turno del agente recibe un `digest()` compacto de todo esto, de modo que
aunque la conversacion se recorte, el estado del proyecto sigue presente.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

SCHEMA = 1
FACT_KINDS = ("decision", "constraint", "evidence", "finding", "improvement", "risk", "note")


def _now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class ProjectMemory:
    root: Path
    data: dict[str, Any] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    # ------------------------------------------------------------------
    @property
    def dir(self) -> Path:
        return self.root / ".rea"

    @property
    def path(self) -> Path:
        return self.dir / "memory.json"

    @property
    def events(self) -> Path:
        return self.dir / "events.jsonl"

    # ------------------------------------------------------------------
    @classmethod
    def load(cls, root: str | Path) -> "ProjectMemory":
        root = Path(root).expanduser().resolve()
        m = cls(root=root)
        if m.path.is_file():
            try:
                m.data = json.loads(m.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                m.data = {}
        m._migrate()
        return m

    def _migrate(self) -> None:
        d = self.data
        d.setdefault("schema", SCHEMA)
        d.setdefault("project", {
            "name": "", "package_id": "", "stack": "", "source_package": "",
            "created": _now(), "updated": _now(),
        })
        for k in ("facts", "todos", "timeline"):
            d.setdefault(k, [])
        d.setdefault("files", {})
        d.setdefault("glossary", {})
        d.setdefault("ui", {"tokens": {}, "screens": []})
        d.setdefault("sessions", [])

    # ------------------------------------------------------------------
    def save(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        with self._lock:
            self.data["project"]["updated"] = _now()
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.path)

    def event(self, kind: str, payload: dict[str, Any]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        with self._lock, open(self.events, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"t": _now(), "event": kind, **payload}, ensure_ascii=False) + "\n")

    # ------------------------------------------------------------- identidad
    def set_project(self, **kw: Any) -> None:
        for k, v in kw.items():
            if v not in (None, ""):
                self.data["project"][k] = v
        self.save()

    @property
    def project(self) -> dict[str, Any]:
        return self.data["project"]

    # ------------------------------------------------------------------ facts
    def remember(self, text: str, *, kind: str = "note", source: str = "agent",
                 key: str | None = None) -> dict[str, Any]:
        text = (text or "").strip()
        if not text:
            raise ValueError("texto vacio")
        if kind not in FACT_KINDS:
            kind = "note"
        # upsert: una clave fija reemplaza el hecho anterior (p.ej. "project")
        if key:
            for f in self.data["facts"]:
                if f.get("key") == key:
                    f.update({"text": text, "kind": kind, "source": source, "t": _now()})
                    self.save()
                    self.data["timeline"].append({"t": f["t"], "what": f"{kind}: {text[:120]}"})
                    self.event("remember_updated", f)
                    return f
        # deduplicacion por (kind, texto normalizado)
        norm = " ".join(text.lower().split())
        for f in self.data["facts"]:
            if f["kind"] == kind and " ".join(f["text"].lower().split()) == norm:
                return f
        fact = {
            "id": f"F{len(self.data['facts']) + 1}",
            "kind": kind,
            "text": text,
            "source": source,
            "key": key or "",
            "t": _now(),
        }
        self.data["facts"].append(fact)
        self.data["timeline"].append({"t": fact["t"], "what": f"{kind}: {text[:120]}"})
        self.save()
        self.event("remember", fact)
        return fact

    def facts(self, kind: str | None = None) -> list[dict[str, Any]]:
        if not kind:
            return list(self.data["facts"])
        return [f for f in self.data["facts"] if f["kind"] == kind]

    # ------------------------------------------------------------------ todos
    def add_todo(self, text: str, *, screen: str = "") -> dict[str, Any]:
        todo = {"id": f"T{len(self.data['todos']) + 1}", "text": text.strip(),
                "screen": screen, "status": "open", "t": _now()}
        self.data["todos"].append(todo)
        self.save()
        self.event("todo", todo)
        return todo

    def complete_todo(self, todo_id: str) -> dict[str, Any] | None:
        for t in self.data["todos"]:
            if t["id"] == todo_id:
                t["status"] = "done"
                t["done"] = _now()
                self.save()
                self.event("todo_done", t)
                return t
        return None

    def open_todos(self) -> list[dict[str, Any]]:
        return [t for t in self.data["todos"] if t["status"] == "open"]

    # ----------------------------------------------------------------- files
    def track_file(self, rel: str, *, role: str = "", summary: str = "") -> dict[str, Any]:
        p = self.root / rel
        entry = self.data["files"].setdefault(rel, {})
        entry.update({
            "sha256": _sha(p) if p.is_file() else "",
            "exists": p.is_file(),
            "role": role or entry.get("role", ""),
            "summary": summary or entry.get("summary", ""),
            "updated": _now(),
        })
        self.save()
        return entry

    def file_changed(self, rel: str) -> bool:
        e = self.data["files"].get(rel)
        if not e:
            return True
        p = self.root / rel
        if not p.is_file():
            return True
        return e.get("sha256") != _sha(p)

    def inventory(self) -> list[str]:
        return sorted(self.data["files"])

    def prune_missing(self) -> list[str]:
        """Quita del inventario archivos que ya no existen (rebuilds, borrados)."""
        gone = [rel for rel in self.data["files"]
                if not (self.root / rel).is_file()]
        for rel in gone:
            self.data["files"].pop(rel, None)
        if gone:
            self.save()
            self.event("prune", {"removed": gone[:50], "count": len(gone)})
        return gone

    # --------------------------------------------------------------- glossary
    def define(self, term: str, definition: str) -> None:
        if term.strip():
            self.data["glossary"][term.strip()] = definition.strip()
            self.save()

    # --------------------------------------------------------------------- ui
    def set_ui(self, tokens: dict[str, Any], screens: Iterable[dict[str, Any]]) -> None:
        self.data["ui"]["tokens"] = tokens
        self.data["ui"]["screens"] = list(screens)
        self.save()
        self.event("ui", {"tokens": list(tokens)[:20], "screens": len(self.data["ui"]["screens"])})

    # ---------------------------------------------------------------- sessions
    def register_session(self, session_id: str) -> None:
        if session_id not in self.data["sessions"]:
            self.data["sessions"].append(session_id)
            self.save()

    # ------------------------------------------------------------------ digest
    def digest(self, *, max_chars: int = 9000, focus: str = "") -> str:
        """Resumen compacto que se inyecta en cada turno del agente."""
        pr = self.project
        lines: list[str] = []
        lines.append("## ESTADO DEL PROYECTO (memoria persistente)")
        lines.append(
            f"- App: **{pr.get('name') or '(sin nombre)'}** · package `{pr.get('package_id') or '?'}`"
            f" · stack `{pr.get('stack') or '?'}` · origen `{pr.get('source_package') or '?'}`"
        )
        files = self.data["files"]
        if files:
            changed = [r for r in files if self.file_changed(r)]
            lines.append(f"- Archivos registrados: {len(files)}"
                         + (f" ({len(changed)} cambiados fuera del agente: "
                            f"{', '.join(changed[:6])})" if changed else ""))
            for rel in sorted(files)[:40]:
                e = files[rel]
                bits = [b for b in (e.get("role"), e.get("summary")) if b]
                lines.append(f"  · `{rel}`" + (f" — {'; '.join(bits)}" if bits else ""))
            if len(files) > 40:
                lines.append(f"  · … y {len(files) - 40} mas")

        facts = self.data["facts"]
        if facts:
            lines.append("- Decisiones y hallazgos (los mas recientes):")
            picked = facts[-28:]
            if focus:
                key = [f for f in facts
                       if any(w in f["text"].lower() for w in focus.lower().split())
                       and f not in picked][:8]
                picked = key + picked
            for f in picked:
                lines.append(f"  · [{f['kind']}] {f['text']} ({f['id']}, {f['source']})")

        todos = self.open_todos()
        if todos:
            lines.append("- Tareas abiertas:")
            for t in todos[:20]:
                lines.append(f"  · {t['id']} ({t['screen'] or 'general'}): {t['text']}")

        if self.data["glossary"]:
            lines.append("- Glosario: " + "; ".join(
                f"{k} = {v}" for k, v in list(self.data["glossary"].items())[:20]))

        ui = self.data["ui"]
        if ui.get("screens"):
            names = [s.get("name") for s in ui["screens"]][:30]
            lines.append(f"- Pantallas capturadas: {', '.join(n for n in names if n)}")
        if ui.get("tokens"):
            tok = ui["tokens"]
            cols = tok.get("colors") or []
            lines.append("- Tokens UI: " + str(len(cols)) + " colores"
                         + (f" ({', '.join(str(c.get('hex')) for c in cols[:6])})" if cols else ""))

        lines.append(
            "- Reglas fijas: identidad y recursos propios; sin copiar codigo ni "
            "assets del original; sin eludir DRM/paywalls."
        )
        text = "\n".join(lines)
        if len(text) > max_chars:
            text = text[: max_chars - 200] + "\n… (memoria recortada, usa project_status para el detalle)"
        return text

    # --------------------------------------------------------------- status md
    def status_markdown(self) -> str:
        pr = self.project
        out = [
            f"# Estado de {pr.get('name') or 'proyecto'}",
            "",
            f"- package: `{pr.get('package_id')}`",
            f"- stack: `{pr.get('stack')}`",
            f"- app origen: `{pr.get('source_package')}`",
            f"- creado: {pr.get('created')} · actualizado: {pr.get('updated')}",
            "",
            "## Decisiones y hallazgos",
            "",
        ]
        for f in self.data["facts"]:
            out.append(f"- **[{f['kind']}]** {f['text']} _({f['id']} · {f['source']})_")
        if not self.data["facts"]:
            out.append("_(vacio)_")
        out += ["", "## Tareas", ""]
        for t in self.data["todos"]:
            mark = "x" if t["status"] == "done" else " "
            out.append(f"- [{mark}] {t['id']} {t['text']}" + (f" · `{t['screen']}`" if t["screen"] else ""))
        if not self.data["todos"]:
            out.append("_(vacio)_")
        out += ["", "## Archivos", ""]
        for rel in sorted(self.data["files"]):
            e = self.data["files"][rel]
            out.append(f"- `{rel}` — {e.get('role') or '?'}: {e.get('summary') or ''}".rstrip())
        if not self.data["files"]:
            out.append("_(vacio)_")
        out += ["", "## Linea de tiempo (ultimos 30)", ""]
        for ev in self.data["timeline"][-30:]:
            out.append(f"- {ev['t']} · {ev['what']}")
        return "\n".join(out) + "\n"

    def write_status(self) -> Path:
        p = self.root / "PROJECT_STATUS.md"
        p.write_text(self.status_markdown(), encoding="utf-8")
        return p
