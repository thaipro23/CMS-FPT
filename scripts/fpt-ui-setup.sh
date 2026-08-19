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
EXPECTED_COMMON_VERSION="release/ulmo.4"
EXPECTED_TUTOR_VERSION="21.0.9"
EXPECTED_TUTOR_MFE_VERSION="21.0.1"
EXPECTED_TUTOR_INDIGO_VERSION="21.2.1"
EXPECTED_ULMO4_COMMIT="46c543590c78aa1bfa846d47a4f1c5c6ec388490"
LEARNING_REPO="${FPT_LEARNING_REPO:-/opt/openedx/frontend-app-learning}"
LEARNING_BRANCH="${FPT_LEARNING_BRANCH:-mfe-unit-reset}"
SKIP_LEARNING_GUARD="${FPT_UI_SKIP_LEARNING_GUARD:-0}"

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
  [ "$COMMON_VERSION" = "$EXPECTED_COMMON_VERSION" ] || fail "FPT UI is validated for OPENEDX_COMMON_VERSION=$EXPECTED_COMMON_VERSION, found '${COMMON_VERSION:-unknown}'. Run scripts/fpt-ulmo-upgrade.sh before building."
  case "$TUTOR_VERSION_RAW" in
    *"$EXPECTED_TUTOR_VERSION"*) ;;
    *) fail "FPT UI is validated on Tutor $EXPECTED_TUTOR_VERSION, found '${TUTOR_VERSION_RAW:-unknown}'. Run scripts/fpt-ulmo-upgrade.sh before building." ;;
  esac

  python - "$EXPECTED_TUTOR_MFE_VERSION" "$EXPECTED_TUTOR_INDIGO_VERSION" <<'PYVERS'
import importlib.metadata as metadata
import sys
expected = {
    'tutor-mfe': sys.argv[1],
    'tutor-indigo': sys.argv[2],
}
for package, wanted in expected.items():
    actual = metadata.version(package)
    if actual != wanted:
        raise SystemExit(f'{package} version mismatch: expected {wanted}, got {actual}')
    print(f'[fpt-ui] {package}={actual} PASS')
PYVERS

  git -C "$REPO_ROOT" merge-base --is-ancestor "$EXPECTED_ULMO4_COMMIT" HEAD || fail "Checked-out FPT branch does not contain the official release/ulmo.4 baseline commit $EXPECTED_ULMO4_COMMIT. Pull the latest fpt-indigo-ui before building."
  log "Open edX release/ulmo.4 ancestry PASS"
else
  warn "FPT_UI_ALLOW_UNTESTED_BASELINE=1: baseline compatibility guards are bypassed"
fi

EXPECTED_ASSETS=(
  fpt-polytechnic-logo.png
  fpt-polytechnic-logo-white.png
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
AUTHN_POLISH_PATCH="$PATCH_DIR/authn_polish.patch"
OPENEDX_PATCH="$PATCH_DIR/openedx.patch"
RUNTIME_PATCH="$PATCH_DIR/runtime.patch"
for patch in "$AUTHN_PATCH" "$AUTHN_POLISH_PATCH" "$OPENEDX_PATCH" "$RUNTIME_PATCH"; do
  [ -s "$patch" ] || fail "Missing FPT UI patch source: $patch"
done

python - "$FPT_PLUGIN" "$AUTHN_PATCH" "$AUTHN_POLISH_PATCH" "$OPENEDX_PATCH" "$RUNTIME_PATCH" <<'PYGUARD'
from pathlib import Path
import sys

plugin, authn, authn_polish, openedx, runtime = [Path(x).read_text(encoding='utf-8') for x in sys.argv[1:]]
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
if authn.count('<img className="fpt-auth-logo fpt-auth-logo--white"') != 3:
    raise SystemExit('all three Ulmo Authn layouts must render exactly one native white logo')
if 'getConfig().LMS_BASE_URL || window.location.origin' not in authn:
    raise SystemExit('Authn white logo URL is missing the same-origin fallback')
if 'FPT Polytechnic V12 white-logo dark-surface contract' not in authn_polish:
    raise SystemExit('Authn white-logo dark-surface contract is missing')
if 'FPT Polytechnic V13 solid-navy seamless wedge contract' not in authn_polish:
    raise SystemExit('Authn solid-navy seamless wedge contract is missing')
if '--fpt-auth-visual-surface:#071A33' not in authn_polish:
    raise SystemExit('Authn solid-navy surface token is missing')
if authn.count('<div className="fpt-auth-wedge"') != 1:
    raise SystemExit('Authn must contain exactly one diagonal wedge element')
if '.fpt-auth-wedge' not in authn or 'clip-path:polygon' not in authn:
    raise SystemExit('approved single-wedge CSS is missing')
if 'useEffect(' in runtime or 'getConfig()' in runtime:
    raise SystemExit('runtime patch contains unscoped React/getConfig dependency')
if 'getFptConfig()' not in runtime:
    raise SystemExit('runtime patch is not using the FPT-scoped getConfig alias')
if openedx.count('COPY --from=edx-platform /fpt_indigo_ui/assets/') != 5:
    raise SystemExit('expected exactly five vendored FPT asset COPY statements')
if 'FptHeaderLogo' not in runtime or 'FptFooter' not in runtime:
    raise SystemExit('MFE runtime branding definitions are incomplete')
if any('curl ' in data.lower() for data in (plugin, authn, authn_polish, openedx, runtime)):
    raise SystemExit('FPT UI source must not download assets during build')
print('[fpt-ui] Source guardrails PASS')
PYGUARD

# Tutor-MFE automatically turns a mount whose basename is frontend-app-APPNAME
# into a build-time source mount for APPNAME. Preserve the custom Learning MFE
# so rebuilding the shared mfe image cannot silently drop Unit Reset.
if [ "$SKIP_LEARNING_GUARD" != "1" ]; then
  [ -d "$LEARNING_REPO/.git" ] || fail "Custom Learning repo not found at $LEARNING_REPO. Set FPT_LEARNING_REPO to the correct path, or FPT_UI_SKIP_LEARNING_GUARD=1 only for an intentional test without Unit Reset."
  [ "$(basename "$LEARNING_REPO")" = "frontend-app-learning" ] || fail "Learning repo directory must be named frontend-app-learning for Tutor-MFE build mounts: $LEARNING_REPO"
  LEARNING_REPO_REAL="$(readlink -f "$LEARNING_REPO")"
  LEARNING_CURRENT_BRANCH="$(git -C "$LEARNING_REPO" rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
  [ "$LEARNING_CURRENT_BRANCH" = "$LEARNING_BRANCH" ] || fail "Learning repo must be on '$LEARNING_BRANCH', found '${LEARNING_CURRENT_BRANCH:-unknown}'"
  if ! git -C "$LEARNING_REPO" diff --quiet || ! git -C "$LEARNING_REPO" diff --cached --quiet; then
    fail "Learning repo has tracked local modifications. Commit/stash them before rebuilding the shared MFE image."
  fi
  UNIT_RESET_MARKER="$LEARNING_REPO/src/courseware/course/sequence/unit-reset/UnitResetButton.jsx"
  [ -s "$UNIT_RESET_MARKER" ] || fail "Unit Reset frontend marker is missing: $UNIT_RESET_MARKER"
  grep -Fq 'function getLmsBaseUrl()' "$UNIT_RESET_MARKER" || fail "Unit Reset marker file does not contain the expected implementation"
  log "Learning Unit Reset source: branch=$LEARNING_CURRENT_BRANCH commit=$(git -C "$LEARNING_REPO" rev-parse HEAD)"
else
  warn "FPT_UI_SKIP_LEARNING_GUARD=1: custom Learning/Unit Reset source protection is bypassed"
  LEARNING_REPO_REAL=""
fi

mount_is_configured() {
  local expected_real="$1"
  while IFS= read -r mount_name; do
    [ -n "$mount_name" ] || continue
    if [ "$(readlink -f "$mount_name" 2>/dev/null || true)" = "$expected_real" ]; then
      return 0
    fi
  done < <(tutor mounts list 2>/dev/null | sed -n 's/^- name: //p')
  return 1
}

if ! mount_is_configured "$REPO_ROOT_REAL"; then
  log "Adding edx-platform source mount: $REPO_ROOT"
  tutor mounts add "$REPO_ROOT"
else
  log "edx-platform source mount already configured"
fi

if [ "$SKIP_LEARNING_GUARD" != "1" ]; then
  if ! mount_is_configured "$LEARNING_REPO_REAL"; then
    log "Adding custom Learning build mount: $LEARNING_REPO"
    tutor mounts add "$LEARNING_REPO"
  else
    log "custom Learning build mount already configured"
  fi
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

# Verify Tutor-MFE resolved the custom Learning folder into the exact build
# context used by the shared production MFE image.
if [ "$SKIP_LEARNING_GUARD" != "1" ]; then
  MOUNTS_CHECK_FILE="$(mktemp)"
  tutor mounts list > "$MOUNTS_CHECK_FILE"
  python - "$MOUNTS_CHECK_FILE" "$LEARNING_REPO_REAL" <<'PYMOUNTS'
from pathlib import Path
import os
import sys
import yaml

entries = yaml.safe_load(Path(sys.argv[1]).read_text(encoding='utf-8')) or []
expected = os.path.realpath(sys.argv[2])
for entry in entries:
    name = entry.get('name') if isinstance(entry, dict) else None
    if not name or os.path.realpath(str(name)) != expected:
        continue
    build_mounts = entry.get('build_mounts') or []
    if any(item.get('image') == 'mfe' and item.get('context') == 'learning-src' for item in build_mounts if isinstance(item, dict)):
        print('[fpt-ui] Learning build mount mfe -> learning-src PASS')
        break
else:
    raise SystemExit('Custom Learning mount is not mapped to MFE build context learning-src')
PYMOUNTS
  rm -f "$MOUNTS_CHECK_FILE"
fi

GENERATED_LMS_SETTINGS="$TUTOR_ROOT/env/apps/openedx/settings/lms/production.py"
[ -f "$GENERATED_LMS_SETTINGS" ] || fail "Generated LMS production settings not found: $GENERATED_LMS_SETTINGS"
grep -Fq 'MFE_CONFIG["ENABLE_IMAGE_LAYOUT"] = False' "$GENERATED_LMS_SETTINGS" || fail "Rendered LMS settings did not receive ENABLE_IMAGE_LAYOUT=False"
grep -Fq 'MFE_CONFIG["SITE_NAME"] = "FPT Polytechnic"' "$GENERATED_LMS_SETTINGS" || fail "Rendered LMS settings did not receive FPT SITE_NAME"
log "Rendered LMS MFE configuration PASS"

GENERATED_OPENEDX="$TUTOR_ROOT/env/build/openedx/Dockerfile"
[ -f "$GENERATED_OPENEDX" ] || fail "Generated Open edX Dockerfile not found: $GENERATED_OPENEDX"

COPY_COUNT="$(grep -Fc 'COPY --from=edx-platform /fpt_indigo_ui/assets/' "$GENERATED_OPENEDX" || true)"
[ "$COPY_COUNT" -eq 5 ] || fail "Expected 5 vendored FPT asset COPY statements, found $COPY_COUNT"

if grep -Eq 'curl .*(caodang\.fpt\.edu\.vn|seeklogo\.com|wikimedia\.org|chungta\.vn)' "$GENERATED_OPENEDX"; then
  fail "Generated Open edX Dockerfile downloads FPT assets from the Internet"
fi

MFE_DOCKERFILE="$TUTOR_ROOT/env/plugins/mfe/build/mfe/Dockerfile"
MFE_ENV_CONFIG="$TUTOR_ROOT/env/plugins/mfe/build/mfe/env.config.jsx"
[ -f "$MFE_DOCKERFILE" ] || fail "Generated MFE Dockerfile not found: $MFE_DOCKERFILE"
[ -f "$MFE_ENV_CONFIG" ] || fail "Generated MFE env.config.jsx not found: $MFE_ENV_CONFIG"
grep -Fq 'FPT Polytechnic V10 edX full-screen authn' "$MFE_DOCKERFILE" || fail "Generated MFE Dockerfile does not contain the FPT Authn V10 layout patch"
grep -Fq 'FPT Polytechnic V11 authn edge-to-edge lock' "$MFE_DOCKERFILE" || fail "Generated MFE Dockerfile does not contain the FPT Authn V11 edge-to-edge patch"
grep -Fq 'FPT Polytechnic V12 white-logo dark-surface contract' "$MFE_DOCKERFILE" || fail "Generated MFE Dockerfile does not contain the FPT Authn V12 logo/surface contract"
grep -Fq 'FPT Polytechnic V13 solid-navy seamless wedge contract' "$MFE_DOCKERFILE" || fail "Generated MFE Dockerfile does not contain the FPT Authn V13 solid/seamless contract"
grep -Fq "RUN node - <<'JS2'" "$MFE_DOCKERFILE" || fail "Generated MFE Authn patch is not using Node.js"
grep -Fq "RUN node - <<'JS3'" "$MFE_DOCKERFILE" || fail "Generated MFE Authn polish patch is not using Node.js"
grep -Fq "import React from 'react';" "$MFE_ENV_CONFIG" || fail "Generated MFE env.config.jsx is missing explicit React import"
grep -Fq 'getConfig as getFptConfig' "$MFE_ENV_CONFIG" || fail "Generated MFE env.config.jsx is missing FPT getConfig alias"
grep -Fq 'const FptHeaderLogo' "$MFE_ENV_CONFIG" || fail "Generated MFE env.config.jsx is missing FPT header runtime"
grep -Fq 'const FptFooter' "$MFE_ENV_CONFIG" || fail "Generated MFE env.config.jsx is missing FPT footer runtime"
grep -Fq 'const FptLearnerBanner' "$MFE_ENV_CONFIG" || fail "Generated MFE env.config.jsx is missing FPT learner banner runtime"
log "Rendered MFE runtime configuration PASS"

if [ "$ALLOW_UNTESTED_BASELINE" != "1" ]; then
  grep -Fq 'ADD --keep-git-dir=true https://github.com/openedx/frontend-app-authn.git#release/ulmo.4 .' "$MFE_DOCKERFILE" || fail "Generated MFE Dockerfile is not sourcing Authn from release/ulmo.4"
fi

python - "$MFE_DOCKERFILE" <<'PYORDER'
from pathlib import Path
import sys

text = Path(sys.argv[1]).read_text(encoding='utf-8')
common = text.find('######## authn (common)')
source_copy = text.find('COPY --from=authn-src / /openedx/app', common)
layout_marker = text.find('FPT Polytechnic V10 edX full-screen authn', common)
polish_marker = text.find('FPT Polytechnic V11 authn edge-to-edge lock', common)
brand_marker = text.find('FPT Polytechnic V12 white-logo dark-surface contract', common)
seamless_marker = text.find('FPT Polytechnic V13 solid-navy seamless wedge contract', common)
dev = text.find('######## authn (dev)', common)
if min(common, source_copy, layout_marker, polish_marker, brand_marker, seamless_marker, dev) < 0:
    raise SystemExit('could not resolve Authn stage/V10/V11/V12/V13 patch markers in generated MFE Dockerfile')
if not (common < source_copy < layout_marker < polish_marker < brand_marker < seamless_marker < dev):
    raise SystemExit('Authn patch ordering is unsafe: source COPY -> V10 layout -> V11 edge-to-edge -> V12 logo/surface -> V13 solid/seamless -> authn build is required')
print('[fpt-ui] Generated Authn V10/V11/V12/V13 patch ordering PASS')
PYORDER

log "Generated MFE Dockerfile verified: $MFE_DOCKERFILE"
log "Setup OK: Ulmo.4/Tutor 21.0.9 baseline, clean source, rendered config, Unit Reset Learning build mount, plugins linked, assets vendored, patch ordering verified"
