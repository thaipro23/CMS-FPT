#!/usr/bin/env bash
set -Eeuo pipefail

log() { printf '[fpt-ulmo-upgrade] %s\n' "$*"; }
fail() { printf '[fpt-ulmo-upgrade] ERROR: %s\n' "$*" >&2; exit 1; }

TUTOR_VERSION_TARGET="21.0.9"
TUTOR_MFE_VERSION_TARGET="21.0.1"
TUTOR_INDIGO_VERSION_TARGET="21.2.1"
OPENEDX_VERSION_TARGET="release/ulmo.4"

command -v git >/dev/null 2>&1 || fail "git is required"
command -v python >/dev/null 2>&1 || fail "python is required"
command -v tutor >/dev/null 2>&1 || fail "Tutor is not available in PATH; activate tutor-venv first"

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
[ -n "$REPO_ROOT" ] || fail "Run inside the CMS-FPT repository"

if ! git -C "$REPO_ROOT" diff --quiet || ! git -C "$REPO_ROOT" diff --cached --quiet; then
  fail "Tracked CMS-FPT source is dirty. Commit/stash tracked changes before upgrading Tutor."
fi

if [ "${VIRTUAL_ENV:-}" = "" ]; then
  fail "No Python virtualenv is active. Activate ~/tutor-venv/bin/activate first."
fi

TUTOR_ROOT="$(tutor config printroot)"
CONFIG_FILE="$TUTOR_ROOT/config.yml"
[ -f "$CONFIG_FILE" ] || fail "Tutor config not found: $CONFIG_FILE"

BACKUP_DIR="$HOME/tutor-backups"
mkdir -p "$BACKUP_DIR"
STAMP="$(date +%Y%m%d-%H%M%S)"
CONFIG_BACKUP="$BACKUP_DIR/config.yml.before-ulmo4-$STAMP"
cp -a "$CONFIG_FILE" "$CONFIG_BACKUP"
log "Tutor config backup: $CONFIG_BACKUP"

log "Current Tutor: $(tutor --version 2>/dev/null || true)"
log "Current OPENEDX_COMMON_VERSION: $(tutor config printvalue OPENEDX_COMMON_VERSION 2>/dev/null || true)"

log "Upgrading Tutor packages inside active virtualenv"
python -m pip install --upgrade \
  "tutor[full]==${TUTOR_VERSION_TARGET}" \
  "tutor-mfe==${TUTOR_MFE_VERSION_TARGET}" \
  "tutor-indigo==${TUTOR_INDIGO_VERSION_TARGET}"

hash -r

python - "$TUTOR_VERSION_TARGET" "$TUTOR_MFE_VERSION_TARGET" "$TUTOR_INDIGO_VERSION_TARGET" <<'PY'
import importlib.metadata as md
import sys
expected = {
    'tutor': sys.argv[1],
    'tutor-mfe': sys.argv[2],
    'tutor-indigo': sys.argv[3],
}
for package, wanted in expected.items():
    actual = md.version(package)
    if actual != wanted:
        raise SystemExit(f'{package} version mismatch: expected {wanted}, got {actual}')
    print(f'[fpt-ulmo-upgrade] PASS {package}={actual}')
PY

log "Saving Ulmo.4 release versions into Tutor config"
tutor config save \
  --set "OPENEDX_COMMON_VERSION=${OPENEDX_VERSION_TARGET}" \
  --set "EDX_PLATFORM_VERSION=${OPENEDX_VERSION_TARGET}" \
  --set "MFE_COMMON_VERSION=${OPENEDX_VERSION_TARGET}"

COMMON_VERSION="$(tutor config printvalue OPENEDX_COMMON_VERSION)"
EDX_VERSION="$(tutor config printvalue EDX_PLATFORM_VERSION)"
MFE_COMMON_VERSION="$(tutor config printvalue MFE_COMMON_VERSION)"

[ "$COMMON_VERSION" = "$OPENEDX_VERSION_TARGET" ] || fail "OPENEDX_COMMON_VERSION=$COMMON_VERSION"
[ "$EDX_VERSION" = "$OPENEDX_VERSION_TARGET" ] || fail "EDX_PLATFORM_VERSION=$EDX_VERSION"
[ "$MFE_COMMON_VERSION" = "$OPENEDX_VERSION_TARGET" ] || fail "MFE_COMMON_VERSION=$MFE_COMMON_VERSION"

log "PASS Tutor runtime: $(tutor --version)"
log "PASS OPENEDX_COMMON_VERSION=$COMMON_VERSION"
log "PASS EDX_PLATFORM_VERSION=$EDX_VERSION"
log "PASS MFE_COMMON_VERSION=$MFE_COMMON_VERSION"
log "Ulmo.4 runtime upgrade complete; no containers/images were changed by this script."
log "Next: FPT_MFE_BUILDER=mfe-builder-6g bash scripts/fpt-ui-build.sh --restart"
