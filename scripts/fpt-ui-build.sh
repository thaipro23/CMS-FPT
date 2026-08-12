#!/usr/bin/env bash
set -euo pipefail

log() { printf '[fpt-ui-build] %s\n' "$*"; }
fail() { printf '[fpt-ui-build] ERROR: %s\n' "$*" >&2; exit 1; }

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
[ -n "$REPO_ROOT" ] || fail "Run this script from inside the CMS-FPT repository"

RESTART=0
if [ "${1:-}" = "--restart" ]; then
  RESTART=1
elif [ -n "${1:-}" ]; then
  fail "Unknown argument: $1 (supported: --restart)"
fi

log "Preflight/setup"
bash "$REPO_ROOT/scripts/fpt-ui-setup.sh"

log "Building Open edX image (BuildKit cache enabled)"
tutor images build openedx

OPENEDX_IMAGE="$(tutor config printvalue DOCKER_IMAGE_OPENEDX)"
[ -n "$OPENEDX_IMAGE" ] || fail "Could not resolve DOCKER_IMAGE_OPENEDX"
log "Verifying vendored assets in $OPENEDX_IMAGE"
docker run --rm --entrypoint bash "$OPENEDX_IMAGE" -lc '
set -e
base=/openedx/staticfiles/indigo/images/fpt
for f in \
  fpt-polytechnic-logo.png \
  fpt-students.png \
  fpt-campus-primary.jpg \
  fpt-campus-secondary.jpg
do
  test -s "$base/$f"
done
ls -lh "$base"
'

log "Building MFE image (no --no-cache)"
tutor images build mfe

if [ "$RESTART" -eq 1 ]; then
  log "Restarting Tutor local deployment"
  tutor local stop
  tutor local start -d
fi

log "BUILD OK: openedx + mfe"
if [ "$RESTART" -ne 1 ]; then
  log "Run with --restart when you are ready to restart the UAT deployment"
fi
