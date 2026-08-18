#!/bin/sh
# POSIX twin of tests/acceptance.py (the canonical, cross-platform runner).
dir="$(dirname "$0")"
if command -v python3 >/dev/null 2>&1; then
    exec python3 "$dir/acceptance.py" "$@"
else
    exec python "$dir/acceptance.py" "$@"
fi
