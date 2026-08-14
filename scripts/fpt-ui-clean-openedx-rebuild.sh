#!/usr/bin/env bash
set -Eeuo pipefail

log() { printf '[fpt-ui-clean] %s\n' "$*"; }
warn() { printf '[fpt-ui-clean] WARN: %s\n' "$*" >&2; }
fail() { printf '[fpt-ui-clean] ERROR: %s\n' "$*" >&2; exit 1; }

command -v git >/dev/null 2>&1 || fail "git is required"
command -v tutor >/dev/null 2>&1 || fail "Tutor is required"
command -v docker >/dev/null 2>&1 || fail "Docker is required"
docker info >/dev/null 2>&1 || fail "Docker daemon is not reachable"

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
[ -n "$REPO_ROOT" ] || fail "Run inside the CMS-FPT repository"

RESTART=0
if [ "${1:-}" = "--restart" ]; then
  RESTART=1
elif [ -n "${1:-}" ]; then
  fail "Unknown argument: $1 (supported: --restart)"
fi

# Open edX is rebuilt with the Docker default builder. MFE is rebuilt with the
# dedicated cached builder, but that builder/cache is never pruned by this path.
# Docker volumes/database data are never removed.
MFE_BUILDER="${FPT_MFE_BUILDER:-mfe-builder-6g}"
MIN_FREE_MIB="${FPT_OPENEDX_CLEAN_MIN_FREE_MIB:-15360}"
MFE_MIN_FREE_MIB="${FPT_MFE_BUILD_MIN_FREE_MIB:-3072}"
OLD_OPENEDX_IMAGE="${FPT_OLD_OPENEDX_IMAGE:-docker.io/overhangio/openedx:21.0.6-indigo}"
TARGET_IMAGE="$(tutor config printvalue DOCKER_IMAGE_OPENEDX)"
TARGET_MFE_IMAGE="$(tutor config printvalue MFE_DOCKER_IMAGE 2>/dev/null || true)"
[ -n "$TARGET_IMAGE" ] || fail "Could not resolve DOCKER_IMAGE_OPENEDX"
[ -n "$TARGET_MFE_IMAGE" ] || fail "Could not resolve MFE_DOCKER_IMAGE"
docker buildx inspect default >/dev/null 2>&1 || fail "Docker default Buildx builder is unavailable"
docker buildx inspect "$MFE_BUILDER" >/dev/null 2>&1 || fail "MFE Buildx builder '$MFE_BUILDER' is unavailable"

free_mib() {
  df -Pm / | awk 'NR==2 {print $4}'
}

show_disk() {
  df -h /
  docker system df || true
}

show_mfe_cache() {
  log "MFE builder '$MFE_BUILDER' cache (read-only check; never pruned by this script)"
  docker buildx du --builder "$MFE_BUILDER" | tail -10 || true
}

remove_tutor_openedx_containers_using_image() {
  local image_ref="$1"
  local ids id name
  ids="$(docker ps -aq --filter "ancestor=$image_ref" 2>/dev/null || true)"
  [ -z "$ids" ] && return 0

  while IFS= read -r id; do
    [ -n "$id" ] || continue
    name="$(docker inspect --format '{{.Name}}' "$id" 2>/dev/null | sed 's#^/##')"
    case "$name" in
      *tutor_local-lms-worker-*|*tutor_local-cms-worker-*|*tutor_local-lms-[0-9]*|*tutor_local-cms-[0-9]*)
        log "Removing Open edX service container $name ($id)"
        docker rm -f "$id" >/dev/null
        ;;
      *)
        fail "Refusing to remove non-Tutor container '$name' that uses $image_ref"
        ;;
    esac
  done <<< "$ids"
}

remove_image_if_present() {
  local image_ref="$1"
  if docker image inspect "$image_ref" >/dev/null 2>&1; then
    remove_tutor_openedx_containers_using_image "$image_ref"
    log "Removing image $image_ref"
    docker image rm "$image_ref"
  else
    log "Image not present, skip: $image_ref"
  fi
}

verify_service_image() {
  local service="$1"
  local expected_image_id="$2"
  local cid actual
  cid="$(tutor local dc ps -q "$service" 2>/dev/null | head -n1)"
  [ -n "$cid" ] || fail "No container found for $service after restart"
  actual="$(docker inspect --format '{{.Image}}' "$cid" 2>/dev/null || true)"
  [ -n "$actual" ] || fail "Could not resolve image ID for $service"
  [ "$actual" = "$expected_image_id" ] || fail "$service is using $actual, expected $expected_image_id"
  log "PASS $service -> $actual"
}

log "Source commit: $(git -C "$REPO_ROOT" rev-parse HEAD)"
log "Open edX target image: $TARGET_IMAGE"
log "MFE target image: $TARGET_MFE_IMAGE"
log "MFE Buildx builder: $MFE_BUILDER (cache preserved/reused)"
log "Old Open edX image candidate: $OLD_OPENEDX_IMAGE"
log "Required free space before full Open edX rebuild: ${MIN_FREE_MIB} MiB"
log "Required free space before cached MFE rebuild: ${MFE_MIN_FREE_MIB} MiB"

if ! git -C "$REPO_ROOT" diff --quiet || ! git -C "$REPO_ROOT" diff --cached --quiet; then
  git -C "$REPO_ROOT" status --short
  fail "Tracked source changes detected; refusing clean rebuild"
fi

log "Initial storage"
show_disk
show_mfe_cache

# Remove only the four Open edX application containers. Database, Redis,
# MongoDB, Caddy and the current MFE container/volumes stay untouched here.
log "Removing current LMS/CMS application containers only"
tutor local dc rm -sf lms cms lms-worker cms-worker >/dev/null 2>&1 || true

# First reclaim the obsolete Ulmo.3 final image. This is not BuildKit cache.
remove_image_if_present "$OLD_OPENEDX_IMAGE"

# Remove only dangling final-image references. Do not use -a, system prune,
# builder prune or volume prune here.
log "Removing dangling images only"
docker image prune -f || true

# Host package/log caches are unrelated to Open edX/MFE BuildKit caches. Clean
# them only when passwordless sudo is already available; otherwise skip safely.
if sudo -n true >/dev/null 2>&1; then
  log "Cleaning host APT package cache"
  sudo -n apt-get clean || true
  log "Capping systemd journal at 200 MiB"
  sudo -n journalctl --vacuum-size=200M >/dev/null || true
else
  warn "Passwordless sudo unavailable; skipping APT/journal cleanup"
fi

FREE_NOW="$(free_mib)"
log "Free space after old-image cleanup: ${FREE_NOW} MiB"

# Preserve the existing Ulmo.4 image as rollback if there is already enough
# room. On this constrained UAT host, remove it only when required to reach the
# clean-build safety floor; its layers will then be recreated from source.
if [ "$FREE_NOW" -lt "$MIN_FREE_MIB" ]; then
  warn "Still below ${MIN_FREE_MIB} MiB; removing current target image to reclaim its final layers"
  remove_image_if_present "$TARGET_IMAGE"
  docker image prune -f || true
  FREE_NOW="$(free_mib)"
  log "Free space after target-image cleanup: ${FREE_NOW} MiB"
fi

show_disk
show_mfe_cache

[ "$FREE_NOW" -ge "$MIN_FREE_MIB" ] || fail "Only ${FREE_NOW} MiB free after safe cleanup; need at least ${MIN_FREE_MIB} MiB. MFE cache and Docker volumes were preserved."

log "Rendering Tutor/FPT build configuration"
bash "$REPO_ROOT/scripts/fpt-ui-setup.sh"

# The default Docker builder is deliberately isolated from mfe-builder-6g.
log "Building Open edX from source with Docker default builder"
BUILDX_BUILDER=default tutor images build openedx

UNIT_RESET_EXPECTED_VERSION="$(python - "$REPO_ROOT/openedx_unit_reset/setup.py" <<'PY'
from pathlib import Path
import re
import sys
text = Path(sys.argv[1]).read_text(encoding='utf-8')
match = re.search(r"\bversion\s*=\s*['\"]([^'\"]+)['\"]", text)
if not match:
    raise SystemExit('could not resolve openedx-unit-reset version')
print(match.group(1))
PY
)"

log "Verifying rebuilt Open edX image"
docker run --rm --entrypoint bash -e UNIT_RESET_EXPECTED_VERSION="$UNIT_RESET_EXPECTED_VERSION" "$TARGET_IMAGE" -lc '
set -euo pipefail
courses=/openedx/themes/indigo/lms/templates/courseware/courses.html
home=/openedx/themes/indigo/lms/templates/index.html
footer=/openedx/themes/indigo/lms/templates/footer.html
assets=/openedx/staticfiles/indigo/images

grep -Fq "FPT_DISCOVERY_V8_START" "$courses"
grep -Fq "FPT_DISCOVERY_V9_BALANCE" "$courses"
grep -Fq "FPT_DISCOVERY_V8_START" "$home"
grep -Fq "FPT_DISCOVERY_V9_BALANCE" "$home"
grep -Fq "id=\"discovery-form\"" "$home"
grep -Fq "fpt-lms-footer" "$footer"

test -s "$assets/fpt/fpt-polytechnic-logo.png"
test -s "$assets/fpt/fpt-polytechnic-logo-white.png"
cmp -s "$assets/fpt/fpt-polytechnic-logo.png" "$assets/logo.png"
cmp -s "$assets/fpt/fpt-polytechnic-logo-white.png" "$assets/logo-white.png"
! cmp -s "$assets/logo.png" "$assets/logo-white.png"

python - <<"PY"
import importlib.metadata as metadata
import os
expected = os.environ["UNIT_RESET_EXPECTED_VERSION"]
actual = metadata.version("openedx-unit-reset")
if actual != expected:
    raise SystemExit(f"openedx-unit-reset mismatch: expected {expected}, got {actual}")
print(f"Unit Reset backend PASS {actual}")
PY

echo "Open edX FPT homepage/courses/colour+white native logo verification PASS"
'

FREE_BEFORE_MFE="$(free_mib)"
log "Free space before MFE build: ${FREE_BEFORE_MFE} MiB"
[ "$FREE_BEFORE_MFE" -ge "$MFE_MIN_FREE_MIB" ] || fail "Open edX rebuild PASS, but only ${FREE_BEFORE_MFE} MiB remains. Need at least ${MFE_MIN_FREE_MIB} MiB before MFE build; MFE cache was not pruned."

# Reuse the dedicated MFE BuildKit cache. This is a normal cached build: no
# --no-cache and no prune, so Learning/Authn/Learner Dashboard rebuild cheaply.
log "Building MFE image with dedicated cached builder '$MFE_BUILDER'"
BUILDX_BUILDER="$MFE_BUILDER" tutor images build mfe

log "Verifying compiled Authn/Learner Dashboard/Learning artifacts"
MFE_VERIFY_CID="$(docker create "$TARGET_MFE_IMAGE")"
MFE_VERIFY_DIR="$(mktemp -d)"
cleanup_mfe_verify() {
  docker rm -f "$MFE_VERIFY_CID" >/dev/null 2>&1 || true
  rm -rf "$MFE_VERIFY_DIR"
}
trap cleanup_mfe_verify EXIT

docker cp "$MFE_VERIFY_CID:/openedx/dist/authn" "$MFE_VERIFY_DIR/authn" >/dev/null
docker cp "$MFE_VERIFY_CID:/openedx/dist/learner-dashboard" "$MFE_VERIFY_DIR/learner-dashboard" >/dev/null
docker cp "$MFE_VERIFY_CID:/openedx/dist/learning" "$MFE_VERIFY_DIR/learning" >/dev/null

grep -R -Fq "Start learning" "$MFE_VERIFY_DIR/authn" || fail "Compiled Authn is missing 'Start learning'"
grep -R -Fq "with CMS" "$MFE_VERIFY_DIR/authn" || fail "Compiled Authn is missing 'with CMS'"
grep -R -Fq "fpt-polytechnic-logo-white.png" "$MFE_VERIFY_DIR/authn" || fail "Compiled Authn is missing the real white FPT logo asset"
grep -R -Fq "selected-paragon-theme-variant" "$MFE_VERIFY_DIR/authn" || fail "Compiled Authn is missing the light-only theme contract"
grep -R -Fq "Tiếp tục hành trình học tập" "$MFE_VERIFY_DIR/learner-dashboard" || fail "Compiled Learner Dashboard is missing the FPT learner banner"
grep -R -Fq "fpt-polytechnic-logo-white.png" "$MFE_VERIFY_DIR/learner-dashboard" || fail "Compiled Learner Dashboard is missing the white FPT footer logo"
grep -R -Fq "AI_MFE_REQUEST_RESIZE" "$MFE_VERIFY_DIR/learning" || fail "Compiled Learning is missing Unit Reset resize marker"
grep -R -Fq "AI_QUIZ_ACTIVE_SESSION_READY_RELOAD" "$MFE_VERIFY_DIR/learning" || fail "Compiled Learning is missing Unit Reset active-session reload marker"

cleanup_mfe_verify
trap - EXIT
log "Compiled MFE Authn/FPT branding/Unit Reset verification PASS"

if [ "$RESTART" -eq 1 ]; then
  log "Starting/recreating LMS/CMS/workers + MFE on rebuilt images"
  tutor local dc up -d --no-deps --force-recreate lms cms lms-worker cms-worker mfe

  EXPECTED_OPENEDX_ID="$(docker image inspect --format '{{.Id}}' "$TARGET_IMAGE")"
  EXPECTED_MFE_ID="$(docker image inspect --format '{{.Id}}' "$TARGET_MFE_IMAGE")"
  for service in lms cms lms-worker cms-worker; do
    verify_service_image "$service" "$EXPECTED_OPENEDX_ID"
  done
  verify_service_image mfe "$EXPECTED_MFE_ID"
fi

log "Final storage"
show_disk
show_mfe_cache
log "CLEAN OPENEDX + MFE REBUILD VERIFIED; MFE cache was reused/not pruned and Docker volumes were preserved"
