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
log "Verifying FPT assets/templates in $OPENEDX_IMAGE"
docker run --rm --entrypoint bash "$OPENEDX_IMAGE" -lc '
set -euo pipefail
base=/openedx/staticfiles/indigo/images/fpt
for f in \
  fpt-polytechnic-logo.png \
  fpt-students.png \
  fpt-campus-primary.jpg \
  fpt-campus-secondary.jpg
do
  test -s "$base/$f"
done

grep -Fq "FPT_DISCOVERY_V8_START" /openedx/themes/indigo/lms/templates/courseware/courses.html
grep -Fq "fpt-hero-slider" /openedx/themes/indigo/lms/templates/courseware/courses.html
grep -Fq "fpt-lms-footer" /openedx/themes/indigo/lms/templates/footer.html
grep -Fq "/static/indigo/images/fpt/fpt-polytechnic-logo.png" /openedx/edx-platform/lms/templates/header/navbar-logo-header.html

echo "[fpt-ui-build] Open edX image branding markers PASS"
ls -lh "$base"
'

log "Building MFE image (no --no-cache)"
tutor images build mfe

MFE_IMAGE="$(tutor config printvalue MFE_DOCKER_IMAGE 2>/dev/null || true)"
[ -n "$MFE_IMAGE" ] || fail "Could not resolve MFE_DOCKER_IMAGE"
log "Verifying compiled Authn/Learner Dashboard branding in $MFE_IMAGE"

CID="$(docker create "$MFE_IMAGE")"
TMP_DIR="$(mktemp -d)"
cleanup() {
  docker rm -f "$CID" >/dev/null 2>&1 || true
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

docker cp "$CID:/openedx/dist/authn" "$TMP_DIR/authn" >/dev/null
docker cp "$CID:/openedx/dist/learner-dashboard" "$TMP_DIR/learner-dashboard" >/dev/null

grep -R -Fq "FPT Polytechnic" "$TMP_DIR/authn" || fail "Compiled Authn bundle does not contain FPT Polytechnic branding"
grep -R -Fq "fpt-auth-wedge" "$TMP_DIR/authn" || fail "Compiled Authn bundle does not contain the approved wedge CSS"
grep -R -Fq "Tiếp tục hành trình học tập" "$TMP_DIR/learner-dashboard" || fail "Compiled Learner Dashboard bundle does not contain FPT learner banner"

cleanup
trap - EXIT
log "Compiled MFE branding markers PASS"

if [ "$RESTART" -eq 1 ]; then
  log "Restarting Tutor local deployment"
  tutor local stop
  tutor local start -d
  tutor local status

  LMS_HOST="$(tutor config printvalue LMS_HOST)"
  ENABLE_HTTPS="$(tutor config printvalue ENABLE_HTTPS 2>/dev/null || echo false)"
  if [ "$ENABLE_HTTPS" = "true" ] || [ "$ENABLE_HTTPS" = "True" ]; then
    LMS_URL="https://$LMS_HOST"
  else
    LMS_URL="http://$LMS_HOST"
  fi

  log "Post-restart smoke test: $LMS_URL"
  bash "$REPO_ROOT/scripts/fpt-ui-smoke.sh" "$LMS_URL"
fi

log "BUILD VERIFIED: openedx + mfe"
if [ "$RESTART" -ne 1 ]; then
  log "Run with --restart when ready; --restart will also run post-deploy smoke checks"
fi
