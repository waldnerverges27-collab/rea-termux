"""Entry point compiled by PyInstaller for the Termux-local CLI binary."""
from __future__ import annotations

import sys

from rea_termux.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
