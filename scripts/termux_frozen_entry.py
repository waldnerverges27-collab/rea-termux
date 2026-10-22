"""Entry point compiled by PyInstaller for the Termux-local CLI binary."""
from __future__ import annotations

import os
import sys

from rea_termux.cli import main

# El shim aapt de fallback ejecuta Python y reutiliza el parser del paquete.
# En el binario congelado, apunta al bundle temporal que PyInstaller extrae.
if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
    os.environ.setdefault("REA_TERMUX_PATH", sys._MEIPASS)

if __name__ == "__main__":
    raise SystemExit(main())
