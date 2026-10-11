"""Top-level launcher used as the PyInstaller entry point.

PyInstaller executes the entry script as a bare ``__main__`` module, so the
package-relative imports inside ``pouncer/__main__.py`` fail when that file is
used directly as the entry point. Importing it here (as ``pouncer.__main__``)
keeps the package context intact for frozen builds.
"""

from __future__ import annotations

import sys

from pouncer.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
