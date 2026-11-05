"""PyInstaller entry point for N1MMautostarter.exe (see pouncer/autostarter.py)."""

import sys

from pouncer.autostarter import main

if __name__ == "__main__":
    sys.exit(main())
