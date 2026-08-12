#!/usr/bin/env bash
set -euo pipefail

log() { printf '[fpt-ui] %s\n' "$*"; }
fail() { printf '[fpt-ui] ERROR: %s\n' "$*" >&2; exit 1; }
warn() { printf '[fpt-ui] WARN: %s\n' "$*" >&2; }

command -v git >/dev/null 2>&1 || fail "git is required"
command -v tutor >/dev/null 2>&1 || fail "Tutor is not available in PATH. Activate the Tutor virtualenv first."
command -v python >/dev/null 2>&1 || fail "python is required"

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
[ -n "$REPO_ROOT" ] || fail "Run this script from inside the CMS-FPT repository"
REPO_ROOT_REAL="$(readlink -f "$REPO_ROOT")"
TUTOR_ROOT="$(tutor config printroot)"
PLUGIN_ROOT="$(tutor plugins printroot)"
ASSET_DIR="$REPO_ROOT/fpt_indigo_ui/assets"
LEGACY_ASSET_DIR="$REPO_ROOT/tutor-plugins/fpt-assets"
FPT_PLUGIN="$REPO_ROOT/tutor-plugins/fpt_indigo_ui.py"
ALLOW_UNTESTED_BASELINE="${FPT_UI_ALLOW_UNTESTED_BASELINE:-0}"
EXPECTED_COMMON_VERSION="release/ulmo.3"

log "Repository: $REPO_ROOT"
log "Tutor root: $TUTOR_ROOT"
log "Tutor plugin root: $PLUGIN_ROOT"

# Never build a production-like image from tracked source that differs from the
# checked-out commit. Untracked operational files are intentionally ignored.
if ! git -C "$REPO_ROOT" diff --quiet || ! git -C "$REPO_ROOT" diff --cached --quiet; then
  fail "Tracked source has local modifications. Commit/stash them before building FPT UI."
fi

COMMON_VERSION="$(tutor config printvalue OPENEDX_COMMON_VERSION 2>/dev/null || true)"
TUTOR_VERSION_RAW="$(tutor --version 2>/dev/null || true)"
log "Open edX common version: ${COMMON_VERSION:-unknown}"
log "Tutor version: ${TUTOR_VERSION_RAW:-unknown}"

if [ "$ALLOW_UNTESTED_BASELINE" != "1" ]; then
  [ "$COMMON_VERSION" = "$EXPECTED_COMMON_VERSION" ] || fail "FPT UI is validated for OPENEDX_COMMON_VERSION=$EXPECTED_COMMON_VERSION, found '${COMMON_VERSION:-unknown}'. Set FPT_UI_ALLOW_UNTESTED_BASELINE=1 only for an intentional compatibility test."
  case "$TUTOR_VERSION_RAW" in
    *"21."*) ;;
    *) fail "FPT UI is validated on Tutor 21.x, found '${TUTOR_VERSION_RAW:-unknown}'. Set FPT_UI_ALLOW_UNTESTED_BASELINE=1 only for an intentional compatibility test." ;;
  esac
else
  warn "FPT_UI_ALLOW_UNTESTED_BASELINE=1: baseline compatibility guards are bypassed"
fi

EXPECTED_ASSETS=(
  fpt-polytechnic-logo.png
  fpt-students.png
  fpt-campus-primary.jpg
  fpt-campus-secondary.jpg
)

mkdir -p "$ASSET_DIR"
if [ -d "$LEGACY_ASSET_DIR" ]; then
  for name in "${EXPECTED_ASSETS[@]}"; do
    if [ ! -s "$ASSET_DIR/$name" ] && [ -s "$LEGACY_ASSET_DIR/$name" ]; then
      log "Migrating legacy asset: $name"
      mv "$LEGACY_ASSET_DIR/$name" "$ASSET_DIR/$name"
    fi
  done
fi

for name in "${EXPECTED_ASSETS[@]}"; do
  [ -s "$ASSET_DIR/$name" ] || fail "Missing vendored asset: fpt_indigo_ui/assets/$name"
done

if command -v file >/dev/null 2>&1; then
  file "$ASSET_DIR"/*
fi

PATCH_DIR="$REPO_ROOT/fpt_indigo_ui/patches"
AUTHN_PATCH="$PATCH_DIR/authn.patch"
OPENEDX_PATCH="$PATCH_DIR/openedx.patch"
RUNTIME_PATCH="$PATCH_DIR/runtime.patch"
for patch in "$AUTHN_PATCH" "$OPENEDX_PATCH" "$RUNTIME_PATCH"; do
  [ -s "$patch" ] || fail "Missing FPT UI patch source: $patch"
done

python - "$FPT_PLUGIN" "$AUTHN_PATCH" "$OPENEDX_PATCH" "$RUNTIME_PATCH" <<'PYGUARD'
from pathlib import Path
import sys

plugin, authn, openedx, runtime = [Path(x).read_text(encoding='utf-8') for x in sys.argv[1:]]
if '_read_patch("authn.patch")' not in plugin or '_read_patch("openedx.patch")' not in plugin:
    raise SystemExit('Tutor plugin is not loading modular FPT patch sources')
if 'mfe-dockerfile-pre-npm-build-authn' not in plugin:
    raise SystemExit('Authn patch must run at pre-npm-build after source copy')
if 'mfe-dockerfile-post-npm-install-authn' in plugin:
    raise SystemExit('obsolete Authn post-npm-install hook is still registered')
if "import React from 'react';" not in plugin or 'getConfig as getFptConfig' not in plugin:
    raise SystemExit('MFE runtime React/getConfig imports are incomplete')
if 'MFE_CONFIG["ENABLE_IMAGE_LAYOUT"] = False' not in plugin:
    raise SystemExit('Authn must be pinned to the tested DefaultLayout')
if "RUN node - <<'JS2'" not in authn:
    raise SystemExit('Authn patch must use Node.js')
if "RUN python - <<'PY2'" in authn:
    raise SystemExit('Authn patch still contains Python heredoc')
if authn.count("import React from 'react';") != 3:
    raise SystemExit('all three Ulmo Authn layouts must preserve an explicit React import')
if authn.count('<div className="fpt-auth-wedge"') != 1:
    raise SystemExit('Authn must contain exactly one orange wedge element')
if '.fpt-auth-wedge' not in authn or 'clip-path:polygon' not in authn:
    raise SystemExit('approved single-wedge CSS is missing')
if 'useEffect(' in runtime or 'getConfig()' in runtime:
    raise SystemExit('runtime patch contains unscoped React/getConfig dependency')
if 'getFptConfig()' not in runtime:
    raise SystemExit('runtime patch is not using the FPT-scoped getConfig alias')
if openedx.count('COPY --from=edx-platform /fpt_indigo_ui/assets/') != 4:
    raise SystemExit('expected exactly four vendored FPT asset COPY statements')
if 'FptHeaderLogo' not in runtime or 'FptFooter' not in runtime:
    raise SystemExit('MFE runtime branding definitions are incomplete')
if any('curl ' in data.lower() for data in (plugin, authn, openedx, runtime)):
    raise SystemExit('FPT UI source must not download assets during build')
print('[fpt-ui] Source guardrails PASS')
PYGUARD

MOUNT_FOUND=0
while IFS= read -r mount_name; do
  [ -n "$mount_name" ] || continue
  if [ "$(readlink -f "$mount_name" 2>/dev/null || true)" = "$REPO_ROOT_REAL" ]; then
    MOUNT_FOUND=1
    break
  fi
done < <(tutor mounts list 2>/dev/null | sed -n 's/^- name: //p')

if [ "$MOUNT_FOUND" -ne 1 ]; then
  log "Adding edx-platform source mount: $REPO_ROOT"
  tutor mounts add "$REPO_ROOT"
else
  log "edx-platform source mount already configured"
fi

mkdir -p "$PLUGIN_ROOT"
for plugin in openedx_connector openedx_unit_reset fpt_indigo_ui; do
  src="$REPO_ROOT/tutor-plugins/$plugin.py"
  dst="$PLUGIN_ROOT/$plugin.py"
  [ -f "$src" ] || fail "Missing Tutor plugin source: $src"

  exact_mount="$(findmnt -rn -T "$dst" -o TARGET 2>/dev/null || true)"
  if [ "$exact_mount" = "$dst" ]; then
    fail "$dst is still a file bind mount. Unmount it once, remove its /etc/fstab entry, then rerun this script."
  fi

  if [ -L "$dst" ] && [ "$(readlink -f "$dst")" = "$(readlink -f "$src")" ]; then
    log "$plugin symlink already correct"
  else
    rm -f "$dst"
    ln -s "$src" "$dst"
    log "Linked $plugin -> $src"
  fi

  python -m py_compile "$src"
  tutor plugins enable "$plugin" >/dev/null
done

log "Rendering Tutor environment"
tutor config save

GENERATED_OPENEDX="$TUTOR_ROOT/env/build/openedx/Dockerfile"
[ -f "$GENERATED_OPENEDX" ] || fail "Generated Open edX Dockerfile not found: $GENERATED_OPENEDX"

COPY_COUNT="$(grep -Fc 'COPY --from=edx-platform /fpt_indigo_ui/assets/' "$GENERATED_OPENEDX" || true)"
[ "$COPY_COUNT" -eq 4 ] || fail "Expected 4 vendored FPT asset COPY statements, found $COPY_COUNT"

if grep -Eq 'curl .*(caodang\.fpt\.edu\.vn|seeklogo\.com|wikimedia\.org|chungta\.vn)' "$GENERATED_OPENEDX"; then
  fail "Generated Open edX Dockerfile downloads FPT assets from the Internet"
fi

MFE_DOCKERFILE="$TUTOR_ROOT/env/plugins/mfe/build/mfe/Dockerfile"
[ -f "$MFE_DOCKERFILE" ] || fail "Generated MFE Dockerfile not found: $MFE_DOCKERFILE"
grep -Fq 'FPT Polytechnic V8 production branding overlay' "$MFE_DOCKERFILE" || fail "Generated MFE Dockerfile does not contain the FPT Authn patch"
grep -Fq "RUN node - <<'JS2'" "$MFE_DOCKERFILE" || fail "Generated MFE Authn patch is not using Node.js"

if [ "$ALLOW_UNTESTED_BASELINE" != "1" ]; then
  grep -Fq 'ADD --keep-git-dir=true https://github.com/openedx/frontend-app-authn.git#release/ulmo.3 .' "$MFE_DOCKERFILE" || fail "Generated MFE Dockerfile is not sourcing Authn from release/ulmo.3"
fi

python - "$MFE_DOCKERFILE" <<'PYORDER'
from pathlib import Path
import sys

text = Path(sys.argv[1]).read_text(encoding='utf-8')
common = text.find('######## authn (common)')
source_copy = text.find('COPY --from=authn-src / /openedx/app', common)
marker = text.find('FPT Polytechnic V8 production branding overlay', common)
dev = text.find('######## authn (dev)', common)
if min(common, source_copy, marker, dev) < 0:
    raise SystemExit('could not resolve Authn stage/patch markers in generated MFE Dockerfile')
if not (common < source_copy < marker < dev):
    raise SystemExit('Authn patch ordering is unsafe: FPT patch must be after authn-src COPY and before authn build')
print('[fpt-ui] Generated Authn patch ordering PASS')
PYORDER

log "Generated MFE Dockerfile verified: $MFE_DOCKERFILE"
log "Setup OK: tested baseline, clean source, plugins linked, assets vendored, patch ordering verified, environment rendered"
