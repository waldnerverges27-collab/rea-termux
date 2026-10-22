"""rea-termux: agente CLI para Termux que opera el MCP de REA (rea-agents).

Componentes:
  config     -> carga/crea la configuracion en TOML
  providers  -> cliente OpenAI-compatible con cadena de fallback
  mcp_client -> cliente MCP sobre stdio (JSON-RPC 2.0, linea a linea)
  rea_cli    -> invocaciones deterministas del CLI de REA (evidencia cruda)
  agent      -> bucle de tool-calling contra el MCP
  phases     -> pipeline recon -> deep -> report -> interview -> scaffold
  policy     -> puerta de autorizacion (app propia o con permiso)
"""

__version__ = "0.1.0"
APP_NAME = "rea-termux"
