#!/usr/bin/env bash
set -Eeuo pipefail

log() { printf '[fpt-ui-build] %s\n' "$*"; }
warn() { printf '[fpt-ui-build] WARN: %s\n' "$*" >&2; }
fail() {
  printf '[fpt-ui-build] ERROR: %s\n' "$*" >&2
  # After the deployment transaction is initialized, assertion failures must
  # use the same rollback path as command/subprocess failures.
  if declare -F rollback_on_error >/dev/null 2>&1; then
    rollback_on_error 1
  fi
  exit 1
}

command -v git >/dev/null 2>&1 || fail "git is required"
command -v tutor >/dev/null 2>&1 || fail "Tutor is not available in PATH"
command -v docker >/dev/null 2>&1 || fail "docker is required"
docker info >/dev/null 2>&1 || fail "Docker daemon is not reachable by the current user"

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
[ -n "$REPO_ROOT" ] || fail "Run this script from inside the CMS-FPT repository"

RESTART=0
if [ "${1:-}" = "--restart" ]; then
  RESTART=1
elif [ -n "${1:-}" ]; then
  fail "Unknown argument: $1 (supported: --restart)"
fi

log "Source commit: $(git -C "$REPO_ROOT" rev-parse HEAD)"

UNIT_RESET_EXPECTED_VERSION="$(python - "$REPO_ROOT/openedx_unit_reset/setup.py" <<'PY'
from pathlib import Path
import re
import sys
text = Path(sys.argv[1]).read_text(encoding='utf-8')
match = re.search(r"\bversion\s*=\s*['\"]([^'\"]+)['\"]", text)
if not match:
    raise SystemExit('could not resolve openedx-unit-reset version from setup.py')
print(match.group(1))
PY
)"
[ -n "$UNIT_RESET_EXPECTED_VERSION" ] || fail "Could not resolve Unit Reset package version"
log "Expected Unit Reset backend version: $UNIT_RESET_EXPECTED_VERSION"

log "Static/fixture validation"
bash "$REPO_ROOT/scripts/fpt-ui-validate-static.sh"

log "Preflight/setup"
bash "$REPO_ROOT/scripts/fpt-ui-setup.sh"

OPENEDX_IMAGE="$(tutor config printvalue DOCKER_IMAGE_OPENEDX)"
MFE_IMAGE="$(tutor config printvalue MFE_DOCKER_IMAGE 2>/dev/null || true)"
[ -n "$OPENEDX_IMAGE" ] || fail "Could not resolve DOCKER_IMAGE_OPENEDX"
[ -n "$MFE_IMAGE" ] || fail "Could not resolve MFE_DOCKER_IMAGE"

# Treat the two shared image tags as one deploy transaction. If a later build,
# verification, restart or smoke check fails, restore the exact image IDs that
# were tagged before this run. Running containers are only restarted during
# rollback if this script already touched the deployment.
PREV_OPENEDX_ID="$(docker image inspect --format '{{.Id}}' "$OPENEDX_IMAGE" 2>/dev/null || true)"
PREV_MFE_ID="$(docker image inspect --format '{{.Id}}' "$MFE_IMAGE" 2>/dev/null || true)"
ROLLBACK_ARMED=0
DEPLOYMENT_TOUCHED=0

if [ -n "$PREV_OPENEDX_ID" ] && [ -n "$PREV_MFE_ID" ]; then
  ROLLBACK_ARMED=1
  log "Rollback checkpoint: openedx=$PREV_OPENEDX_ID mfe=$PREV_MFE_ID"
else
  warn "A previous openedx/mfe image is missing; automatic image rollback cannot be fully armed for this first build"
fi

rollback_on_error() {
  local status=$?
  if [ "$#" -gt 0 ]; then
    status="$1"
  fi
  trap - ERR
  set +e
  if [ "$ROLLBACK_ARMED" -eq 1 ]; then
    warn "Deployment transaction failed (exit $status). Restoring previous image tags."
    docker tag "$PREV_OPENEDX_ID" "$OPENEDX_IMAGE"
    local openedx_restore=$?
    docker tag "$PREV_MFE_ID" "$MFE_IMAGE"
    local mfe_restore=$?
    if [ "$openedx_restore" -ne 0 ] || [ "$mfe_restore" -ne 0 ]; then
      warn "One or more previous image tags could not be restored automatically"
    else
      log "Previous openedx/mfe image tags restored"
    fi

    if [ "$DEPLOYMENT_TOUCHED" -eq 1 ]; then
      warn "Restarting previous Tutor deployment after rollback"
      tutor local stop || true
      tutor local start -d || true
      tutor local status || true
    fi
  else
    warn "Deployment transaction failed (exit $status); no complete rollback checkpoint was available"
  fi
  exit "$status"
}
trap rollback_on_error ERR

log "Building Open edX image (BuildKit cache enabled)"
tutor images build openedx

log "Verifying FPT assets/templates + Unit Reset backend in $OPENEDX_IMAGE"
docker run --rm --entrypoint bash -e UNIT_RESET_EXPECTED_VERSION="$UNIT_RESET_EXPECTED_VERSION" "$OPENEDX_IMAGE" -lc '
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

python - <<"PY"
import importlib
import importlib.metadata as metadata
import os
expected = os.environ["UNIT_RESET_EXPECTED_VERSION"]
actual = metadata.version("openedx-unit-reset")
if actual != expected:
    raise SystemExit(f"openedx-unit-reset version mismatch: expected {expected}, got {actual}")
module = importlib.import_module("openedx_unit_reset")
print(f"[fpt-ui-build] Unit Reset backend PASS version={actual} module={module.__name__}")
PY

echo "[fpt-ui-build] Open edX image branding markers PASS"
ls -lh "$base"
'

log "Building MFE image (no --no-cache)"
tutor images build mfe

log "Verifying compiled Authn/Learner Dashboard/Learning artifacts in $MFE_IMAGE"
CID="$(docker create "$MFE_IMAGE")"
TMP_DIR="$(mktemp -d)"
cleanup_artifacts() {
  docker rm -f "$CID" >/dev/null 2>&1 || true
  rm -rf "$TMP_DIR"
}
trap cleanup_artifacts EXIT

docker cp "$CID:/openedx/dist/authn" "$TMP_DIR/authn" >/dev/null
docker cp "$CID:/openedx/dist/learner-dashboard" "$TMP_DIR/learner-dashboard" >/dev/null
docker cp "$CID:/openedx/dist/learning" "$TMP_DIR/learning" >/dev/null

grep -R -Fq "FPT Polytechnic" "$TMP_DIR/authn" || fail "Compiled Authn bundle does not contain FPT Polytechnic branding"
grep -R -Fq "fpt-auth-wedge" "$TMP_DIR/authn" || fail "Compiled Authn bundle does not contain the approved wedge CSS"
grep -R -Fq "Tiếp tục hành trình học tập" "$TMP_DIR/learner-dashboard" || fail "Compiled Learner Dashboard bundle does not contain FPT learner banner"
grep -R -Fq "AI_MFE_REQUEST_RESIZE" "$TMP_DIR/learning" || fail "Compiled Learning bundle does not contain the custom Unit Reset frontend marker"
grep -R -Fq "AI_QUIZ_ACTIVE_SESSION_READY_RELOAD" "$TMP_DIR/learning" || fail "Compiled Learning bundle is missing the Unit Reset active-session reload contract"

cleanup_artifacts
trap - EXIT
log "Compiled MFE branding + Unit Reset markers PASS"

if [ "$RESTART" -eq 1 ]; then
  DEPLOYMENT_TOUCHED=1
  log "Restarting Tutor local deployment"
  tutor local stop
  tutor local start -d
  tutor local status

  LMS_HOST="$(tutor config printvalue LMS_HOST)"
  MFE_HOST="$(tutor config printvalue MFE_HOST 2>/dev/null || true)"
  ENABLE_HTTPS="$(tutor config printvalue ENABLE_HTTPS 2>/dev/null || echo false)"
  [ -n "$LMS_HOST" ] || fail "Could not resolve LMS_HOST after restart"
  [ -n "$MFE_HOST" ] || fail "Could not resolve MFE_HOST after restart"

  if [ "$ENABLE_HTTPS" = "true" ] || [ "$ENABLE_HTTPS" = "True" ]; then
    LMS_URL="https://$LMS_HOST"
    MFE_URL="https://$MFE_HOST"
  else
    LMS_URL="http://$LMS_HOST"
    MFE_URL="http://$MFE_HOST"
  fi

  log "Post-restart smoke test: LMS=$LMS_URL MFE=$MFE_URL"
  bash "$REPO_ROOT/scripts/fpt-ui-smoke.sh" "$LMS_URL" "$MFE_URL"
fi

# Commit the transaction only after all image and optional live smoke checks pass.
ROLLBACK_ARMED=0
trap - ERR
log "BUILD VERIFIED: openedx + mfe + Unit Reset backend/frontend"
if [ "$RESTART" -ne 1 ]; then
  log "Run with --restart when ready; --restart will also run LMS + MFE post-deploy smoke checks"
fi
