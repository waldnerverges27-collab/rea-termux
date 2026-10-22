#!/data/data/com.termux/files/usr/bin/bash
# Construye un ejecutable PyInstaller nativo para el entorno Termux actual.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$ROOT/dist/rea-termux"

usage() {
  cat <<'EOF'
Uso: bash scripts/build-termux-binary.sh [--help]

En Termux compila rea-termux como ejecutable PyInstaller de un solo archivo:
  dist/rea-termux

La salida es específica para la arquitectura/ABI del Termux donde se compila.
Node/REA, Java/JADX y las herramientas Android siguen siendo dependencias externas.
EOF
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  usage
  exit 0
fi
if [[ $# -gt 0 ]]; then
  echo "opción desconocida: $1" >&2
  usage >&2
  exit 2
fi

if [[ -z "${PREFIX:-}" || ! -d "/data/data/com.termux/files/usr" || "$PREFIX" != "/data/data/com.termux/files/usr" ]]; then
  echo "Este empaquetado debe construirse dentro de Termux nativo (no Linux/proot/PC)." >&2
  exit 2
fi
for cmd in python clang make; do
  command -v "$cmd" >/dev/null 2>&1 || {
    echo "Falta $cmd. Instala el toolchain con: pkg install python clang make pkg-config zlib" >&2
    exit 2
  }
done

cd "$ROOT"
if ! python -c 'import PyInstaller' >/dev/null 2>&1; then
  echo "Instalando rea-termux y el extra de empaquetado (PyInstaller >= 6.20)…"
  python -m pip install --upgrade '.[freeze]'
fi

python - <<'PY'
import sys
import PyInstaller
version = tuple(int(part) for part in PyInstaller.__version__.split(".")[:2])
if version < (6, 20):
    raise SystemExit(f"PyInstaller {PyInstaller.__version__} es antiguo; se requiere >= 6.20")
print(f"PyInstaller {PyInstaller.__version__} · Python {sys.version.split()[0]}")
PY

mkdir -p "$ROOT/dist" "$ROOT/build"
python -m PyInstaller \
  --clean \
  --noconfirm \
  --onefile \
  --name rea-termux \
  --paths "$ROOT" \
  --collect-submodules rea_termux \
  --hidden-import rea_termux.gui \
  --distpath "$ROOT/dist" \
  --workpath "$ROOT/build/pyinstaller" \
  --specpath "$ROOT/build" \
  "$ROOT/scripts/termux_frozen_entry.py"

chmod 755 "$OUT"
"$OUT" --version
"$OUT" gui --help >/dev/null
"$OUT" doctor --help >/dev/null
sha256sum "$OUT"
printf '\nEjecutable Termux listo: %s\n' "$OUT"
printf 'Prueba: %s gui\n' "$OUT"
