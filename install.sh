#!/bin/sh
# Thin POSIX convenience wrapper — install.py is the canonical installer.
dir="$(dirname "$0")"
if command -v python3 >/dev/null 2>&1; then
    exec python3 "$dir/install.py" "$@"
else
    exec python "$dir/install.py" "$@"
fi
