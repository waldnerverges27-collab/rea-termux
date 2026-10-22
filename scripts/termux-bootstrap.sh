#!/data/data/com.termux/files/usr/bin/bash
# ---------------------------------------------------------------------------
# termux-bootstrap.sh — prepara Termux para rea-termux
#   * paquetes base + Android Build/Platform Tools de Termux
#   * JADX headless (jar que REA usa para APKs) con verificacion sha256
#   * shims para lo que REA espera encontrar en PATH
#   * prefetch del paquete npm rea-agents (fijado por version)
#
# Uso:
#   bash scripts/termux-bootstrap.sh              # todo
#   bash scripts/termux-bootstrap.sh --jadx       # solo el jar de JADX
#   bash scripts/termux-bootstrap.sh --shims      # solo los wrappers
#   bash scripts/termux-bootstrap.sh --rea        # solo prefetch de rea-agents
#   bash scripts/termux-bootstrap.sh --android-tools # APKTool/aapt/apksigner/adb
#   bash scripts/termux-bootstrap.sh --binary      # empaqueta dist/rea-termux en Termux
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
  do_android_tools
  # JDK completo: JADX necesita jdk.compiler; OpenJDK 21 sirve para REA/JADX y Build Tools.
  pkg_install openjdk-21 || warn "OpenJDK 21 fallo; instala manualmente un JDK completo 17+"
  command -v java >/dev/null 2>&1 && java -version 2>&1 | head -2 || warn "java no disponible"
  say "python deps"
  python -m pip install --quiet --upgrade pip || true
  python -m pip install --quiet httpx || warn "no pude instalar httpx"
}

do_android_tools() {
  need_termux
  # aapt (Termux) integra varias Android Build Tools nativas (aapt2,
  # zipalign y aidl en las versiones actuales); doctor verifica cada binario.
  pkg_install aapt apktool apksigner android-tools
  # Gradle es auxiliar para compilar el proyecto nuevo; REA/JADX no lo requieren.
  pkg_install gradle || warn "Gradle es opcional; instala su toolchain segun el stack elegido"
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

  chmod +x "$bindir/jadx" "$bindir/rea"
  say "shims instalados en $bindir: jadx, rea"

  # No se reemplaza el aapt del SDK/Termux. Solo instalar un shim minimo si falta.
  if ! command -v aapt >/dev/null 2>&1; then
    cat > "$bindir/aapt" <<'EOF'
#!/usr/bin/env bash
# Fallback solo para obtener package id cuando Android Build Tools no existe.
if [ "$#" -ge 3 ] && [ "$1" = "dump" ] && [ "$2" = "badging" ]; then
  shift 2
  exec python - "$1" <<'PY'
import sys
sys.path.insert(0, __import__("os").environ.get("REA_TERMUX_PATH", ""))
try:
    from rea_termux.apkmeta import read_apk_meta
except ImportError:
    sys.exit("aapt shim: instala rea-termux o define REA_TERMUX_PATH")
meta = read_apk_meta(sys.argv[1])
if meta.get("package"):
    print(f"package: name='{meta['package']}'")
else:
    print("aapt shim: no pude leer el manifest", file=sys.stderr)
    sys.exit(1)
PY
fi
echo "aapt shim: solo implementa 'dump badging'; instala aapt para Android Build Tools" >&2
exit 1
EOF
    chmod +x "$bindir/aapt"
    say "shim aapt minimo instalado (solo fallback de package id)"
  else
    say "aapt real detectado; se conserva sin sobrescribirlo"
  fi

}

do_binary() {
  need_termux
  pkg_install python clang make pkg-config zlib
  local script_dir
  script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  bash "$script_dir/build-termux-binary.sh"
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
    --android-tools) do_android_tools ;;
    --binary) do_binary ;;
    "")      do_packages; do_jadx; do_shims; do_rea ;;
    *)       die "opcion desconocida: $only" ;;
  esac
  say "listo. Siguiente: rea-termux init && rea-termux doctor"
}

main "$@"
