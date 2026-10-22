#!/data/data/com.termux/files/usr/bin/bash
# ---------------------------------------------------------------------------
# termux-bootstrap.sh — prepara Termux para rea-termux
#   * paquetes base (nodejs, python, openjdk-17, procps, herramientas)
#   * JADX headless (jar que REA usa para APKs) con verificacion sha256
#   * shims para lo que REA espera encontrar en PATH
#   * prefetch del paquete npm rea-agents (fijado por version)
#
# Uso:
#   bash scripts/termux-bootstrap.sh              # todo
#   bash scripts/termux-bootstrap.sh --jadx       # solo el jar de JADX
#   bash scripts/termux-bootstrap.sh --shims      # solo los wrappers
#   bash scripts/termux-bootstrap.sh --rea        # solo prefetch de rea-agents
# ---------------------------------------------------------------------------
set -euo pipefail

REA_VERSION="${REA_VERSION:-6.7.0}"
JADX_VER="${JADX_VER:-0.7.1}"
JADX_JAR="jadx-headless-mcp-${JADX_VER}-all.jar"
JADX_URL="https://github.com/1013503897/jadx-headless-mcp/releases/download/v${JADX_VER}/${JADX_JAR}"
JADX_SHA256="${JADX_SHA256:-6e5eacf500b64292bfb73c49797c1958f6ee44646e43e868039ae7feb573ff75}"
REA_DATA="${HOME}/.local/share/rea/android"

say()  { printf '\033[1m»\033[0m %s\n' "$*"; }
warn() { printf '\033[33m!\033[0m %s\n' "$*"; }
die()  { printf '\033[31m✗\033[0m %s\n' "$*" >&2; exit 1; }

need_termux() {
  [ -d "/data/data/com.termux/files/usr" ] || warn "no parece Termux nativo; continuo igual"
}

pkg_install() {
  command -v pkg >/dev/null 2>&1 || { warn "sin 'pkg' (no es Termux); instala a mano: $*"; return 0; }
  say "pkg install $*"
  pkg install -y "$@" || warn "algunos paquetes fallaron: $*"
}

do_packages() {
  need_termux
  say "actualizando indice"
  pkg update -y >/dev/null 2>&1 || true
  pkg_install nodejs npm python procps unzip zip jq git
  # JDK completo: JADX necesita jdk.compiler (un JRE no sirve)
  pkg_install openjdk-17 || warn "openjdk-17 fallo; prueba: pkg install openjdk-21"
  command -v java >/dev/null 2>&1 && java -version 2>&1 | head -2 || warn "java no disponible"
  say "python deps"
  python -m pip install --quiet --upgrade pip || true
  python -m pip install --quiet httpx || warn "no pude instalar httpx"
}

do_jadx() {
  mkdir -p "$REA_DATA"
  cd "$REA_DATA"
  if [ -f "$JADX_JAR" ]; then
    say "JADX ya presente: $REA_DATA/$JADX_JAR"
  else
    say "descargando $JADX_JAR"
    if command -v curl >/dev/null 2>&1; then
      curl -fL --retry 3 -o "$JADX_JAR.part" "$JADX_URL"
    else
      pkg_install curl && curl -fL --retry 3 -o "$JADX_JAR.part" "$JADX_URL"
    fi
    mv "$JADX_JAR.part" "$JADX_JAR"
  fi
  if [ -n "$JADX_SHA256" ]; then
    say "verificando sha256"
    echo "${JADX_SHA256}  ${JADX_JAR}" | sha256sum -c - \
      || die "sha256 no coincide: no uses ese jar (posible descarga corrupta o manipulada)"
  else
    warn "sin sha256 configurado: verifica manualmente el jar"
  fi
  say "exporta esto en ~/.bashrc:"
  echo "    export REA_JADX_MCP_JAR=\"$REA_DATA/$JADX_JAR\""
  if ! grep -qs 'REA_JADX_MCP_JAR' "$HOME/.bashrc" 2>/dev/null; then
    printf '\nexport REA_JADX_MCP_JAR="%s"\n' "$REA_DATA/$JADX_JAR" >> "$HOME/.bashrc"
    say "anadido a ~/.bashrc"
  fi
  export REA_JADX_MCP_JAR="$REA_DATA/$JADX_JAR"
}

do_shims() {
  local bindir="${PREFIX:-/usr}/bin"
  [ -w "$bindir" ] || { warn "$bindir no escribible; salto shims"; return 0; }

  # 1) `jadx` = java -jar del jar headless
  cat > "$bindir/jadx" <<'EOF'
#!/usr/bin/env bash
JAR="${REA_JADX_MCP_JAR:-$HOME/.local/share/rea/android/jadx-headless-mcp-0.7.1-all.jar}"
[ -f "$JAR" ] || { echo "jadx: falta $JAR (corre scripts/termux-bootstrap.sh --jadx)" >&2; exit 1; }
exec java -jar "$JAR" "$@"
EOF

  # 2) `rea` = npx fijado al paquete oficial
  cat > "$bindir/rea" <<EOF
#!/usr/bin/env bash
exec npx -y rea-agents@${REA_VERSION} "\$@"
EOF

  # 3) `aapt` de reemplazo: package id sin Android SDK (parser AXML en python)
  cat > "$bindir/aapt" <<'EOF'
#!/usr/bin/env bash
# shim minimo: soporta `aapt dump badging <apk>` -> emite "package: name=..."
if [ "$1" = "dump" ] && [ "$2" = "badging" ]; then
  shift 2
  exec python - "$1" <<'PY'
import sys
sys.path.insert(0, __import__("os").environ.get("REA_TERMUX_PATH", ""))
try:
    from rea_termux.apkmeta import read_apk_meta
except ImportError:
    sys.exit("aapt shim: instala rea-termux (pip install -e .) o define REA_TERMUX_PATH")
meta = read_apk_meta(sys.argv[1])
if meta.get("package"):
    print(f"package: name='{meta['package']}'")
else:
    print("aapt shim: no pude leer el manifest", file=sys.stderr)
    sys.exit(1)
PY
fi
echo "aapt shim: solo implementa 'dump badging'" >&2
exit 1
EOF

  chmod +x "$bindir/jadx" "$bindir/rea" "$bindir/aapt"
  say "shims instalados en $bindir: jadx, rea, aapt"
}

do_rea() {
  command -v npx >/dev/null 2>&1 || die "falta npx: pkg install nodejs npm"
  say "prefetch rea-agents@${REA_VERSION} (puede tardar)"
  npx -y "rea-agents@${REA_VERSION}" --version || warn "el prefetch fallo; revisa la version existe en npm"
}

main() {
  local only="${1:-}"
  case "$only" in
    --jadx)  do_jadx ;;
    --shims) do_shims ;;
    --rea)   do_rea ;;
    "")      do_packages; do_jadx; do_shims; do_rea ;;
    *)       die "opcion desconocida: $only" ;;
  esac
  say "listo. Siguiente: rea-termux init && rea-termux doctor"
}

main "$@"
