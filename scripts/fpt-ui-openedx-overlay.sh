#!/usr/bin/env bash
set -Eeuo pipefail

log() { printf '[fpt-ui-overlay] %s\n' "$*"; }
fail() { printf '[fpt-ui-overlay] ERROR: %s\n' "$*" >&2; exit 1; }

command -v git >/dev/null 2>&1 || fail "git is required"
command -v tutor >/dev/null 2>&1 || fail "Tutor is required"
command -v docker >/dev/null 2>&1 || fail "Docker is required"
docker info >/dev/null 2>&1 || fail "Docker daemon is not reachable"

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
[ -n "$REPO_ROOT" ] || fail "Run this script inside the CMS-FPT repository"

RESTART=0
if [ "${1:-}" = "--restart" ]; then
  RESTART=1
elif [ -n "${1:-}" ]; then
  fail "Unknown argument: $1 (supported: --restart)"
fi

HOMEPAGE_PATCH="$REPO_ROOT/fpt_indigo_ui/patches/homepage_slider.patch"
NATIVE_LOGO_PATCH="$REPO_ROOT/fpt_indigo_ui/patches/native_logo.patch"
[ -s "$HOMEPAGE_PATCH" ] || fail "Missing $HOMEPAGE_PATCH"
[ -s "$NATIVE_LOGO_PATCH" ] || fail "Missing $NATIVE_LOGO_PATCH"

OPENEDX_IMAGE="$(tutor config printvalue DOCKER_IMAGE_OPENEDX)"
[ -n "$OPENEDX_IMAGE" ] || fail "Could not resolve DOCKER_IMAGE_OPENEDX"

PREV_ID="$(docker image inspect --format '{{.Id}}' "$OPENEDX_IMAGE" 2>/dev/null || true)"
[ -n "$PREV_ID" ] || fail "Base Open edX image is not available locally: $OPENEDX_IMAGE"

AVAILABLE_KB="$(df -Pk / | awk 'NR==2 {print $4}')"
[ -n "$AVAILABLE_KB" ] || fail "Could not resolve free disk space"
if [ "$AVAILABLE_KB" -lt 3145728 ]; then
  fail "Less than 3 GiB free on /. Overlay build aborted before touching image tags."
fi
log "Disk free before overlay: $((AVAILABLE_KB / 1024)) MiB"
log "Base image: $OPENEDX_IMAGE -> $PREV_ID"

# This fast path is intentionally only for a previously built FPT Ulmo.4 image.
# It must already contain the complete Discovery V8/V9 hero and vendored FPT logo;
# the overlay merely reuses that final hero on index.html and swaps native logo files.
docker run --rm --entrypoint bash "$PREV_ID" -lc '
set -euo pipefail
test -s /openedx/staticfiles/indigo/images/fpt/fpt-polytechnic-logo.png
grep -Fq "FPT_DISCOVERY_V8_START" /openedx/themes/indigo/lms/templates/courseware/courses.html
grep -Fq "FPT_DISCOVERY_V9_BALANCE" /openedx/themes/indigo/lms/templates/courseware/courses.html
' || fail "Existing Open edX image is not a compatible FPT Ulmo.4 base"

SHORT_ID="${PREV_ID#sha256:}"
SHORT_ID="${SHORT_ID:0:12}"
BASE_TAG="local/fpt-openedx-overlay-base:${SHORT_ID}"
TMP_DIR="$(mktemp -d)"
BASE_USER="$(docker image inspect --format '{{.Config.User}}' "$PREV_ID" 2>/dev/null || true)"
ROLLBACK_ARMED=1

cleanup() {
  rm -rf "$TMP_DIR"
  docker image rm "$BASE_TAG" >/dev/null 2>&1 || true
}

rollback() {
  local status=$?
  trap - ERR
  set +e
  if [ "$ROLLBACK_ARMED" -eq 1 ]; then
    log "Overlay failed; restoring previous image tag"
    docker tag "$PREV_ID" "$OPENEDX_IMAGE" >/dev/null 2>&1 || true
  fi
  cleanup
  exit "$status"
}
trap rollback ERR
trap cleanup EXIT

docker tag "$PREV_ID" "$BASE_TAG"

{
  printf 'FROM %s\n' "$BASE_TAG"
  printf 'USER root\n'
  cat "$HOMEPAGE_PATCH"
  printf '\n'
  cat "$NATIVE_LOGO_PATCH"
  if [ -n "$BASE_USER" ]; then
    printf '\nUSER %s\n' "$BASE_USER"
  fi
} > "$TMP_DIR/Dockerfile"

log "Building thin UI overlay with Docker default builder"
BUILDX_BUILDER=default docker buildx build \
  --load \
  --tag "$OPENEDX_IMAGE" \
  --file "$TMP_DIR/Dockerfile" \
  "$TMP_DIR"

NEW_ID="$(docker image inspect --format '{{.Id}}' "$OPENEDX_IMAGE")"
[ -n "$NEW_ID" ] || fail "Could not resolve overlay image ID"
[ "$NEW_ID" != "$PREV_ID" ] || fail "Overlay image ID did not change"

log "Verifying thin overlay image: $NEW_ID"
docker run --rm --entrypoint bash "$NEW_ID" -lc '
set -euo pipefail
courses=/openedx/themes/indigo/lms/templates/courseware/courses.html
home=/openedx/themes/indigo/lms/templates/index.html
logo=/openedx/staticfiles/indigo/images/fpt/fpt-polytechnic-logo.png
native=/openedx/staticfiles/indigo/images/logo.png
native_white=/openedx/staticfiles/indigo/images/logo-white.png

grep -Fq "FPT_DISCOVERY_V8_START" "$courses"
grep -Fq "FPT_DISCOVERY_V9_BALANCE" "$courses"
grep -Fq "FPT_HOMEPAGE_SHARED_SLIDER_START" "$home"
grep -Fq "fpt-hero-slider" "$home"
grep -Fq "id=\"discovery-form\"" "$home"
cmp -s "$logo" "$native"
cmp -s "$logo" "$native_white"

python - <<"PY"
from pathlib import Path
courses = Path("/openedx/themes/indigo/lms/templates/courseware/courses.html").read_text(encoding="utf-8")
home = Path("/openedx/themes/indigo/lms/templates/index.html").read_text(encoding="utf-8")
start = "<!-- FPT_DISCOVERY_V8_START -->"
end = "<!-- FPT_DISCOVERY_V8_END -->"
course_hero = start + courses.split(start, 1)[1].split(end, 1)[0] + end
home_hero = start + home.split(start, 1)[1].split(end, 1)[0] + end
if course_hero != home_hero:
    raise SystemExit("homepage/course Discovery hero mismatch")
if home.count("id=\"fpt-hero-slider\"") != 1:
    raise SystemExit("homepage must contain exactly one FPT hero")
print("[fpt-ui-overlay] Homepage shared slider + native logo PASS")
PY
'

if [ "$RESTART" -eq 1 ]; then
  log "Recreating Open edX services only"
  tutor local dc up -d --no-deps --force-recreate lms cms lms-worker cms-worker

  for service in lms cms lms-worker cms-worker; do
    cid="$(tutor local dc ps -q "$service" 2>/dev/null | head -n1)"
    [ -n "$cid" ] || fail "No container found for $service"
    actual="$(docker inspect --format '{{.Image}}' "$cid")"
    [ "$actual" = "$NEW_ID" ] || fail "$service is using $actual, expected $NEW_ID"
    log "PASS $service -> $NEW_ID"
  done
fi

ROLLBACK_ARMED=0
log "Overlay build verified: previous=$PREV_ID new=$NEW_ID"
log "Disk after overlay: $(df -h / | awk 'NR==2 {print $4 " free (" $5 " used)"}')"
if [ "$RESTART" -ne 1 ]; then
  log "Run again with --restart to recreate LMS/CMS/workers when ready"
fi
