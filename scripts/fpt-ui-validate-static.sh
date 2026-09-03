#!/usr/bin/env bash
set -euo pipefail

log() { printf '[fpt-ui-static] %s\n' "$*"; }
fail() { printf '[fpt-ui-static] ERROR: %s\n' "$*" >&2; exit 1; }

command -v python >/dev/null 2>&1 || fail "python is required"
command -v node >/dev/null 2>&1 || fail "node is required"

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
[ -n "$REPO_ROOT" ] || fail "Run inside the CMS-FPT repository"
cd "$REPO_ROOT"

log "Checking shell/Python syntax"
for script in \
  scripts/fpt-ui-setup.sh \
  scripts/fpt-ui-build.sh \
  scripts/fpt-ui-clean-openedx-rebuild.sh \
  scripts/fpt-ui-openedx-overlay.sh \
  scripts/fpt-ui-smoke.sh \
  scripts/fpt-ui-validate-static.sh \
  scripts/fpt-ui-validate-polish.sh \
  scripts/fpt-ui-validate-homepage-slider.sh \
  scripts/fpt-ulmo-upgrade.sh
do
  bash -n "$script"
done

python -m py_compile \
  tutor-plugins/fpt_indigo_ui.py \
  tutor-plugins/openedx_unit_reset.py \
  openedx_unit_reset/setup.py \
  scripts/fpt-ui-validate-assets.py

log "Checking vendored assets"
python scripts/fpt-ui-validate-assets.py

TMP_DIR="$(mktemp -d)"
cleanup() { rm -rf "$TMP_DIR"; }
trap cleanup EXIT

log "Checking canonical Authn JavaScript syntax"
python - fpt_indigo_ui/patches/authn.patch "$TMP_DIR/authn.js" <<'PY'
from pathlib import Path
import sys

text = Path(sys.argv[1]).read_text(encoding='utf-8')
start = "RUN node - <<'JS2'\n"
end = "\nJS2"
if text.count(start) != 1:
    raise SystemExit('expected exactly one canonical Authn JS heredoc')
code = text.split(start, 1)[1].rsplit(end, 1)[0]
Path(sys.argv[2]).write_text(code, encoding='utf-8')
PY
node --check "$TMP_DIR/authn.js"

log "Checking current FPT UI source contracts"
python - <<'PY'
from pathlib import Path

patch_dir = Path('fpt_indigo_ui/patches')
authn = (patch_dir / 'authn.patch').read_text(encoding='utf-8')
authoring = (patch_dir / 'authoring.patch').read_text(encoding='utf-8')
openedx = (patch_dir / 'openedx.patch').read_text(encoding='utf-8')
slider = (patch_dir / 'slider_images.patch').read_text(encoding='utf-8')
native = (patch_dir / 'native_logo.patch').read_text(encoding='utf-8')
runtime = (patch_dir / 'runtime.patch').read_text(encoding='utf-8')
plugin = Path('tutor-plugins/fpt_indigo_ui.py').read_text(encoding='utf-8')

stale = (
    'authn_polish.patch',
    'authn_sso_only.patch',
    'openedx_polish.patch',
    'openedx_balance.patch',
    'homepage_slider.patch',
)
for name in stale:
    if (patch_dir / name).exists():
        raise SystemExit(f'stale superseded patch still exists: {name}')
    if f'_read_patch("{name}")' in plugin:
        raise SystemExit(f'plugin still composes superseded patch: {name}')

for marker in (
    'Canonical FPT Polytechnic Authn customization',
    'FPT_AUTHN_CANONICAL_LAYOUT_V1',
):
    if marker not in authn:
        raise SystemExit(f'canonical Authn marker missing: {marker}')

if 'FPT_AUTHORING_ACMS_ONLY_QUESTION_CREATION_V1' not in authoring:
    raise SystemExit('ACMS-only Authoring marker missing')

for marker in (
    'FPT_DISCOVERY_V8_START',
    'FPT_DISCOVERY_V8_END',
    'FPT_LMS_FOOTER_V10_CONTRAST',
    'background:#071A33!important',
    'color:#FFFFFF!important',
    'color:#8ED0FF!important',
):
    if marker not in openedx:
        raise SystemExit(f'legacy LMS branding/footer marker missing: {marker}')

assets = (
    'fpt-slider-01-male-desktop.webp',
    'fpt-slider-01-male-mobile.webp',
    'fpt-slider-02-female-desktop.webp',
    'fpt-slider-02-female-mobile.webp',
    'fpt-slider-03-group-desktop.webp',
    'fpt-slider-03-group-mobile.webp',
)
for marker in (
    'FPT_DISCOVERY_V11_IMAGE_ONLY',
    'aspect-ratio:1920/650',
    'aspect-ratio:4/5',
    'role="group" aria-label="Chọn slide"',
    "timer=setInterval(function(){show(i+1)},7000)",
    "root.addEventListener('focusout',function(e){if(!root.contains(e.relatedTarget)){start()}});",
    'media="(max-width: 820px)"',
):
    if marker not in slider:
        raise SystemExit(f'responsive slider marker missing: {marker}')
if 'fpt-slide__copy' in slider or 'fpt-collage' in slider:
    raise SystemExit('responsive slider patch still contains legacy text/collage markup')
for name in assets:
    if slider.count(f'/fpt_indigo_ui/assets/{name} /openedx/staticfiles/indigo/images/fpt/{name}') != 1:
        raise SystemExit(f'slider COPY contract missing/duplicated: {name}')
    if slider.count(f'/static/indigo/images/fpt/{name}') != 1:
        raise SystemExit(f'slider static reference missing/duplicated: {name}')

for marker in (
    'themes/indigo/lms/static/images',
    'logo_*.png',
    'logo-white_*.png',
    'fpt-polytechnic-logo-white.png',
    '[fpt-native-logo] patched collected hashes',
):
    if marker not in native:
        raise SystemExit(f'native logo marker missing: {marker}')

for marker in (
    'const FptHeaderLogo = () => null;',
    '.theme-toggle-button,.light-theme-icon,.dark-theme-icon,.toggle-switch{display:none!important}',
    '.fpt-ui-footer{border-top:1px solid #2F4765;background:#071A33',
    '.fpt-ui-footer__title{color:#FFFFFF',
    '.fpt-ui-footer a{color:#8ED0FF',
    '.fpt-ui-footer__address{color:#E3ECF6',
):
    if marker not in runtime:
        raise SystemExit(f'MFE runtime branding marker missing: {marker}')

for marker in (
    '# FPT_TIMEZONE_V1',
    'TIME_ZONE = "{FPT_TIME_ZONE}"',
    'USE_TZ = True',
    'MFE_CONFIG["INDIGO_ENABLE_DARK_TOGGLE"] = False',
    'MFE_CONFIG["ENABLE_IMAGE_LAYOUT"] = False',
    '_read_patch("authn.patch")',
    '_read_patch("authoring.patch")',
    '_read_patch("runtime.patch")',
    '_read_patch("openedx.patch")',
    '_read_patch("slider_images.patch")',
    '_read_patch("native_logo.patch")',
):
    if marker not in plugin:
        raise SystemExit(f'Tutor plugin contract missing: {marker}')

order = [
    plugin.find('_read_patch("openedx.patch")'),
    plugin.find('_read_patch("slider_images.patch")'),
    plugin.find('_read_patch("native_logo.patch")'),
]
if any(pos < 0 for pos in order) or order != sorted(order):
    raise SystemExit('Open edX patch order must be openedx -> slider_images -> native_logo')

print('[fpt-ui-static] Current source contracts PASS')
PY

log "Checking shared homepage/courses slider fixture"
bash scripts/fpt-ui-validate-homepage-slider.sh

log "ALL STATIC TESTS PASS"
