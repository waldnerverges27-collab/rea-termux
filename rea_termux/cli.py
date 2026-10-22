"""Interfaz de linea de comandos de rea-termux."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

from . import __version__
from . import agent as agent_module
from .agent import Agent
from .config import Config, config_path, data_home, dump_json, load, write_default
from .interview import Answers, build_plan_markdown, interview, load_answers, save_answers
from .devtools import DevTools
from .mcp_client import MCPClient, select_tools
from .memory import ProjectMemory
from .policy import (PolicyError, authorize_from_workspace, check_declaration,
                     make_declaration, safe_identifier, write_declaration)
from .preflight import preflight
from .providers import AllProvidersFailed, Router
from .report import build_analysis, summary_for_prompt
from .scaffold import write_project
from . import (android_tools, design, probe as probe_mod, rea_cli, static_analysis,
               uicapture, vcs, verify as verify_mod)

BOLD, DIM, RED, YEL, GRN, RST = "\033[1m", "\033[2m", "\033[31m", "\033[33m", "\033[32m", "\033[0m"


def info(msg: str) -> None:
    print(f"{BOLD}»{RST} {msg}")


def warn(msg: str) -> None:
    print(f"{YEL}!{RST} {msg}")


def err(msg: str) -> None:
    print(f"{RED}✗{RST} {msg}", file=sys.stderr)


def ok(msg: str) -> None:
    print(f"{GRN}✓{RST} {msg}")


# --------------------------------------------------------------------- helpers
def _cfg(args: argparse.Namespace) -> Config:
    return load(getattr(args, "config", None))


def _router(cfg: Config) -> Router:
    chain = cfg.provider_chain()
    if not chain:
        raise SystemExit(
            "Sin proveedores usables.\n"
            f"Edita {config_path()} y define base_url/model/api_key_env."
        )
    info("Proveedores: " + " -> ".join(p.name for p in chain))
    return Router(chain, log=lambda m: print(f"{DIM}{m}{RST}"))


def _mcp(cfg: Config, out_dir: Path) -> MCPClient:
    logp = out_dir / "mcp-server.log"
    out_dir.mkdir(parents=True, exist_ok=True)
    env = dict(cfg.mcp.env)
    jadx = os.environ.get("REA_JADX_MCP_JAR")
    if jadx:
        env.setdefault("REA_JADX_MCP_JAR", jadx)
    return MCPClient(
        command=cfg.mcp.command,
        args=list(cfg.mcp.args),
        env=env,
        cwd=cfg.mcp.cwd or None,
        startup_timeout_s=cfg.mcp.startup_timeout_s,
        call_timeout_s=cfg.mcp.call_timeout_s,
        stderr_log=logp,
    )


def _workspace_for(cfg: Config, apk: Path, override: str | None) -> Path:
    if override:
        p = Path(override).expanduser()
    else:
        stem = apk.stem if apk.is_file() else "app"
        p = cfg.ws() / f"{stem}-{time.strftime('%Y%m%d-%H%M')}"
    p.mkdir(parents=True, exist_ok=True)
    return p


ANALYSIS_OBJECTIVE = """\
Analiza a fondo el APK ya descompuesto que tienes disponible mediante tus
herramientas (REA). El paquete es `{package}` y el APK esta en `{apk}`.

Produce un analisis en este orden, deteniendote cuando tengas evidencia
suficiente (no vuelques arboles completos):

1. **Mapa de la app**: entry points, actividades/fragmentos principales,
   navegacion entre pantallas, y que hace cada flujo.
2. **Flujos clave**: para los 3-5 flujos mas importantes (login, listado,
   detalle, ajustes, pago...), la cadena UI -> handler -> metodo que hace el
   trabajo, con nombres exactos `paquete.Clase.metodo`.
3. **Datos y red**: modelos de datos, endpoints/URLs, formato de peticiones,
   almacenamiento local (prefs, Room, ficheros), y autenticacion.
4. **Dependencias y arquitectura**: librerias relevantes (Compose, Retrofit,
   Firebase, Flutter, React Native...), y si hay indicios de ofuscacion.
5. **Reimplementacion**: para cada flujo, que habria que escribir de cero en una
   app nueva (pantalla, estado, capa de datos) — sin copiar codigo.

Reglas: cita evidencia (nombres de clases/metodos y el archivo de evidencia);
si algo no lo pudiste verificar, dilo. Responde en espanol.

Termina SIEMPRE con esta seccion, en este formato exacto:

HUECOS:
- <cosa que no pudiste verificar y por que>
- ...
"""


def _strip_unknowns(text: str) -> str:
    return text.split("HUECOS:", 1)[0].rstrip()


# ------------------------------------------------------------------- comandos
def cmd_init(args: argparse.Namespace) -> int:
    p = write_default(args.config, force=args.force)
    ok(f"configuracion en {p}")
    print(
        "\nEdita base_url / model / api_key_env de tus proveedores y exporta las llaves:\n"
        "  export NINEROUTER_API_KEY=...\n"
        "  export OMNIROUTE_API_KEY=...\n"
        "Luego:  rea-termux doctor"
    )
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:  # noqa: C901
    cfg = _cfg(args)
    info("Entorno")
    okall, checks = preflight(cfg)
    info("Configuracion")
    print(f"  archivo: {config_path() if config_path().is_file() else '(no existe, usando defaults)'}")
    print(f"  workspace: {cfg.ws()}")
    chain = cfg.provider_chain()
    if not chain:
        warn("Ningun proveedor usable (revisa base_url/model).")
    for p in chain:
        key = "OK" if p.resolve_key() else "FALTA"
        print(f"  [{key}] {p.name}: {p.base_url} · modelo {p.model} · ${p.api_key_env}")
    if getattr(args, "probe_rea", False):
        info("Validacion contra REA (probe)")
        rep = probe_mod.probe(cfg, apk=getattr(args, "apk", None), deep=True)
        probe_mod.print_report(rep)
        probe_mod.save(rep)
        okall = okall and rep.ok
    if args.probe and chain:
        info("Probando credenciales (GET /models)")
        router = Router(chain)
        for p in chain:
            good, detail = router.probe(p)
            (ok if good else err)(f"{p.name}: {detail}")
        router.close()
    return 0 if okall else 2


def cmd_models(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    chain = cfg.provider_chain()
    if not chain:
        err("sin proveedores configurados")
        return 2
    router = Router(chain)
    for p in chain:
        good, detail = router.probe(p)
        print(f"{'✓' if good else '✗'} {p.name}: {detail}")
    router.close()
    return 0


def cmd_tools(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    out = cfg.ws() / "_mcp"
    with _mcp(cfg, out) as mcp:
        info(f"servidor: {mcp.server_info}")
        tools = mcp.list_tools()
        sel, notes = select_tools(
            tools, max_tools=cfg.mcp.max_tools if not args.all else 10**6,
            always_include=cfg.mcp.always_include,
            include_patterns=cfg.mcp.include_patterns,
            exclude_patterns=cfg.mcp.exclude_patterns,
        )
        ok(f"{len(tools)} herramientas en el servidor, {len(sel)} expuestas al modelo")
        for n in notes:
            warn(n)
        if args.json:
            print(json.dumps(sel if not args.all else tools, indent=2, ensure_ascii=False))
        else:
            for t in sel:
                print(f"  - {t.get('name')}: {(t.get('description') or '')[:88]}")
    return 0


def cmd_declare(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    apk = Path(args.apk).expanduser()
    if not apk.is_file():
        err(f"no existe: {apk}")
        return 2
    pkg = args.package or rea_cli.detect_package_name(apk) or apk.stem
    attested = bool(args.clone or args.own)
    d = make_declaration(apk, pkg, attested=attested,
                         legal_basis=args.basis or "", cfg=cfg)
    ws = _workspace_for(cfg, apk, args.out)
    path = write_declaration(cfg, d, ws)
    ok(f"declaracion guardada en {path}")
    for w in d.warnings:
        warn(w)
    if not attested:
        warn("sin atestacion: `plan` y `build` pediran confirmacion")
    return 0


def cmd_analyze(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    apk = Path(args.apk).expanduser()
    if not apk.is_file():
        err(f"no existe: {apk}")
        return 2
    diff_apk = Path(args.diff).expanduser() if getattr(args, "diff", None) else None
    if diff_apk and not diff_apk.is_file():
        err(f"no existe el APK de comparacion: {diff_apk}")
        return 2
    ws = _workspace_for(cfg, apk, args.out)
    analysis = ws / "analysis"
    evidence = analysis / "evidence"
    evidence.mkdir(parents=True, exist_ok=True)

    staged = ws / apk.name
    if not staged.exists():
        shutil.copy2(apk, staged)

    passing, _ = preflight(cfg, verbose=not args.quiet)
    if not passing:
        warn("faltan dependencias obligatorias; el analisis puede fallar")

    rea_cli.set_fail_log(analysis / "failures.jsonl")
    info("Herramientas Android locales: aapt, apksigner, zipalign y apktool (si estan instaladas)")
    android_report = android_tools.process_apk(
        staged, analysis, decode_resources=not getattr(args, "no_apktool", False))
    android_paths = android_tools.write_report(analysis, android_report)
    artifacts: dict[str, Path] = dict(android_paths)
    for key, detail in android_report.get("aapt_metadata", {}).items():
        if key == "package":
            info(f"aapt package: {detail}")
    failed_steps = [s for s in android_report.get("steps", [])
                    if s.get("available") and not s.get("ok")]
    if failed_steps:
        warn("una o mas comprobaciones Android devolvieron aviso/error; consulta ANDROID_TOOLS.md")
    else:
        ok(f"herramientas Android -> {android_paths['android_tools_report']}")
    if not args.no_vcs:
        res_vcs = vcs.init(ws)
        if res_vcs.get("ok"):
            vcs.snapshot(ws, "rea-termux: workspace creado")
        elif res_vcs.get("reason"):
            warn(f"git: {res_vcs['reason']}")
    info("Paso 1/3 · descompilacion determinista con el CLI de REA")
    if not rea_cli.npx_available():
        err("npx no disponible; instala nodejs")
        return 2
    res, art_inspect = rea_cli.inspect_android_package(cfg, staged, evidence,
                                                       full=not args.fast)
    if art_inspect:
        artifacts["inspect"] = art_inspect
        ok(f"manifest/clases -> {art_inspect}")
    elif not res.ok:
        err("REA no pudo descompilar el APK:")
        err(f"  {res.error_message or 'sin detalle'}")
        if res.remediation:
            err(f"  remediacion: {res.remediation}")
        for line in probe_mod.suggest_from_error(cfg, res.error or {}):
            err(f"  -> {line}")
        if not args.continue_on_error:
            err("abortando (usa --continue-on-error para generar solo el informe parcial)")
            return 3
    else:
        warn("el CLI no devolvio JSON. Revisa el log:")
        warn((res.stderr or res.stdout or "")[:800])

    # la verificacion de firma ya viene dentro de inspect_android_package
    # (normalized_result.signature_verification); el comando aparte de REA no
    # acepta un APK como objetivo, asi que solo se usa si se pide explicitamente
    if getattr(args, "with_signature", False):
        sig_res, art_sig = rea_cli.inspect_apk_signature(cfg, staged, evidence)
        if art_sig:
            artifacts["signature"] = art_sig
            ok(f"firmas -> {art_sig}")
        elif not sig_res.ok:
            warn(f"firma: {sig_res.error_message}")

    pkg_rea = rea_cli.detect_package_name(staged, art_inspect)
    try:
        from .apkmeta import detect_package as _axml_pkg
        pkg_axml = _axml_pkg(staged)
    except Exception:
        pkg_axml = None
    if pkg_rea and pkg_axml and pkg_rea != pkg_axml:
        warn(f"discrepancia de package id: REA dice {pkg_rea}, el manifest dice {pkg_axml}")
    pkg_aapt = (android_report.get("aapt_metadata") or {}).get("package")
    package = args.package or pkg_rea or pkg_axml or pkg_aapt or staged.stem
    info(f"package id detectado: {package}")

    info("Escaneo estatico local del APK (sin ejecutarlo)")
    manifest_data = {}
    if art_inspect:
        try:
            manifest_data = json.loads(art_inspect.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            warn("no se pudo leer el artefacto de manifest de REA; pruebo el manifest de apktool")
    if not manifest_data:
        apktool_manifest = android_report.get("artifacts", {}).get("apktool_manifest")
        if apktool_manifest and Path(apktool_manifest).is_file():
            try:
                manifest_xml = Path(apktool_manifest).read_text(encoding="utf-8", errors="replace")
                manifest_data = {"manifest": {"text": manifest_xml}}
                artifacts["apktool_manifest"] = Path(apktool_manifest)
            except OSError:
                warn("no se pudo leer el manifest decodificado por apktool")
    static_report = static_analysis.analyze_apk(staged, manifest_data)
    if not static_report.get("package"):
        static_report["package"] = package
    static_paths = static_analysis.write_outputs(analysis, static_report)
    artifacts["static_json"] = static_paths["static_json"]
    artifacts["static_report"] = static_paths["static_report"]
    artifacts["license_notice"] = static_paths["license_notice"]
    ok(f"estatico: {len(static_report['urls'])} URLs · {len(static_report['hosts'])} hosts · "
       f"{len(static_report['secrets'])} candidatos redactados · "
       f"{len(static_report['licenses'])} licencias copyleft candidatas")

    static_diff_paths: dict[str, Path] = {}
    if diff_apk:
        info(f"comparando static analysis con {diff_apk.name}")
        other_analysis_dir = analysis / "diff_other"
        other_android_report = android_tools.process_apk(
            diff_apk, other_analysis_dir,
            decode_resources=not getattr(args, "no_apktool", False))
        other_android_paths = android_tools.write_report(other_analysis_dir, other_android_report)
        artifacts.update({"diff_" + key: path for key, path in other_android_paths.items()})
        other_evidence = other_analysis_dir / "evidence"
        other_evidence.mkdir(parents=True, exist_ok=True)
        other_manifest = {}
        other_result, other_inspect = rea_cli.inspect_android_package(
            cfg, diff_apk, other_evidence, full=False)
        if other_inspect:
            try:
                other_manifest = json.loads(other_inspect.read_text(encoding="utf-8"))
                artifacts["diff_other_inspect"] = other_inspect
            except (OSError, json.JSONDecodeError):
                warn("no se pudo leer el manifest del APK comparado; diff local sigue")
        elif not other_result.ok:
            warn("REA no obtuvo el manifest del APK comparado; el diff estatico omite deep links/permisos declarados")
        if not other_manifest:
            other_apktool_manifest = other_android_report.get("artifacts", {}).get("apktool_manifest")
            if other_apktool_manifest and Path(other_apktool_manifest).is_file():
                try:
                    other_manifest = {"manifest": {"text": Path(other_apktool_manifest).read_text(
                        encoding="utf-8", errors="replace")}}
                    artifacts["diff_other_apktool_manifest"] = Path(other_apktool_manifest)
                except OSError:
                    warn("no se pudo leer el manifest apktool del APK comparado")
        other_report = static_analysis.analyze_apk(diff_apk, other_manifest)
        diff_data = static_analysis.compare_reports(static_report, other_report)
        static_diff_paths = {
            "static_diff_json": analysis / "STATIC_DIFF.json",
            "static_diff_report": analysis / "STATIC_DIFF.md",
        }
        static_diff_paths["static_diff_json"].write_text(
            json.dumps(diff_data, indent=2, ensure_ascii=False), encoding="utf-8")
        static_diff_paths["static_diff_report"].write_text(
            static_analysis.render_diff_markdown(diff_data), encoding="utf-8")
        artifacts.update(static_diff_paths)
        ok("diff estatico -> " + str(static_diff_paths["static_diff_report"]))

    notes, unknowns = "", []
    if not args.no_agent:
        info("Paso 2/3 · analisis profundo con el agente (MCP + IA)")
        router = _router(cfg)
        try:
            with _mcp(cfg, analysis) as mcp:
                agent = Agent(cfg, router, mcp, out_dir=analysis,
                              stream=not getattr(args, "no_stream", False))
                run = agent.run(
                    ANALYSIS_OBJECTIVE.format(package=package,
                                              apk=str(staged)),
                    max_turns=args.turns,
                    context=static_analysis.render_markdown(static_report)[:10000],
                )
                notes = _strip_unknowns(run.final_text) or "(sin notas)"
                unknowns = agent_module._parse_unknowns(run.final_text)
                # lista de huecos validada por esquema (con fallback al parseo)
                structured_unknowns, u_summary = agent_module.extract_unknowns(
                    router, run.final_text)
                if structured_unknowns:
                    unknowns = structured_unknowns
                    ok(f"huecos estructurados: {len(unknowns)}")
                    if u_summary:
                        print(f"  {DIM}{u_summary}{RST}")
                    (analysis / "unknowns.json").write_text(
                        json.dumps({"unknowns": unknowns, "summary": u_summary},
                                   indent=2, ensure_ascii=False), encoding="utf-8")
                (analysis / "AGENT_NOTES.md").write_text(notes, encoding="utf-8")
                ok(f"agente: {run.turns} turnos, {run.tool_calls} herramientas, "
                   f"proveedor {run.provider_used or '?'}")
                if run.error:
                    warn(f"error del agente: {run.error}")
                if run.transcript_path:
                    print(f"{DIM}  transcript: {run.transcript_path}{RST}")
        finally:
            router.close()
    else:
        info("Paso 2/3 · omitido (--no-agent)")

    info("Paso 3/3 · informe")
    out = build_analysis(analysis, package=package, apk_name=staged.name,
                         agent_notes=notes, unknowns=unknowns, artifacts=artifacts)
    ok(f"informe -> {out}")
    (ws / "WORKSPACE.json").write_text(
        json.dumps({"package": package, "package_axml": pkg_axml,
                    "apk": str(staged), "analysis": str(analysis),
                    "created": time.time()},
                   indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"\n{SIGUIENTE}")
    return 0


SIGUIENTE = (
    "Siguiente:\n"
    "  rea-termux plan <workspace>      # entrevista: como quieres tu version\n"
    "  rea-termux build <workspace>     # genera el esqueleto del proyecto"
)


def _find_analysis_dir(target: Path) -> Path:
    if (target / "analysis" / "ANALYSIS.md").is_file():
        return target / "analysis"
    if (target / "ANALYSIS.md").is_file():
        return target
    raise SystemExit(f"no encuentro ANALYSIS.md en {target}; corre `analyze` primero")


def propose_plan(router, analysis_md: str, answers, package: str):
    """Plan de pantallas/datos validado por esquema (opcional, con --structured)."""
    from .schemas import PLAN_SCHEMA

    messages = [
        {"role": "system", "content": (
            "Disenas la reimplementacion de una app a partir de su analisis. "
            "Propon pantallas con su proposito, los componentes principales y "
            "las entidades de datos. No inventes datos que no esten en el "
            "analisis; si algo no se sabe, indicalo con source='no_verificado'. "
            "No reutilices la marca ni textos del original.")},
        {"role": "user", "content": (
            f"App origen: {package}\n"
            f"Nombre nuevo: {answers.app_name}\nPackage: {answers.package_id}\n"
            f"Stack: {answers.stack} · backend: {answers.backend}\n"
            f"Mejoras pedidas: {answers.improvements}\n"
            f"Pantallas ya elegidas: {', '.join(answers.screens)}\n\n"
            "Analisis:\n" + analysis_md[:14000])},
    ]
    return router.structured(messages, PLAN_SCHEMA, schema_name="plan")


def cmd_plan(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    ws = Path(args.workspace).expanduser().resolve()
    analysis = _find_analysis_dir(ws)
    meta = {}
    mp = ws / "WORKSPACE.json"
    if mp.is_file():
        meta = json.loads(mp.read_text(encoding="utf-8"))
    package = meta.get("package") or args.package or ws.name
    packages = {p for p in (meta.get("package"), meta.get("package_axml"),
                            args.package) if p}
    if authorize_from_workspace(cfg, ws):
        info("declaracion de titularidad encontrada en el workspace")

    if args.answers:
        answers = load_answers(args.answers)
        info(f"respuestas cargadas de {args.answers}")
    else:
        base = Path(package.split(".")[-1].capitalize() + "App").name
        defaults: dict[str, Any] = {}
        try:
            for _p in (packages or {package}):
                for w in check_declaration(cfg, _p, need_clone=True):
                    warn(w)
            defaults["backend"] = "mock"
        except PolicyError as exc:
            warn(str(exc))
            if not args.force:
                print("\nEl plan de reimplementacion requiere declaracion de "
                      "titularidad. Ejecuta:\n"
                      f"  rea-termux declare <apk> --own --out {ws}\n"
                      "y vuelve a intentarlo (o usa --force para solo documentar).")
                return 3
        defaults.update({
            "app_name": base,
            "package_id": f"com.example.{package.split('.')[-1]}",
            "screens": ["home"],
            "purpose": "reimplementacion funcional propia",
        })
        answers = interview(defaults)

    try:
        for _p in (packages or {package}):
            for w in check_declaration(cfg, _p, need_clone=True):
                warn(w)
    except PolicyError as exc:
        if not args.force:
            err(str(exc))
            return 3
        warn(f"continuando en modo documento: {exc}")

    if getattr(args, "structured", False):
        analysis_md = (analysis / "ANALYSIS.md").read_text(encoding="utf-8")
        router = _router(cfg)
        try:
            info("proponiendo pantallas y modelo de datos (salida estructurada)")
            res = propose_plan(router, analysis_md, answers, package)
            if res.data:
                screens = [s_["name"] for s_ in res.data.get("screens", [])]
                if screens and not args.answers:
                    answers.screens = screens or answers.screens
                (ws / "PLAN_STRUCTURED.json").write_text(
                    json.dumps(res.data, indent=2, ensure_ascii=False), encoding="utf-8")
                ok(f"plan estructurado: {len(screens)} pantalla(s), "
                   f"{len(res.data.get('data_model', []))} entidad(es) -> "
                   f"{ws / 'PLAN_STRUCTURED.json'}")
            else:
                warn("el modelo no devolvio un plan valido; sigo con la entrevista")
                for e in res.errors[:5]:
                    print(f"  {DIM}- {e}{RST}")
        finally:
            router.close()

    save_answers(answers, ws / "answers.json")
    structured = None
    psp = ws / "PLAN_STRUCTURED.json"
    if psp.is_file():
        try:
            structured = json.loads(psp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            structured = None
    plan = build_plan_markdown(
        answers, source_package=package,
        evidence_summary=summary_for_prompt(analysis, 3000),
        structured=structured,
    )
    (ws / "CLONE_PLAN.md").write_text(plan, encoding="utf-8")
    ok(f"plan -> {ws / 'CLONE_PLAN.md'}")
    print(f"\nSiguiente:\n  rea-termux build {ws}")
    return 0


def cmd_build(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    ws = Path(args.workspace).expanduser().resolve()
    if not (ws / "answers.json").is_file():
        err(f"falta {ws/'answers.json'}; corre `rea-termux plan {ws}`")
        return 2
    answers = load_answers(ws / "answers.json")
    if args.stack:
        answers.stack = args.stack
    meta = {}
    if (ws / "WORKSPACE.json").is_file():
        meta = json.loads((ws / "WORKSPACE.json").read_text(encoding="utf-8"))
    package = meta.get("package", ws.name)

    authorize_from_workspace(cfg, ws)
    try:
        check_declaration(cfg, package, need_clone=True)
    except PolicyError as exc:
        err(str(exc))
        return 3

    # guardarrail de marca: renombrar identificadores si procede
    if cfg.policy.rename_branded:
        new_pkg, new_name = safe_identifier(package, answers.app_name, cfg,
                                            current_id=answers.package_id)
        if new_pkg != answers.package_id:
            info(f"identidad propia (marca vigilada): package "
                 f"{answers.package_id} → {new_pkg}, nombre → {new_name}")
        answers.package_id, answers.app_name = new_pkg, new_name

    out = ws / "app" / answers.stack
    if out.exists() and any(out.iterdir()) and not args.force:
        err(f"{out} ya existe y no esta vacio (usa --force)")
        return 2
    files = write_project(answers, out, source_package=package)

    # pantallas derivadas de las capturas de UI
    mem = ProjectMemory.load(ws)
    for gone in mem.prune_missing():
        warn(f"archivo desaparecido, fuera del inventario: {gone}")
    plans = _load_ui_plans(ws)
    if plans:
        ui_files = design.write_ui(plans, answers, out)
        files += ui_files
        tokens = _load_tokens(ws, plans)
        design_md = ws / "DESIGN.md"
        design_md.write_text(design.build_design_md(plans, tokens, answers), encoding="utf-8")
        ok(f"{len(ui_files)} pantallas generadas desde las capturas de UI")
        print(f"  {DIM}sistema de diseno -> {design_md}{RST}")
        mem.set_ui(tokens, plans)
        for pl in plans:
            mem.remember(f"Pantalla '{pl['name']}' implementada desde la captura "
                         f"({len(pl['components'])} componentes detectados).",
                         kind="decision", source="build")
        mem.save()
    for f in files:
        try:
            rel = str(f.relative_to(ws))
        except ValueError:
            continue
        mem.track_file(rel, role=_role_of(f), summary="generado por build")
    mem.set_project(name=answers.app_name, package_id=answers.package_id,
                    stack=answers.stack, source_package=package)
    mem.remember(f"Proyecto generado en app/{answers.stack}: package "
                 f"{answers.package_id}, nombre '{answers.app_name}', backend "
                 f"{answers.backend}.", kind="decision", source="build",
                 key="project_generated")
    mem.write_status()
    ok(f"{len(files)} archivos generados en {out}")
    for f in files[:8]:
        print(f"  {DIM}{f.relative_to(ws)}{RST}")
    if len(files) > 8:
        print(f"  {DIM}… y {len(files)-8} mas{RST}")
    print("\nAbre NEXT_STEPS.md en ese directorio.")
    if getattr(args, "verify", False):
        info("verificando el proyecto generado")
        res = verify_mod.verify(out)
        print(res.summary(limit=15))
        for n in res.notes:
            print(f"  {DIM}· {n}{RST}")
        verify_mod.save_report(out, res)
        if not res.ok:
            warn("el proyecto generado tiene errores; corrige con: "
                 f"rea-termux verify {ws} --fix")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    """Pipeline completo: analyze -> (ui) -> plan -> build."""
    cfg = _cfg(args)
    apk = Path(args.apk).expanduser()
    code = cmd_analyze(argparse.Namespace(
        apk=str(apk), out=args.out, package=None, no_agent=args.no_agent,
        fast=args.fast, turns=args.turns, quiet=True,
        continue_on_error=False,
        config=getattr(args, "config", None),
    ))
    if code != 0:
        return code
    ws = Path(args.out).expanduser().resolve() if args.out else _detect_last_ws(cfg, apk)
    if not ws or not (ws / "analysis" / "ANALYSIS.md").is_file():
        err("no pude localizar el workspace recien creado")
        return 2

    if args.screens:
        info("Paso extra · captura de UI del original")
        cmd_ui(argparse.Namespace(
            workspace=str(ws), screens=args.screens, count=3, wait=True, list=False,
            config=getattr(args, "config", None),
        ))

    code = cmd_plan(argparse.Namespace(
        workspace=str(ws), answers=args.answers, package=None,
        force=args.force, config=getattr(args, "config", None),
    ))
    if code != 0:
        return code
    code = cmd_build(argparse.Namespace(
        workspace=str(ws), stack=args.stack, force=True,
        config=getattr(args, "config", None),
    ))
    if code != 0:
        return code
    print(f"\n{DIM}Chat con el agente para seguir mejorando:{RST}\n"
          f"  rea-termux chat {ws}\n")
    return 0


_ROLE_BY_EXT = {
    ".kt": "codigo Kotlin", ".dart": "codigo Flutter", ".tsx": "componente React",
    ".ts": "codigo TypeScript", ".xml": "recurso/manifest", ".kts": "build Gradle",
    ".yaml": "config/build", ".yml": "CI", ".json": "config", ".md": "documentacion",
}


def _role_of(p: Path) -> str:
    return _ROLE_BY_EXT.get(p.suffix.lower(), "archivo")


def cmd_probe(args: argparse.Namespace) -> int:
    """Valida los supuestos contra el REA real y guarda el informe."""
    cfg = _cfg(args)
    rep = probe_mod.probe(cfg, apk=args.apk, echo=print, deep=not args.fast)
    probe_mod.print_report(rep, echo=print)
    p = probe_mod.save(rep)
    print(f"{DIM}informe -> {p}{RST}")
    if args.json:
        print(json.dumps(rep.to_dict(), indent=2, ensure_ascii=False))
    aliases = rep.aliases
    if aliases:
        print(f"{DIM}alias en uso: " +
              ", ".join(f"{k}={v}" for k, v in aliases.items()) + RST)
    return 0 if rep.ok else 2


def _load_ui_plans(ws: Path) -> list[dict[str, Any]]:
    plans: list[dict[str, Any]] = []
    for name in uicapture.list_screens(ws):
        spec = uicapture.load_spec(ws, name)
        if spec:
            plans.append(design.screen_plan(spec))
    return plans


def _load_tokens(ws: Path, plans: list[dict[str, Any]]) -> dict[str, Any]:
    tp = ws / "ui" / "tokens.json"
    if tp.is_file():
        try:
            data = json.loads(tp.read_text(encoding="utf-8"))
            if data.get("colors"):
                return data
        except (OSError, json.JSONDecodeError):
            pass
    # agrega las paletas de todas las capturas
    totals: dict[str, dict[str, Any]] = {}
    for pl in plans:
        for c in pl.get("palette") or []:
            e = totals.setdefault(c["hex"], {"hex": c["hex"], "rgb": c["rgb"],
                                             "share": 0.0, "luminance": c.get("luminance", 0)})
            e["share"] = round(e["share"] + float(c.get("share") or 0), 4)
    colors = sorted(totals.values(), key=lambda c: c["share"], reverse=True)[:8]
    try:
        from .pngpalette import suggest_theme
        theme = suggest_theme(colors)
    except Exception:
        theme = plans[0].get("colors", {}) if plans else {}
    return {"colors": colors, "theme": theme}


def _detect_last_ws(cfg: Config, apk: Path) -> Path | None:
    cands = sorted(
        (p for p in cfg.ws().glob(f"{apk.stem}-*") if (p / "analysis").is_dir()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return cands[0] if cands else None


def cmd_ask(args: argparse.Namespace) -> int:
    cfg = _cfg(args)
    ws = Path(args.workspace).expanduser().resolve()
    analysis = ws / "analysis" if (ws / "analysis").is_dir() else ws
    router = _router(cfg)
    try:
        with _mcp(cfg, analysis) as mcp:
            agent = Agent(cfg, router, mcp, out_dir=analysis)
            ctx = summary_for_prompt(analysis) if (analysis / "ANALYSIS.md").is_file() else ""
            run = agent.run(args.question, max_turns=args.turns, context=ctx)
            if run.error:
                warn(run.error)
                return 1
            return 0
    finally:
        router.close()


def cmd_report(args: argparse.Namespace) -> int:
    ws = Path(args.workspace).expanduser().resolve()
    analysis = _find_analysis_dir(ws)
    meta = json.loads((ws / "WORKSPACE.json").read_text(encoding="utf-8")) if (ws / "WORKSPACE.json").is_file() else {}
    notes = (analysis / "AGENT_NOTES.md").read_text(encoding="utf-8") if (analysis / "AGENT_NOTES.md").is_file() else ""
    arts = {k: p for k, p in {
        "inspect": analysis / "evidence" / "rea.inspect_android_package.json",
        "signature": analysis / "evidence" / "rea.inspect_apk_signature.json",
    }.items() if p.is_file()}
    out = build_analysis(analysis, package=meta.get("package", ws.name),
                         apk_name=Path(meta.get("apk", "app.apk")).name,
                         agent_notes=notes, artifacts=arts)
    ok(f"informe regenerado -> {out}")
    return 0


def cmd_ui(args: argparse.Namespace) -> int:
    """Captura pantallas y, opcionalmente, las interpreta con vision multimodal."""
    cfg = _cfg(args)
    ws = Path(args.workspace).expanduser().resolve()
    if not ws.is_dir():
        err(f"no existe el workspace: {ws}")
        return 2
    mem = ProjectMemory.load(ws)

    if args.list:
        names = [s_.rsplit("/", 1)[-1] for s_ in uicapture.list_screens(ws)]
        print("\n".join(names) or "(sin capturas)")
        return 0
    if not uicapture.adb_path():
        err("adb no instalado: pkg install android-tools")
        return 2
    devs = uicapture.devices()
    if not devs:
        err("ningun dispositivo adb autorizado. Activa depuracion USB o "
            "`adb pair` / `adb connect <ip>:<puerto>` para inalambrica.")
        return 2
    serial = args.serial or devs[0]
    if serial not in devs:
        err(f"dispositivo no conectado/autorizado: {serial}")
        return 2

    screens = [s_.strip() for s_ in (args.screens or "").split(",") if s_.strip()]
    if not screens:
        screens = [f"pantalla-{i}" for i in range(1, min(args.count, 8) + 1)]
    info(f"capturando {len(screens)} pantalla(s). Navega en el telefono cuando se indique.")
    if args.vision:
        warn("--vision enviara cada captura al proveedor multimodal configurado; evita datos personales.")

    router = None
    if args.vision:
        try:
            router = _router(cfg)
        except SystemExit as exc:
            warn(f"vision IA desactivada: {exc}")
        except Exception as exc:
            warn(f"vision IA desactivada: {type(exc).__name__}")

    rea_mcp = _mcp(cfg, ws / "_mcp_ui") if args.vision else None
    results = []
    try:
        for i, name in enumerate(screens, 1):
            if args.wait:
                try:
                    input(f"  [{i}/{len(screens)}] deja el telefono en la pantalla "
                          f"'{name}' y pulsa Enter... ")
                except EOFError:
                    pass
            image = None
            image_source = "adb local"
            if args.vision and rea_mcp:
                from .vision import capture_from_rea
                image, source = capture_from_rea(cfg, serial=serial, mcp=rea_mcp)
                if image:
                    image_source = source
                elif i == 1:
                    warn(source)
            res = uicapture.capture(ws, name, serial=serial,
                                    screenshot_bytes=image, screenshot_source=image_source)
            results.append(res)
            if res.get("ok"):
                ok(f"{name}: {res['nodes']} nodos · {res['size']} · "
                   f"tema {res.get('theme', {}).get('primary')} · captura {res.get('screenshot_source')}")
                print(f"  {DIM}{res['png']} · {res['wireframe']}{RST}")
                if res.get("texts"):
                    print(f"  {DIM}textos: {', '.join(t for t in res['texts'][:8] if t)}{RST}")
                if args.vision:
                    if router:
                        from .vision import analyze_screenshot
                        analysis = analyze_screenshot(router, ws / res["png"], name)
                        if analysis.data is not None:
                            spec_path = ws / res["spec"]
                            spec = json.loads(spec_path.read_text(encoding="utf-8"))
                            spec["vision"] = analysis.data
                            spec["vision_meta"] = {"provider": analysis.provider,
                                                    "model": analysis.model,
                                                    "confidence": analysis.data.get("confidence")}
                            spec_path.write_text(json.dumps(spec, indent=2, ensure_ascii=False),
                                                 encoding="utf-8")
                            ok(f"vision: {analysis.provider}/{analysis.model} · "
                               f"confianza {analysis.data.get('confidence')}")
                        else:
                            warn("vision sin resultado validado: " + "; ".join(analysis.errors[:3]))
                    else:
                        warn("sin análisis visual IA; la captura y el análisis local se conservaron")
                mem.remember(f"Capturada la pantalla '{name}' con {res['nodes']} nodos.",
                             kind="evidence", source="ui")
            else:
                err(f"{name}: {res.get('error')}")
            time.sleep(0.4)
    finally:
        if rea_mcp:
            rea_mcp.close()
        if router:
            router.close()

    plans = _load_ui_plans(ws)
    if plans:
        tokens = _load_tokens(ws, plans)
        design.save_tokens(ws, tokens)
        md = ws / "DESIGN.md"
        md.write_text(design.build_design_md(plans, tokens), encoding="utf-8")
        ok(f"sistema de diseno -> {md} ({len(plans)} pantallas)")
        mem.set_ui(tokens, plans)
        mem.save()
        mem.write_status()
    return 0 if any(r.get("ok") for r in results) else 1


def cmd_chat(args: argparse.Namespace) -> int:
    """Sesion conversacional persistente: construye y mejora la app."""
    from .session import build_session

    cfg = _cfg(args)
    ws = Path(args.workspace).expanduser().resolve()
    if not ws.is_dir():
        err(f"no existe el workspace: {ws} (corre `analyze` primero)")
        return 2
    if not list(ws.glob("app/*")):
        warn("aun no hay proyecto generado; puedes pedirle al agente que lo cree")

    router = _router(cfg)
    session = None
    try:
        session = build_session(cfg, router, ws, session_id=args.session,
                                with_mcp=not args.no_mcp)
        session.use_vcs = not args.no_vcs
        session.dry_run = args.dry_run
        session.stream = not args.no_stream
        if session.devtools:
            session.devtools.dry_run = args.dry_run
        if args.dry_run:
            info("modo dry-run: el agente propone cambios (diff) y no escribe")
        if args.message:
            # modo one-shot: encadena varios mensajes
            for msg in args.message:
                print(f"\n{BOLD}tu>{RST} {msg}")
                session.turn(msg, max_turns=args.turns)
            return 0
        if not sys.stdin.isatty() and not args.message:
            err("sin terminal interactiva: usa --message 'lo que quieras'")
            return 2
        session.shell()
        return 0
    finally:
        if session and session.mcp:
            session.mcp.close()
        router.close()


def cmd_verify(args: argparse.Namespace) -> int:
    """Comprueba que el proyecto compila; con --fix, deja al agente corregir."""
    cfg = _cfg(args)
    ws = Path(args.workspace).expanduser().resolve()
    if not ws.is_dir():
        err(f"no existe el workspace: {ws}")
        return 2
    target = ws
    if args.project:
        target = (ws / args.project).resolve()
        if not target.is_dir():
            err(f"no existe el proyecto: {target}")
            return 2

    info(f"verificando {target} (stack detectado: {verify_mod.detect_stack(target)})")
    res = verify_mod.verify(target, stack=args.stack,
                            use_toolchain=not args.static_only)
    print(res.summary(limit=30))
    for n in res.notes:
        print(f"  {DIM}· {n}{RST}")
    verify_mod.save_report(target, res)
    exit_code = 0 if res.ok else 1

    if not res.ok and args.fix:
        router = _router(cfg)
        session = None
        try:
            from .session import build_session
            session = build_session(cfg, router, ws, session_id=None, with_mcp=False)
            session.use_vcs = not args.no_vcs
            heal = verify_mod.SelfHeal(session, max_rounds=args.rounds or
                                       cfg.agent.self_heal_rounds)
            out = heal.run()
            print()
            ok(f"auto-reparacion: {'OK' if out['ok'] else 'sin exito'} "
               f"en {out['rounds']} ronda(s)")
            verify_mod.save_report(target, verify_mod.verify(target, stack=args.stack),
                                   out.get("history"))
            exit_code = 0 if out["ok"] else 1
        finally:
            if session and session.mcp:
                session.mcp.close()
            router.close()
    return exit_code


def cmd_vcs(args: argparse.Namespace) -> int:
    """init | log | diff | undo | restore sobre el workspace."""
    ws = Path(args.workspace).expanduser().resolve()
    if not ws.is_dir():
        err(f"no existe el workspace: {ws}")
        return 2
    if not vcs.available():
        err("git no instalado (Termux: pkg install git)")
        return 2

    if args.action == "init":
        res = vcs.init(ws, allow_existing=args.adopt)
        (ok if res.get("ok") else err)(res.get("reason", ""))
        return 0 if res.get("ok") else 2
    if args.action == "log":
        commits = vcs.log(ws, limit=args.limit)
        if not commits:
            warn("sin commits (corre `rea-termux vcs init <ws>`)")
            return 0
        for c in commits:
            print(f"{c.short()}  {c.when}  {c.subject}")
        return 0
    if args.action == "diff":
        d = vcs.diff(ws, since=args.since, stat=args.stat)
        print(d[:20000] or "(sin cambios)")
        return 0
    if args.action == "undo":
        res = vcs.undo(ws, steps=args.steps)
        if not res.get("ok"):
            err(res.get("reason", "no se pudo deshacer"))
            return 2
        ok(f"deshecho → {res.get('to')} ({res.get('what')})")
        return 0
    if args.action == "restore":
        res = vcs.restore(ws, args.commit, args.paths or None)
        (ok if res.get("ok") else err)(res.get("detail") or "restaurado")
        return 0 if res.get("ok") else 2
    if args.action == "apply":
        mem = ProjectMemory.load(ws)
        dev = DevTools(ws, mem)
        pending = ws / ".rea" / "pending.json"
        if not pending.is_file():
            err("no hay propuestas pendientes (.rea/pending.json)")
            return 2
        data = json.loads(pending.read_text(encoding="utf-8"))
        dev.pending = data
        done = dev.apply_pending()
        ok("aplicado: " + ", ".join(done)) if done else warn("nada que aplicar")
        return 0 if done else 2
    return 2


def cmd_status(args: argparse.Namespace) -> int:
    """Muestra la memoria del proyecto y reescribe PROJECT_STATUS.md."""
    ws = Path(args.workspace).expanduser().resolve()
    if not ws.is_dir():
        err(f"no existe el workspace: {ws}")
        return 2
    mem = ProjectMemory.load(ws)
    p = mem.write_status()
    print(mem.status_markdown())
    print(f"{DIM}(escrito en {p}){RST}")
    return 0


def cmd_gui(args: argparse.Namespace) -> int:
    """Abre el panel web local para ejecutar el flujo sin encadenar comandos."""
    from .gui import run_gui
    return run_gui(config_path=getattr(args, "config", None),
                   port=getattr(args, "port", 8765),
                   open_browser=not getattr(args, "no_open", False))


# --------------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="rea-termux",
        description="Analiza y reimplementa apps Android desde Termux con REA (MCP) "
                    "y un proveedor compatible con OpenAI.",
    )
    p.add_argument("--version", action="version", version=f"rea-termux {__version__}")
    p.add_argument("--config", help="ruta al TOML de configuracion")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="crea la configuracion por defecto")
    s.add_argument("--force", action="store_true")
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("doctor", help="comprueba entorno, dependencias y llaves")
    s.add_argument("--probe", action="store_true", help="prueba credenciales de cada proveedor")
    s.add_argument("--probe-rea", action="store_true", help="valida supuestos contra REA")
    s.add_argument("--apk", default=None, help="APK de prueba para --probe-rea")
    s.set_defaults(func=cmd_doctor)

    s = sub.add_parser("probe", help="valida supuestos contra el REA real")
    s.add_argument("--apk", default=None, help="APK de prueba para el CLI")
    s.add_argument("--fast", action="store_true", help="sin handshake MCP")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_probe)

    s = sub.add_parser("models", help="lista/valida proveedores")
    s.set_defaults(func=cmd_models)

    s = sub.add_parser("tools", help="muestra las herramientas MCP de REA")
    s.add_argument("--all", action="store_true", help="sin recorte (catalogo completo)")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_tools)

    s = sub.add_parser("declare", help="registra que eres titular o tienes permiso")
    s.add_argument("apk")
    s.add_argument("--clone", action="store_true",
                   help="atestiguo que reimplemento solo la funcionalidad, con "
                        "identidad y recursos propios")
    s.add_argument("--own", action="store_true", help="alias de --clone")
    s.add_argument("--basis", default="", help="base legal / detalle")
    s.add_argument("--package", default=None)
    s.add_argument("--mode", default="analisis+reimplementacion")
    s.add_argument("--out", default=None)
    s.set_defaults(func=cmd_declare)

    s = sub.add_parser("analyze", help="descompila y analiza un APK")
    s.add_argument("apk")
    s.add_argument("--out", default=None, help="directorio de trabajo")
    s.add_argument("--package", default=None)
    s.add_argument("--no-agent", action="store_true", help="solo CLI de REA, sin IA")
    s.add_argument("--fast", action="store_true", help="sin --full-output (mas rapido)")
    s.add_argument("--turns", type=int, default=None)
    s.add_argument("--quiet", action="store_true")
    s.add_argument("--with-signature", action="store_true",
                   help="intenta el comando de firma aparte de REA (experimental)")
    s.add_argument("--continue-on-error", action="store_true",
                   help="sigue aunque la descompilacion falle")
    s.add_argument("--diff", default=None, metavar="OTRO.apk",
                   help="compara el APK actual con otro (endpoints, permisos, SDK, licencias)")
    s.add_argument("--no-apktool", action="store_true",
                   help="omite la decodificacion local de recursos con apktool")
    s.add_argument("--no-vcs", action="store_true", help="no crear repo git")
    s.add_argument("--no-stream", action="store_true", help="sin streaming")
    s.set_defaults(func=cmd_analyze)

    s = sub.add_parser("plan", help="entrevista: define como quieres tu version")
    s.add_argument("workspace")
    s.add_argument("--answers", help="JSON con respuestas (modo no interactivo)")
    s.add_argument("--package", default=None)
    s.add_argument("--force", action="store_true")
    s.add_argument("--structured", action="store_true",
                   help="propone pantallas/modelo de datos con salida JSON validada")
    s.set_defaults(func=cmd_plan)

    s = sub.add_parser("build", help="genera el esqueleto del proyecto")
    s.add_argument("workspace")
    s.add_argument("--stack", choices=["android-kotlin", "flutter", "react-native", "web-pwa"])
    s.add_argument("--force", action="store_true")
    s.add_argument("--verify", action="store_true", help="verificar al terminar")
    s.set_defaults(func=cmd_build)

    s = sub.add_parser("run", help="pipeline completo: analyze + plan + build")
    s.add_argument("apk")
    s.add_argument("--out", default=None)
    s.add_argument("--answers", default=None)
    s.add_argument("--stack", default=None,
                   choices=["android-kotlin", "flutter", "react-native", "web-pwa"])
    s.add_argument("--no-agent", action="store_true")
    s.add_argument("--fast", action="store_true")
    s.add_argument("--turns", type=int, default=None)
    s.add_argument("--screens", default=None,
                   help="tras analizar, captura estas pantallas por adb (coma)")
    s.add_argument("--force", action="store_true")
    s.set_defaults(func=cmd_run)

    s = sub.add_parser("ask", help="pregunta libre al agente sobre un workspace")
    s.add_argument("workspace")
    s.add_argument("question")
    s.add_argument("--turns", type=int, default=20)
    s.add_argument("--no-stream", action="store_true")
    s.set_defaults(func=cmd_ask)

    s = sub.add_parser("ui", help="captura pantallas del telefono (adb) y genera DESIGN.md")
    s.add_argument("workspace")
    s.add_argument("--screens", default="", help="nombres separados por coma (home,login,...)")
    s.add_argument("--count", type=int, default=3, help="cuantas capturar si no das nombres")
    s.add_argument("--wait", action="store_true", help="pausa para que navegues entre capturas")
    s.add_argument("--list", action="store_true", help="lista capturas existentes")
    s.add_argument("--vision", action="store_true", help="REA captura primero; proveedor multimodal analiza la imagen (requiere supports_vision = true)")
    s.add_argument("--serial", default=None, help="serial adb si hay varios dispositivos")
    s.set_defaults(func=cmd_ui)

    s = sub.add_parser("chat", help="conversacion persistente para construir/mejorar la app")
    s.add_argument("workspace")
    s.add_argument("--session", default=None, help="id de sesion a reanudar")
    s.add_argument("--message", action="append", help="mensaje one-shot (repetible)")
    s.add_argument("--turns", type=int, default=None)
    s.add_argument("--no-mcp", action="store_true", help="sin servidor REA (solo archivos)")
    s.add_argument("--dry-run", action="store_true",
                   help="no escribe: el agente propone cambios con diff")
    s.add_argument("--no-vcs", action="store_true",
                   help="no inicializa ni usa git en el workspace")
    s.add_argument("--no-stream", action="store_true",
                   help="desactiva el streaming (respuestas de golpe)")
    s.set_defaults(func=cmd_chat)

    s = sub.add_parser("verify", help="comprueba que el proyecto compila (--fix corrige)")
    s.add_argument("workspace")
    s.add_argument("--project", default=None,
                   help="subdirectorio del proyecto (p.ej. app/flutter)")
    s.add_argument("--stack", default=None,
                   choices=["flutter", "android-kotlin", "react-native", "web-pwa"])
    s.add_argument("--fix", action="store_true", help="pide al agente que corrija")
    s.add_argument("--rounds", type=int, default=None)
    s.add_argument("--static-only", action="store_true",
                   help="sin toolchain: solo chequeos estaticos")
    s.add_argument("--no-vcs", action="store_true")
    s.set_defaults(func=cmd_verify)

    s = sub.add_parser("vcs", help="control de versiones del workspace")
    s.add_argument("workspace")
    s.add_argument("action", choices=["init", "log", "diff", "undo", "restore", "apply"])
    s.add_argument("--adopt", action="store_true", help="adoptar un repo existente")
    s.add_argument("--limit", type=int, default=15)
    s.add_argument("--since", default=None)
    s.add_argument("--stat", action="store_true")
    s.add_argument("--steps", type=int, default=1)
    s.add_argument("--commit", default="HEAD")
    s.add_argument("--paths", nargs="*", default=None)
    s.set_defaults(func=cmd_vcs)

    s = sub.add_parser("status", help="estado y memoria del proyecto")
    s.add_argument("workspace")
    s.set_defaults(func=cmd_status)

    s = sub.add_parser("gui", help="abre el panel gráfico local en el navegador")
    s.add_argument("--port", type=int, default=8765, help="puerto local (0 = elegir uno libre)")
    s.add_argument("--no-open", action="store_true", help="no abrir el navegador automáticamente")
    s.set_defaults(func=cmd_gui)

    s = sub.add_parser("report", help="regenera ANALYSIS.md desde los artefactos")
    s.add_argument("workspace")
    s.set_defaults(func=cmd_report)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        err("interrumpido")
        return 130
    except AllProvidersFailed as exc:
        err(str(exc))
        return 4
    except PolicyError as exc:
        err(str(exc))
        return 3
    except SystemExit as exc:
        if isinstance(exc.code, str):
            err(exc.code)
            return 2
        return int(exc.code or 0)
