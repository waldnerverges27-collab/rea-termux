"""Configuracion de rea-termux.

Orden de busqueda del archivo de config:
  1. $REA_TERMUX_CONFIG
  2. ./rea-termux.toml
  3. $XDG_CONFIG_HOME/rea-termux/config.toml   (~/.config/rea-termux/config.toml)

Secretos NUNCA van en el TOML: se leen de variables de entorno
(api_key_env), para que el archivo se pueda compartir sin filtrar llaves.
"""
from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

APP = "rea-termux"


# --------------------------------------------------------------------------
# rutas
# --------------------------------------------------------------------------
def config_home() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / APP


def data_home() -> Path:
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / APP


def config_path() -> Path:
    env = os.environ.get("REA_TERMUX_CONFIG")
    if env:
        return Path(env).expanduser()
    local = Path.cwd() / "rea-termux.toml"
    if local.is_file():
        return local
    return config_home() / "config.toml"


def expand(path: str | Path) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(str(path))))


# --------------------------------------------------------------------------
# modelos de config
# --------------------------------------------------------------------------
@dataclass
class ProviderCfg:
    """Un endpoint compatible con la API de OpenAI."""

    name: str
    base_url: str = ""
    model: str = ""
    api_key_env: str = ""
    priority: int = 100
    supports_tools: bool = True
    supports_stream: bool = True
    #: capacidad multimodal para recibir imagenes; debe activarse segun el modelo real
    supports_vision: bool = False
    #: capacidad de salida estructurada; se degrada sola si el proveedor la rechaza
    structured_json_schema: bool = True
    structured_json_object: bool = True
    temperature: float = 0.2
    max_tokens: int = 8192
    timeout_s: float = 180.0
    extra_headers: dict[str, str] = field(default_factory=dict)
    #: si se define, se usa como ruta completa del endpoint chat/completions
    chat_path: str = "/chat/completions"
    #: reintentos por proveedor antes de degradar al siguiente
    retries: int = 1

    def resolve_key(self) -> str | None:
        if not self.api_key_env:
            return None
        v = os.environ.get(self.api_key_env, "").strip()
        return v or None

    def endpoint(self) -> str:
        base = self.base_url.rstrip("/")
        # tolera base_url que ya termina en /chat/completions
        if base.endswith("/chat/completions"):
            return base
        return base + self.chat_path


@dataclass
class MCPServerCfg:
    """Como lanzar el servidor MCP (REA por defecto, por stdio)."""

    command: str = "npx"
    args: list[str] = field(
        default_factory=lambda: ["-y", "rea-agents@6.7.0", "mcp"]
    )
    env: dict[str, str] = field(default_factory=dict)
    cwd: str = ""
    startup_timeout_s: float = 120.0
    call_timeout_s: float = 600.0
    #: limita cuantas herramientas se exponen al modelo (REA trae ~200)
    max_tools: int = 48
    #: patrones (substring) que deben quedar SIEMPRE
    always_include: list[str] = field(
        default_factory=lambda: [
            "inspect_android_package",
            "search_android_classes",
            "inspect_android_class",
            "inspect_android_method",
            "trace_android_references",
            "inspect_apk_signature",
        ]
    )
    include_patterns: list[str] = field(default_factory=list)
    exclude_patterns: list[str] = field(default_factory=list)


@dataclass
class AgentCfg:
    max_turns: int = 40
    #: permite que el agente ejecute comandos de shell dentro del workspace
    allow_command: bool = True
    command_timeout_s: float = 180.0
    #: verificar tras cada turno del chat (solo informa)
    verify_after_turn: bool = False
    #: rondas de auto-reparacion en `verify --fix`
    self_heal_rounds: int = 3
    #: caracteres maximos de un resultado de herramienta que ve el modelo
    max_tool_chars: int = 14000
    #: presupuesto de caracteres del historial enviado al modelo
    history_char_budget: int = 220_000
    auto_approve: bool = True
    #: herramientas que siempre piden confirmacion (substring)
    confirm_tools: list[str] = field(default_factory=list)
    system_prompt: str = ""


@dataclass
class PolicyCfg:
    """Guardarrailes de propiedad intelectual al reimplementar."""

    #: el usuario atestiguo que no reutilizara codigo, assets ni marca
    attested: bool = False
    legal_basis: str = ""
    #: se permite clonar la funcionalidad de CUALQUIER app (modo replicate)
    allow_any_app: bool = True
    #: sustituir nombre/identificadores si el paquete es de una marca vigilada
    rename_branded: bool = True
    #: paquetes de marcas conocidas: la funcionalidad se puede clonar, pero la
    #: identidad visual/marca no se replica
    blocked_packages: list[str] = field(
        default_factory=lambda: [
            "com.facebook",
            "com.whatsapp",
            "com.instagram",
            "com.google",
            "com.android.chrome",
            "com.spotify",
            "com.netflix",
            "com.amazon",
            "com.microsoft",
            "com.tiktok",
            "com.zhiliaoapp",
            "com.tencent",
            "com.snapchat",
            "com.twitter",
            "com.uber",
            "com.airbnb",
            "org.telegram",
            "com.discord",
            "com.paypal",
            "com.coinbase",
        ]
    )


@dataclass
class Config:
    providers: list[ProviderCfg] = field(default_factory=list)
    mcp: MCPServerCfg = field(default_factory=MCPServerCfg)
    agent: AgentCfg = field(default_factory=AgentCfg)
    policy: PolicyCfg = field(default_factory=PolicyCfg)
    workspace: str = "~/rea-workspace"
    rea_version: str = "6.7.0"

    # -- utilidades --------------------------------------------------------
    def provider_chain(self) -> list[ProviderCfg]:
        """Proveedores usables, en orden de prioridad."""
        usable = [p for p in self.providers if p.base_url and p.model]
        return sorted(usable, key=lambda p: p.priority)

    def ws(self) -> Path:
        p = expand(self.workspace)
        p.mkdir(parents=True, exist_ok=True)
        return p

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


DEFAULT_TOML = """\
# rea-termux · configuracion
# Llaves de API: SIEMPRE por variable de entorno, nunca aqui.
#
#   export NINEROUTER_API_KEY="sk-..."
#   export OMNIROUTE_API_KEY="sk-..."
#
# Los proveedores se intentan en orden de `priority` (menor = primero).
# Si uno falla con 429/5xx/timeout, se pasa automaticamente al siguiente.

workspace = "~/rea-workspace"
rea_version = "6.7.0"

# ---------------------------------------------------------------- proveedor 1
[[providers]]
name = "9router"
# TODO: pon aqui el base_url real de 9router (debe terminar en /v1 normalmente)
base_url = "https://9router.invalid/v1"
model = "gpt-4.1-mini"          # TODO: el id exacto que expone 9router
api_key_env = "NINEROUTER_API_KEY"
priority = 10
supports_tools = true
supports_stream = true
supports_vision = false # activa solo para modelos multimodales
structured_json_schema = true
structured_json_object = true
temperature = 0.2
max_tokens = 8192
timeout_s = 180
retries = 1
# extra_headers = { "X-Title" = "rea-termux" }

# ---------------------------------------------------------------- proveedor 2
[[providers]]
name = "omniroute"
base_url = "https://omniroute.invalid/v1"   # TODO
model = "gpt-4.1-mini"                      # TODO
api_key_env = "OMNIROUTE_API_KEY"
priority = 20
supports_tools = true
supports_stream = true
supports_vision = false # activa solo para modelos multimodales
structured_json_schema = true
structured_json_object = true
temperature = 0.2
max_tokens = 8192
timeout_s = 180
retries = 1

# ---------------------------------------------------------------------- MCP
[mcp]
command = "npx"
args = ["-y", "rea-agents@6.7.0", "mcp"]
startup_timeout_s = 120
call_timeout_s = 600
max_tools = 48
always_include = [
  "inspect_android_package",
  "search_android_classes",
  "inspect_android_class",
  "inspect_android_method",
  "trace_android_references",
  "inspect_apk_signature",
]
include_patterns = []
exclude_patterns = []

# -------------------------------------------------------------------- agente
[agent]
max_turns = 40
allow_command = true
command_timeout_s = 180
verify_after_turn = false
self_heal_rounds = 3
max_tool_chars = 14000
history_char_budget = 220000
auto_approve = true
confirm_tools = []

# -------------------------------------------------------------------- policy
[policy]
# atestiguas que reimplementas solo la funcionalidad, sin copiar codigo,
# recursos ni marca del original. Se activa con: rea-termux declare app.apk --clone
attested = false
legal_basis = ""
allow_any_app = true
rename_branded = true
"""


def _apply(dc: Any, data: dict[str, Any]) -> Any:
    """Asigna solo claves conocidas (ignora extras con aviso silencioso)."""
    for k, v in data.items():
        if hasattr(dc, k):
            setattr(dc, k, v)
    return dc


def load(path: str | Path | None = None) -> Config:
    p = Path(path).expanduser() if path else config_path()
    cfg = Config()
    if not p.is_file():
        return cfg
    raw = tomllib.loads(p.read_text(encoding="utf-8"))

    if "workspace" in raw:
        cfg.workspace = raw["workspace"]
    if "rea_version" in raw:
        cfg.rea_version = str(raw["rea_version"])

    providers: list[ProviderCfg] = []
    for item in raw.get("providers", []):
        if not isinstance(item, dict) or "name" not in item:
            continue
        providers.append(_apply(ProviderCfg(name=item["name"]), item))
    cfg.providers = providers

    if isinstance(raw.get("mcp"), dict):
        _apply(cfg.mcp, raw["mcp"])
    if isinstance(raw.get("agent"), dict):
        _apply(cfg.agent, raw["agent"])
    if isinstance(raw.get("policy"), dict):
        _apply(cfg.policy, raw["policy"])
    return cfg


def write_default(path: str | Path | None = None, force: bool = False) -> Path:
    p = Path(path).expanduser() if path else config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists() and not force:
        return p
    p.write_text(DEFAULT_TOML, encoding="utf-8")
    try:
        p.chmod(0o600)
    except OSError:
        pass
    return p


def dump_json(cfg: Config) -> str:
    """Config serializada, con llaves redactadas."""
    d = cfg.to_dict()
    for p in d.get("providers", []):
        p.pop("extra_headers", None)
    return json.dumps(d, indent=2, ensure_ascii=False)
