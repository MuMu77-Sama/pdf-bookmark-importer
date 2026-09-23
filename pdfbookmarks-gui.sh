#!/usr/bin/env bash
#
# pdfbookmarks - graphical launcher for Linux and macOS.
#
# Usage:
#     ./pdfbookmarks-gui.sh
#
# On Windows use pdfbookmarks-gui.cmd instead.
#
# The GUI needs tkinter.  It ships with the python.org builds on Windows and
# macOS; on Debian/Ubuntu you may need:  sudo apt install python3-tk
#
# If the script is not executable yet:  chmod +x pdfbookmarks-gui.sh

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

find_python() {
    local cand
    for cand in python3 python; do
        if command -v "$cand" >/dev/null 2>&1 &&
           "$cand" -c "import zlib, argparse, tkinter" >/dev/null 2>&1; then
            printf '%s\n' "$cand"
            return 0
        fi
    done
    for cand in /usr/bin/python3 /usr/local/bin/python3 /opt/homebrew/bin/python3; do
        if [ -x "$cand" ] && "$cand" -c "import zlib, argparse, tkinter" >/dev/null 2>&1; then
            printf '%s\n' "$cand"
            return 0
        fi
    done
    return 1
}

if ! PY="$(find_python)"; then
    echo "pdfbookmarks: no Python 3 with tkinter found." >&2
    echo "Install Python 3.8+ and tkinter, then try again." >&2
    echo "  Debian/Ubuntu:  sudo apt install python3-tk" >&2
    echo "  Fedora:         sudo dnf install python3-tkinter" >&2
    exit 1
fi

exec "$PY" -m pdfbookmarks.gui "$@"
