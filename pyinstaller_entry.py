"""PyInstaller entry point.

PyInstaller runs the entry script as a top-level module, so the package's
relative imports (``from .cli import ...``) cannot be used directly here.
Import the package by its absolute name instead.

The same file works when run from source (``python pyinstaller_entry.py``),
which makes it easy to check the entry point before freezing.
"""

import os
import sys

if not getattr(sys, "frozen", False):
    # Running from source: make the package directory importable.
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pdfbookmarks.gui import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
