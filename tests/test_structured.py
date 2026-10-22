"""Salidas estructuradas: esquemas, validacion, extraccion y Router.structured."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from rea_termux import schemas as S
from rea_termux.config import ProviderCfg
from rea_termux.providers import Router, messages_for_structured


# ------------------------------------------------------------------ esquemas
def test_strictify_closes_objects():
    st = S.strictify(S.PLAN_SCHEMA)
    assert st["additionalProperties"] is False
    screen = st["properties"]["screens"]["items"]
    assert screen["additionalProperties"] is False
    # strict exige TODAS las propiedades declaradas en required
    assert set(screen["required"]) == {"name", "purpose", "components", "data_needs"}
    loose = S.PLAN_SCHEMA["properties"]["screens"]["items"]["required"]
    assert loose == ["name", "purpose"]      # el original puede ser flexible
    # quita default/examples si los hubiera
    assert "default" not in json.dumps(st)


def test_validate_types_required_enum_extra():
    ok = {"unknowns": [{"topic": "red", "why": "sin captura"}]}
    assert S.validate(ok, S.UNKNOWNS_SCHEMA) == []

    assert any("obligatoria" in e for e in
               S.validate({"unknowns": [{"topic": "x"}]}, S.UNKNOWNS_SCHEMA))
    assert any("no permitida" in e for e in
               S.validate({"unknowns": [], "extra": 1}, S.UNKNOWNS_SCHEMA))
    assert any("esperaba array" in e for e in
               S.validate({"unknowns": "x"}, S.UNKNOWNS_SCHEMA))
    bad_enum = {"flows": [{"name": "a", "entry": "b", "steps": [],
                           "data": [{"x": 1}]}]}
    assert S.validate(bad_enum, S.FLOW_SCHEMA)


def test_coerce_fixes_common_slips():
    assert S.coerce({"unknowns": "texto"}, S.UNKNOWNS_SCHEMA)["unknowns"] == ["texto"]
    schema = {"type": "object", "properties": {"n": {"type": "integer"},
                                               "b": {"type": "boolean"}}}
    assert S.coerce({"n": "42", "b": "si"}, schema) == {"n": 42, "b": True}


def test_extract_json_variants():
    assert S.extract_json('{"a": 1}') == {"a": 1}
    assert S.extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert S.extract_json('Respuesta:\n{"a": {"b": "}"}, "c": [1,2]}\nfin')["c"] == [1, 2]
    assert S.extract_json("sin json") is None
    assert S.extract_json('texto con {"roto": ') is None


def test_messages_for_structured_injects_schema():
    msgs = messages_for_structured([{"role": "user", "content": "haz un plan"}],
                                   S.PLAN_SCHEMA)
    assert msgs[0]["role"] == "system" and "JSON Schema" in msgs[0]["content"]
    assert msgs[-1]["content"] == "haz un plan"


# --------------------------------------------------------------- router HTTP
class SchemaHandler(BaseHTTPRequestHandler):
    """Servidor que exige response_format y devuelve JSON (o basura a proposito)."""

    script: list[str] = []
    seen: list[dict] = []
    mode = "strict_ok"   # strict_ok | reject_schema | garbage_then_ok | always_garbage

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length"))))
        type(self).seen.append(body)
        rf = body.get("response_format") or {}
        n = len([m for m in body["messages"] if m["role"] == "assistant"])
        mode = type(self).mode

        if mode == "reject_schema" and rf.get("type") == "json_schema":
            self._send(400, {"error": {"message": "response_format json_schema not supported"}})
            return
        if mode == "always_garbage" or (mode == "garbage_then_ok" and n == 0):
            content = "Claro! aqui tienes: no es json"
        else:
            content = json.dumps({
                "unknowns": [{"topic": "endpoints", "why": "no se capturo red",
                              "next_step": "capturar trafico con mitmproxy"}],
                "summary": "falta la capa de red",
            })
        self._send(200, {"choices": [{"message": {"role": "assistant",
                                                  "content": content}}],
                         "model": "m"})

    def _send(self, code, payload):
        raw = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


@pytest.fixture()
def server():
    srv = HTTPServer(("127.0.0.1", 0), SchemaHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    SchemaHandler.seen = []
    yield srv
    srv.shutdown()


def router_for(srv, monkeypatch):
    monkeypatch.setenv("FAKE_KEY", "sk-x")
    p = ProviderCfg(name="fake", base_url=f"http://127.0.0.1:{srv.server_address[1]}/v1",
                    model="m", api_key_env="FAKE_KEY", retries=0)
    return Router([p]), p


def test_structured_ok_with_json_schema(server, monkeypatch):
    SchemaHandler.mode = "strict_ok"
    r, p = router_for(server, monkeypatch)
    res = r.structured([{"role": "user", "content": "x"}], S.UNKNOWNS_SCHEMA)
    assert res.data["unknowns"][0]["topic"] == "endpoints"
    assert res.errors == [] and res.attempts == 1
    sent = SchemaHandler.seen[0]["response_format"]
    assert sent["type"] == "json_schema" and sent["json_schema"]["strict"] is True
    assert sent["json_schema"]["schema"]["additionalProperties"] is False


def test_structured_degrades_to_json_object(server, monkeypatch):
    SchemaHandler.mode = "reject_schema"
    r, p = router_for(server, monkeypatch)
    res = r.structured([{"role": "user", "content": "x"}], S.UNKNOWNS_SCHEMA)
    assert res.data is not None
    assert p.structured_json_schema is False        # aprendido
    assert SchemaHandler.seen[-1]["response_format"]["type"] == "json_object"


def test_structured_retries_with_feedback(server, monkeypatch):
    SchemaHandler.mode = "garbage_then_ok"
    r, p = router_for(server, monkeypatch)
    res = r.structured([{"role": "user", "content": "x"}], S.UNKNOWNS_SCHEMA)
    assert res.data is not None
    # el reintento incluye el error como feedback
    last = SchemaHandler.seen[-1]["messages"]
    assert any("no contiene JSON" in (m.get("content") or "") for m in last)


def test_structured_gives_up_with_errors(server, monkeypatch):
    SchemaHandler.mode = "always_garbage"
    r, p = router_for(server, monkeypatch)
    res = r.structured([{"role": "user", "content": "x"}], S.UNKNOWNS_SCHEMA,
                       retries=0)
    assert res.data is None
    assert res.errors


# ------------------------------------------------------- integracion con el flujo
def test_extract_unknowns_structured(monkeypatch):
    from rea_termux.agent import extract_unknowns

    class FakeRouter:
        def structured(self, messages, schema, **kw):
            class R:
                data = {"unknowns": [{"topic": "red", "why": "sin captura",
                                      "next_step": "mitmproxy"}],
                        "summary": "resumen"}
                errors = []
            return R()

    items, summary = extract_unknowns(FakeRouter(), "texto")
    assert items == ["red: sin captura → mitmproxy"] and summary == "resumen"


def test_extract_unknowns_falls_back_to_text():
    from rea_termux.agent import extract_unknowns

    class BrokenRouter:
        def structured(self, *a, **k):
            raise RuntimeError("proveedor muerto")

    notas = "analisis\n\nHUECOS:\n- no verifique la red\n- falta el backend"
    items, summary = extract_unknowns(BrokenRouter(), notas)
    assert items == ["no verifique la red", "falta el backend"]
    assert summary == ""


def test_plan_markdown_includes_structured_section():
    from rea_termux.interview import Answers, build_plan_markdown

    a = Answers(app_name="N", package_id="com.mine.n", screens=["login"])
    md = build_plan_markdown(
        a, source_package="com.src", evidence_summary="x",
        structured={"screens": [{"name": "login", "purpose": "autenticar",
                                 "components": ["TextField"]}],
                    "data_model": [{"entity": "Usuario", "fields": ["email"],
                                    "source": "original"}],
                    "risks": ["backend no verificado"]},
    )
    assert "Plan estructurado" in md
    assert "`Usuario`: email" in md
    assert "backend no verificado" in md
