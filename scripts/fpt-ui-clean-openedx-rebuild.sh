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

# This path is intentionally Open-edX-only. It never prunes or rebuilds the
# dedicated MFE builder/cache and never removes Docker volumes/database data.
MFE_BUILDER="${FPT_MFE_BUILDER:-mfe-builder-6g}"
MIN_FREE_MIB="${FPT_OPENEDX_CLEAN_MIN_FREE_MIB:-15360}"
OLD_OPENEDX_IMAGE="${FPT_OLD_OPENEDX_IMAGE:-docker.io/overhangio/openedx:21.0.6-indigo}"
TARGET_IMAGE="$(tutor config printvalue DOCKER_IMAGE_OPENEDX)"
[ -n "$TARGET_IMAGE" ] || fail "Could not resolve DOCKER_IMAGE_OPENEDX"

free_mib() {
  df -Pm / | awk 'NR==2 {print $4}'
}

show_disk() {
  df -h /
  docker system df || true
}

show_mfe_cache() {
  if docker buildx inspect "$MFE_BUILDER" >/dev/null 2>&1; then
    log "MFE builder '$MFE_BUILDER' cache (read-only check; preserved)"
    docker buildx du --builder "$MFE_BUILDER" | tail -10 || true
  else
    warn "MFE builder '$MFE_BUILDER' not found; no MFE cleanup will be attempted"
  fi
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

log "Source commit: $(git -C "$REPO_ROOT" rev-parse HEAD)"
log "Target image: $TARGET_IMAGE"
log "Old image candidate: $OLD_OPENEDX_IMAGE"
log "Required free space before full Open edX rebuild: ${MIN_FREE_MIB} MiB"

if ! git -C "$REPO_ROOT" diff --quiet || ! git -C "$REPO_ROOT" diff --cached --quiet; then
  git -C "$REPO_ROOT" status --short
  fail "Tracked source changes detected; refusing clean rebuild"
fi

log "Initial storage"
show_disk
show_mfe_cache

# Remove only the four Open edX application containers. Database, Redis,
# MongoDB, Caddy and MFE containers/volumes stay untouched.
log "Removing current LMS/CMS application containers only"
tutor local dc rm -sf lms cms lms-worker cms-worker >/dev/null 2>&1 || true

# First reclaim the obsolete Ulmo.3 final image. This is not BuildKit cache.
remove_image_if_present "$OLD_OPENEDX_IMAGE"

# Remove only dangling final-image references. Do not use -a, system prune,
# builder prune or volume prune here.
log "Removing dangling images only"
docker image prune -f || true

# Host package/log caches are unrelated to Open edX build caches. Clean them
# only when passwordless sudo is already available; otherwise skip safely.
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

[ "$FREE_NOW" -ge "$MIN_FREE_MIB" ] || fail "Only ${FREE_NOW} MiB free after safe cleanup; need at least ${MIN_FREE_MIB} MiB. MFE cache and volumes were preserved."

log "Rendering Tutor/FPT build configuration"
bash "$REPO_ROOT/scripts/fpt-ui-setup.sh"

# The default Docker builder is deliberately isolated from mfe-builder-6g.
log "Building Open edX from source with Docker default builder (MFE untouched)"
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

if [ "$RESTART" -eq 1 ]; then
  log "Starting/recreating LMS/CMS application services on rebuilt image"
  tutor local dc up -d --no-deps --force-recreate lms cms lms-worker cms-worker

  EXPECTED_ID="$(docker image inspect --format '{{.Id}}' "$TARGET_IMAGE")"
  for service in lms cms lms-worker cms-worker; do
    cid="$(tutor local dc ps -q "$service" 2>/dev/null | head -n1)"
    [ -n "$cid" ] || fail "No container found for $service after restart"
    actual="$(docker inspect --format '{{.Image}}' "$cid")"
    [ "$actual" = "$EXPECTED_ID" ] || fail "$service is using $actual, expected $EXPECTED_ID"
    log "PASS $service -> $actual"
  done
fi

log "Final storage"
show_disk
show_mfe_cache
log "CLEAN OPENEDX REBUILD VERIFIED; MFE builder/cache and Docker volumes were preserved"
