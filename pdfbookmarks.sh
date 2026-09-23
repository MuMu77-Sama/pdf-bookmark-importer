#!/usr/bin/env bash
#
# pdfbookmarks - command line launcher for Linux and macOS.
#
# Usage:
#     ./pdfbookmarks.sh input.pdf outline.txt [options]
#     ./pdfbookmarks.sh input.pdf --list
#
# On Windows use pdfbookmarks.cmd instead.
#
# Requires Python 3.8+ with the standard library only (no pip installs).
# If the script is not executable yet:  chmod +x pdfbookmarks.sh

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

# Pick the first interpreter that can actually import what we need.  Testing
# argparse (not just zlib) matters: zlib is a built-in C module and imports
# fine even from a broken interpreter with no standard library.
find_python() {
    local cand
    for cand in python3 python; do
        if command -v "$cand" >/dev/null 2>&1 &&
           "$cand" -c "import zlib, argparse" >/dev/null 2>&1; then
            printf '%s\n' "$cand"
            return 0
        fi
    done
    for cand in /usr/bin/python3 /usr/local/bin/python3 /opt/homebrew/bin/python3; do
        if [ -x "$cand" ] && "$cand" -c "import zlib, argparse" >/dev/null 2>&1; then
            printf '%s\n' "$cand"
            return 0
        fi
    done
    return 1
}

if ! PY="$(find_python)"; then
    echo "pdfbookmarks: no usable Python 3 found." >&2
    echo "Install Python 3.8 or newer and try again." >&2
    exit 1
fi

exec "$PY" -m pdfbookmarks "$@"
