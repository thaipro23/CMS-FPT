#!/usr/bin/env bash
set -euo pipefail

log() { printf '[fpt-ui-polish] %s\n' "$*"; }
fail() { printf '[fpt-ui-polish] ERROR: %s\n' "$*" >&2; exit 1; }

command -v python >/dev/null 2>&1 || fail "python is required"

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
[ -n "$REPO_ROOT" ] || fail "Run inside the CMS-FPT repository"
cd "$REPO_ROOT"

SLIDER="fpt_indigo_ui/patches/slider_images.patch"
OPENEDX="fpt_indigo_ui/patches/openedx.patch"
PLUGIN="tutor-plugins/fpt_indigo_ui.py"
RUNTIME="fpt_indigo_ui/patches/runtime.patch"
for file in "$SLIDER" "$OPENEDX" "$PLUGIN" "$RUNTIME"; do
  [ -s "$file" ] || fail "Missing $file"
done

log "Compiling responsive slider Python heredoc"
python - "$SLIDER" <<'PY'
from pathlib import Path
import sys

text = Path(sys.argv[1]).read_text(encoding='utf-8')
start = "RUN python - <<'PY_SLIDER_V11'\n"
end = "\nPY_SLIDER_V11"
if text.count(start) != 1:
    raise SystemExit('expected exactly one slider Python heredoc')
code = text.split(start, 1)[1].rsplit(end, 1)[0]
compile(code, sys.argv[1], 'exec')
print('[fpt-ui-polish] Responsive slider Python syntax PASS')
PY

log "Checking responsive/accessibility/contrast contracts"
python - "$SLIDER" "$OPENEDX" "$PLUGIN" "$RUNTIME" <<'PY'
from pathlib import Path
import sys

slider = Path(sys.argv[1]).read_text(encoding='utf-8')
openedx = Path(sys.argv[2]).read_text(encoding='utf-8')
plugin = Path(sys.argv[3]).read_text(encoding='utf-8')
runtime = Path(sys.argv[4]).read_text(encoding='utf-8')

for marker in (
    'FPT_DISCOVERY_V11_IMAGE_ONLY',
    'role="group" aria-label="Chọn slide"',
    'aria-label="Slide trước"',
    'aria-label="Slide sau"',
    "root.addEventListener('focusout',function(e){if(!root.contains(e.relatedTarget)){start()}});",
    '@media(max-width:820px){.fpt-hero__stage{aspect-ratio:4/5}',
    '@media(max-width:520px){.fpt-hero{margin-bottom:22px}.fpt-hero__arrow{display:none}',
    '@media(prefers-reduced-motion:reduce){.fpt-dot{transition:none!important}}',
):
    if marker not in slider:
        raise SystemExit(f'responsive slider accessibility/layout marker missing: {marker}')

if 'fpt-slide__copy' in slider or 'fpt-collage' in slider:
    raise SystemExit('legacy slider text/collage markup remains')

assets = (
    'fpt-slider-01-male-desktop.webp',
    'fpt-slider-01-male-mobile.webp',
    'fpt-slider-02-female-desktop.webp',
    'fpt-slider-02-female-mobile.webp',
    'fpt-slider-03-group-desktop.webp',
    'fpt-slider-03-group-mobile.webp',
)
for name in assets:
    if slider.count(f'/static/indigo/images/fpt/{name}') != 1:
        raise SystemExit(f'slider static reference mismatch: {name}')
    if slider.count(f'/fpt_indigo_ui/assets/{name} /openedx/staticfiles/indigo/images/fpt/{name}') != 1:
        raise SystemExit(f'slider vendoring COPY mismatch: {name}')

for marker in (
    'FPT_LMS_FOOTER_V10_CONTRAST',
    'background:#071A33!important',
    'color:#FFFFFF!important',
    'color:#8ED0FF!important',
):
    if marker not in openedx:
        raise SystemExit(f'legacy LMS footer contrast marker missing: {marker}')

for marker in (
    '.fpt-ui-footer{border-top:1px solid #2F4765;background:#071A33',
    '.fpt-ui-footer__title{color:#FFFFFF',
    '.fpt-ui-footer a{color:#8ED0FF',
    '.fpt-ui-footer__address{color:#E3ECF6',
    '.theme-toggle-button,.light-theme-icon,.dark-theme-icon,.toggle-switch{display:none!important}',
):
    if marker not in runtime:
        raise SystemExit(f'MFE final branding/contrast marker missing: {marker}')

order = [
    plugin.find('_read_patch("openedx.patch")'),
    plugin.find('_read_patch("slider_images.patch")'),
    plugin.find('_read_patch("native_logo.patch")'),
]
if any(pos < 0 for pos in order) or order != sorted(order):
    raise SystemExit('Open edX patch order must be openedx -> slider_images -> native_logo')
if 'MFE_CONFIG["INDIGO_ENABLE_DARK_TOGGLE"] = False' not in plugin:
    raise SystemExit('MFE dark toggle remains enabled')

print('[fpt-ui-polish] Responsive slider/light-only/footer contrast contract PASS')
PY

log "ALL POLISH TESTS PASS"
