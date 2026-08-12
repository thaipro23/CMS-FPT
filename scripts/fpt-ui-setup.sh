#!/usr/bin/env bash
set -euo pipefail

log() { printf '[fpt-ui] %s\n' "$*"; }
fail() { printf '[fpt-ui] ERROR: %s\n' "$*" >&2; exit 1; }

command -v git >/dev/null 2>&1 || fail "git is required"
command -v tutor >/dev/null 2>&1 || fail "Tutor is not available in PATH. Activate the Tutor virtualenv first."
command -v python >/dev/null 2>&1 || fail "python is required"

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
[ -n "$REPO_ROOT" ] || fail "Run this script from inside the CMS-FPT repository"
REPO_ROOT_REAL="$(readlink -f "$REPO_ROOT")"
PLUGIN_ROOT="$(tutor plugins printroot)"
ASSET_DIR="$REPO_ROOT/fpt_indigo_ui/assets"
LEGACY_ASSET_DIR="$REPO_ROOT/tutor-plugins/fpt-assets"

EXPECTED_ASSETS=(
  fpt-polytechnic-logo.png
  fpt-students.png
  fpt-campus-primary.jpg
  fpt-campus-secondary.jpg
)

log "Repository: $REPO_ROOT"
log "Tutor plugin root: $PLUGIN_ROOT"

# One-time migration for the UAT layout used before assets were vendored under
# fpt_indigo_ui/assets. Fresh clones should already contain these files in Git.
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

# Ensure the local edx-platform checkout is exposed to Tutor as a source build
# context. Resolve symlinks so /opt/openedx/edx-platform and /opt/openedx/CMS-FPT
# are treated as the same checkout.
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

GENERATED_OPENEDX="$HOME/.local/share/tutor/env/build/openedx/Dockerfile"
[ -f "$GENERATED_OPENEDX" ] || fail "Generated Open edX Dockerfile not found: $GENERATED_OPENEDX"

COPY_COUNT="$(grep -Fc 'COPY --from=edx-platform /fpt_indigo_ui/assets/' "$GENERATED_OPENEDX" || true)"
[ "$COPY_COUNT" -eq 4 ] || fail "Expected 4 vendored FPT asset COPY statements, found $COPY_COUNT"

if grep -Eq 'curl .*(caodang\.fpt\.edu\.vn|seeklogo\.com)' "$GENERATED_OPENEDX"; then
  fail "Generated Open edX Dockerfile still downloads FPT assets from the Internet"
fi

log "Setup OK: Tutor plugins linked, assets vendored, environment rendered"
