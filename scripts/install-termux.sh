#!/data/data/com.termux/files/usr/bin/bash
# Instalador remoto para: curl -fsSL URL | bash
# Descarga main, prepara Termux, compila el ejecutable y lo instala en $PREFIX/bin.
set -euo pipefail

REPO="waldnerverges27-collab/rea-termux"
BRANCH="main"
ARCHIVE_URL="https://github.com/${REPO}/archive/refs/heads/${BRANCH}.tar.gz"
INSTALL_ROOT="$HOME/.local/opt/rea-termux"
BIN_DIR="${PREFIX:-/data/data/com.termux/files/usr}/bin"

say() { printf '\033[1m»\033[0m %s\n' "$*"; }
die() { printf '\033[31m✗\033[0m %s\n' "$*" >&2; exit 1; }

if [[ -z "${PREFIX:-}" || "$PREFIX" != "/data/data/com.termux/files/usr" || ! -d "$PREFIX" ]] || ! command -v pkg >/dev/null 2>&1; then
  die "Este instalador requiere Termux nativo (no Linux, proot ni PC)."
fi

command -v curl >/dev/null 2>&1 || pkg install -y curl
tar_bin="$(command -v tar || true)"
if [[ -z "$tar_bin" ]]; then
  pkg install -y tar
  tar_bin="$(command -v tar || true)"
fi
[[ -n "$tar_bin" ]] || die "No se pudo instalar tar."

parent="$(dirname "$INSTALL_ROOT")"
mkdir -p "$parent" "$BIN_DIR"
stage="$(mktemp -d "$parent/.rea-termux-stage.XXXXXX")"
tmpdir="${TMPDIR:-$PREFIX/tmp}"
mkdir -p "$tmpdir"
archive=""
backup=""
installed=0
cleanup() {
  [[ -n "$archive" ]] && rm -f "$archive"
  if [[ -d "$stage" ]]; then rm -rf "$stage"; fi
  if [[ -n "$backup" && -d "$backup" && ! -d "$INSTALL_ROOT" ]]; then
    mv "$backup" "$INSTALL_ROOT"
  fi
  if [[ "$installed" -eq 0 ]]; then rm -f "$BIN_DIR/rea-termux.new"; fi
}
trap cleanup EXIT INT TERM
archive="$(mktemp "$tmpdir/rea-termux.XXXXXX.tar.gz")"

say "Descargando ${REPO}@${BRANCH} desde GitHub"
curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 \
  "$ARCHIVE_URL" -o "$archive"
# La extracción se limita a un directorio nuevo bajo el home de Termux.
"$tar_bin" -tzf "$archive" >/dev/null || die "El archivo descargado no es un tar.gz válido."
if "$tar_bin" -tzf "$archive" | grep -E '(^/|(^|/)\.\.(/|$))' >/dev/null; then
  die "El archivo contiene rutas de extracción no permitidas."
fi
"$tar_bin" -xzf "$archive" --strip-components=1 -C "$stage"
[[ -f "$stage/scripts/termux-bootstrap.sh" && -f "$stage/scripts/build-termux-binary.sh" ]] \
  || die "El repo no contiene los scripts de instalación/compilación esperados."

say "Preparando dependencias y herramientas Android"
bash "$stage/scripts/termux-bootstrap.sh"

say "Compilando rea-termux para esta arquitectura Termux"
bash "$stage/scripts/build-termux-binary.sh"
[[ -x "$stage/dist/rea-termux" ]] || die "No se generó dist/rea-termux."

say "Instalando el ejecutable en $BIN_DIR"
if [[ -e "$BIN_DIR/rea-termux" && ! -f "$BIN_DIR/rea-termux" ]]; then
  die "$BIN_DIR/rea-termux existe y no es un archivo normal."
fi
cp "$stage/dist/rea-termux" "$BIN_DIR/rea-termux.new"
chmod 755 "$BIN_DIR/rea-termux.new"
"$BIN_DIR/rea-termux.new" --version >/dev/null
if [[ -e "$INSTALL_ROOT" ]]; then
  backup="${INSTALL_ROOT}.previous.$(date +%Y%m%d%H%M%S)"
  mv "$INSTALL_ROOT" "$backup"
fi
if ! mv "$stage" "$INSTALL_ROOT"; then
  [[ -n "$backup" && -d "$backup" ]] && mv "$backup" "$INSTALL_ROOT"
  die "No se pudo activar el código descargado."
fi
if ! mv "$BIN_DIR/rea-termux.new" "$BIN_DIR/rea-termux"; then
  rm -rf "$INSTALL_ROOT"
  [[ -n "$backup" && -d "$backup" ]] && mv "$backup" "$INSTALL_ROOT"
  die "No se pudo instalar el ejecutable en $BIN_DIR."
fi
installed=1

say "Instalación terminada"
printf 'Ejecutable: %s/rea-termux\n' "$BIN_DIR"
printf 'Código fuente: %s\n' "$INSTALL_ROOT"
printf '\nSiguientes pasos:\n  rea-termux init\n'
printf 'Configura las URLs/modelos reales y las variables API_KEY que usarás.\n'
printf 'Después abre el panel con:\n  rea-termux gui\n'
if [[ -n "$backup" ]]; then
  printf 'Versión anterior conservada en: %s\n' "$backup"
fi
