#!/usr/bin/env bash
set -euo pipefail

command -v tutor >/dev/null 2>&1 || { echo "Tutor is not available in PATH" >&2; exit 1; }
command -v python >/dev/null 2>&1 || { echo "python is required" >&2; exit 1; }

REPO_ROOT="$(git rev-parse --show-toplevel)"
PLUGIN_ROOT="$(tutor plugins printroot)"
SRC="$REPO_ROOT/tutor-plugins/fpt_external_minio.py"
DST="$PLUGIN_ROOT/fpt_external_minio.py"

[ -f "$SRC" ] || { echo "Missing plugin: $SRC" >&2; exit 1; }
python -m py_compile "$SRC"
mkdir -p "$PLUGIN_ROOT"
rm -f "$DST"
ln -s "$SRC" "$DST"
tutor plugins enable fpt_external_minio >/dev/null

echo "[fpt-minio] enabled fpt_external_minio"
echo "[fpt-minio] configure with tutor config save --set FPT_MINIO_ENABLED=true ..."
