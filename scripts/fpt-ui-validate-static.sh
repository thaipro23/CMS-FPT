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
python -m py_compile tutor-plugins/fpt_indigo_ui.py tutor-plugins/openedx_unit_reset.py openedx_unit_reset/setup.py

log "Checking vendored assets"
for asset in \
  fpt_indigo_ui/assets/fpt-polytechnic-logo.png \
  fpt_indigo_ui/assets/fpt-polytechnic-logo-white.png \
  fpt_indigo_ui/assets/fpt-students.png \
  fpt_indigo_ui/assets/fpt-campus-primary.jpg \
  fpt_indigo_ui/assets/fpt-campus-secondary.jpg
do
  [ -s "$asset" ] || fail "Missing/empty asset: $asset"
  bytes="$(wc -c < "$asset" | tr -d ' ')"
  [ "$bytes" -gt 1000 ] || fail "Asset too small: $asset ($bytes bytes)"
done

TMP_DIR="$(mktemp -d)"
cleanup() { rm -rf "$TMP_DIR"; }
trap cleanup EXIT

extract_node_patch() {
  local patch="$1" start="$2" end="$3" out="$4"
  python - "$patch" "$start" "$end" "$out" <<'PY'
from pathlib import Path
import sys
text = Path(sys.argv[1]).read_text(encoding='utf-8')
start, end = sys.argv[2], sys.argv[3]
if text.count(start) != 1:
    raise SystemExit(f'expected exactly one heredoc start in {sys.argv[1]}')
code = text.split(start, 1)[1].rsplit(end, 1)[0]
Path(sys.argv[4]).write_text(code, encoding='utf-8')
PY
  node --check "$out"
}

log "Validating Authn V10/V11 source and fixture"
extract_node_patch fpt_indigo_ui/patches/authn.patch "RUN node - <<'JS2'"$'\n' $'\n''JS2' "$TMP_DIR/authn.js"
extract_node_patch fpt_indigo_ui/patches/authn_polish.patch "RUN node - <<'JS3'"$'\n' $'\n''JS3' "$TMP_DIR/authn-polish.js"

FIXTURE_APP="$TMP_DIR/openedx/app"
mkdir -p "$FIXTURE_APP/src/base-container/components/default-layout"
printf '/* fixture */\n' > "$FIXTURE_APP/src/index.scss"

python - "$TMP_DIR/authn.js" "$TMP_DIR/authn-fixture.js" "$FIXTURE_APP" <<'PY'
from pathlib import Path
import sys
source = Path(sys.argv[1]).read_text(encoding='utf-8')
root = Path(sys.argv[3]).as_posix()
source = source.replace('/openedx/app/src/base-container/components/default-layout', root + '/src/base-container/components/default-layout')
source = source.replace('/openedx/app/src/index.scss', root + '/src/index.scss')
Path(sys.argv[2]).write_text(source, encoding='utf-8')
PY
python - "$TMP_DIR/authn-polish.js" "$TMP_DIR/authn-polish-fixture.js" "$FIXTURE_APP" <<'PY'
from pathlib import Path
import sys
source = Path(sys.argv[1]).read_text(encoding='utf-8')
root = Path(sys.argv[3]).as_posix()
source = source.replace('/openedx/app/src/base-container/components/default-layout', root + '/src/base-container/components/default-layout')
source = source.replace('/openedx/app/src/index.scss', root + '/src/index.scss')
Path(sys.argv[2]).write_text(source, encoding='utf-8')
PY

node "$TMP_DIR/authn-fixture.js"
node "$TMP_DIR/authn-fixture.js"
node "$TMP_DIR/authn-polish-fixture.js"
node "$TMP_DIR/authn-polish-fixture.js"

python - "$FIXTURE_APP" <<'PY'
from pathlib import Path
import re
import sys
app = Path(sys.argv[1])
layout = app / 'src/base-container/components/default-layout'
for name in ('LargeLayout.jsx', 'MediumLayout.jsx', 'SmallLayout.jsx'):
    text = (layout / name).read_text(encoding='utf-8')
    if "fpt-polytechnic-logo-white.png" not in text:
        raise SystemExit(f'{name} does not use the real white FPT logo')
    if 'fpt-campus-primary.jpg' in text or 'backgroundImage' in text:
        raise SystemExit(f'{name} still contains a login background image')
    if 'Start learning' not in text or 'with CMS' not in text:
        raise SystemExit(f'{name} login copy is incomplete')
    if text.count('<img className="fpt-auth-logo fpt-auth-logo--white"') != 1:
        raise SystemExit(f'{name} must render exactly one native white FPT logo')
    if '<Image ' in text or '<Hyperlink ' in text:
        raise SystemExit(f'{name} still relies on wrapper components for the critical logo')
    if 'getConfig().LMS_BASE_URL || window.location.origin' not in text:
        raise SystemExit(f'{name} white logo URL has no runtime fallback')
    if 'getConfig().MARKETING_SITE_BASE_URL || getFptBaseUrl()' not in text:
        raise SystemExit(f'{name} logo home link has no safe fallback')
large = (layout / 'LargeLayout.jsx').read_text(encoding='utf-8')
if large.count('className="fpt-auth-wedge"') != 1:
    raise SystemExit('LargeLayout must contain exactly one diagonal wedge')

scss = (app / 'src/index.scss').read_text(encoding='utf-8')
required = [
    'FPT Polytechnic V10 edX full-screen authn',
    'FPT Polytechnic V11 authn edge-to-edge lock',
    'FPT Polytechnic V12 white-logo dark-surface contract',
    '--fpt-auth-brand-surface:#0B3B82',
    '--fpt-auth-form-surface:#FFFFFF',
    '.fpt-auth-logo-link{display:inline-flex!important;visibility:visible!important;opacity:1!important;',
    '.fpt-auth-logo,.fpt-auth-logo--white{display:block!important;height:auto!important;filter:none!important;visibility:visible!important;opacity:1!important}',
    'width:100vw!important',
    'max-width:none!important',
    'border-radius:0!important',
    'background-image:none!important',
    'grid-template-columns:var(--fpt-auth-left)',
    'clip-path:polygon(0 0,100% 0,0 100%)',
    'height:100dvh!important',
]
for marker in required:
    if marker not in scss:
        raise SystemExit(f'missing final Authn marker: {marker}')
if scss.count('FPT Polytechnic V10 edX full-screen authn') != 1:
    raise SystemExit('Authn V10 CSS is not idempotent')
if scss.count('FPT Polytechnic V11 authn edge-to-edge lock') != 1:
    raise SystemExit('Authn V11 CSS is not idempotent')
if scss.count('FPT Polytechnic V12 white-logo dark-surface contract') != 1:
    raise SystemExit('Authn V12 CSS is not idempotent')

surface_match = re.search(r'--fpt-auth-brand-surface:(#[0-9A-Fa-f]{6})', scss)
if not surface_match:
    raise SystemExit('Authn brand surface colour is not explicit')

def luminance(hex_colour):
    channels = [int(hex_colour[index:index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4 for value in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

surface_luminance = luminance(surface_match.group(1))
white_luminance = luminance('#FFFFFF')
contrast = (white_luminance + 0.05) / (surface_luminance + 0.05)
if contrast < 7:
    raise SystemExit(f'white Authn logo contrast regression: {contrast:.2f}:1')

print(f'[fpt-ui-static] Authn edge-to-edge/white-logo contract PASS contrast={contrast:.2f}:1')
PY

log "Checking final Hero/header/footer/theme source contracts"
python - <<'PY'
from pathlib import Path

openedx = Path('fpt_indigo_ui/patches/openedx.patch').read_text(encoding='utf-8')
authn_polish = Path('fpt_indigo_ui/patches/authn_polish.patch').read_text(encoding='utf-8')
balance = Path('fpt_indigo_ui/patches/openedx_balance.patch').read_text(encoding='utf-8')
native = Path('fpt_indigo_ui/patches/native_logo.patch').read_text(encoding='utf-8')
runtime = Path('fpt_indigo_ui/patches/runtime.patch').read_text(encoding='utf-8')
plugin = Path('tutor-plugins/fpt_indigo_ui.py').read_text(encoding='utf-8')
setup = Path('scripts/fpt-ui-setup.sh').read_text(encoding='utf-8')
build = Path('scripts/fpt-ui-clean-openedx-rebuild.sh').read_text(encoding='utf-8')
standard_build = Path('scripts/fpt-ui-build.sh').read_text(encoding='utf-8')
smoke = Path('scripts/fpt-ui-smoke.sh').read_text(encoding='utf-8')

if openedx.count('COPY --from=edx-platform /fpt_indigo_ui/assets/') != 5:
    raise SystemExit('Open edX must vendor exactly five FPT UI assets')
if '/fpt_indigo_ui/assets/fpt-polytechnic-logo-white.png /openedx/staticfiles/indigo/images/fpt/fpt-polytechnic-logo-white.png' not in openedx:
    raise SystemExit('Open edX does not explicitly vendor the Authn white logo')
if 'FPT Polytechnic V12 white-logo dark-surface contract' not in authn_polish:
    raise SystemExit('Authn V12 white-logo/dark-surface patch is missing')
if '  fpt-polytechnic-logo-white.png\n' not in smoke:
    raise SystemExit('deployed static smoke test does not request the white logo')

pairs = [
    ('Học tập chủ động.', 'Phát triển mỗi ngày.'),
    ('Học để làm được.', 'Học để đi xa hơn.'),
    ('Một hành trình học tập.', 'Một trải nghiệm thống nhất.'),
]
for first, second in pairs:
    if first not in openedx or second not in openedx:
        raise SystemExit(f'Hero source copy missing: {first} / {second}')
    final = f'<span class="fpt-headline-line">{first}</span><em class="fpt-headline-line">{second}</em>'
    if final not in balance:
        raise SystemExit(f'Hero explicit two-line contract missing: {first}')
for marker in (
    '.fpt-headline-line{display:block;white-space:nowrap',
    '.fpt-kicker:before{display:none!important}',
    'align-items:flex-start;text-align:left',
    'FPT_DISCOVERY_V10_MOBILE_STACK',
    '.fpt-slide{grid-template-columns:minmax(0,1fr);gap:18px;',
    '.fpt-slide__copy{width:100%;min-width:0;max-width:680px}',
    '.fpt-collage{width:100%;max-width:390px;min-width:0;',
):
    if marker not in balance:
        raise SystemExit(f'Hero alignment marker missing: {marker}')

for marker in (
    'theme-toggle-button.html',
    'FPT_LIGHT_ONLY_THEME_TOGGLE_DISABLED',
    "classList.remove('indigo-dark-theme')",
    'FPT_LMS_FOOTER_V10_CONTRAST',
    'background:#071A33!important',
    'color:#FFFFFF!important',
    'color:#8ED0FF!important',
):
    if marker not in balance:
        raise SystemExit(f'legacy LMS final contract missing: {marker}')

for marker in (
    'themes/indigo/lms/static/images',
    'logo_*.png',
    'logo-white_*.png',
    'fpt-polytechnic-logo-white.png',
    '[fpt-native-logo] patched collected hashes',
):
    if marker not in native:
        raise SystemExit(f'native/hashed logo contract missing: {marker}')

for marker in (
    "FptHeaderLogo = () => null",
    "fpt-polytechnic-logo-white.png",
    '.theme-toggle-button,.light-theme-icon,.dark-theme-icon,.toggle-switch{display:none!important}',
    '.fpt-ui-footer{border-top:1px solid #2F4765;background:#071A33',
    '.fpt-ui-footer__title{color:#FFFFFF',
    '.fpt-ui-footer a{color:#8ED0FF',
    '.fpt-ui-footer__address{color:#E3ECF6',
    '.fpt-ui-footer__copyright{border-top:1px solid #2F4765',
):
    if marker not in runtime:
        raise SystemExit(f'MFE final branding/contrast marker missing: {marker}')

if 'MFE_CONFIG["INDIGO_ENABLE_DARK_TOGGLE"] = False' not in plugin:
    raise SystemExit('MFE dark toggle is not disabled')
if 'logo_slot' in plugin and 'Never override logo_slot' not in plugin:
    raise SystemExit('unexpected custom logo slot override present')
if 'MFE_CONFIG["ENABLE_IMAGE_LAYOUT"] = False' not in plugin:
    raise SystemExit('Authn DefaultLayout pin missing')
if 'EXPECTED_COMMON_VERSION="release/ulmo.4"' not in setup or 'EXPECTED_TUTOR_VERSION="21.0.9"' not in setup:
    raise SystemExit('Ulmo.4/Tutor baseline guards missing')
if any(marker not in setup for marker in (
    'FPT Polytechnic V10 edX full-screen authn',
    'FPT Polytechnic V11 authn edge-to-edge lock',
    'FPT Polytechnic V12 white-logo dark-surface contract',
)):
    raise SystemExit('generated MFE Authn guard is not synchronized to V10/V11/V12')
if 'FPT Polytechnic V8 production branding overlay' in setup:
    raise SystemExit('stale generated MFE Authn V8 guard remains in setup')
if 'BUILDX_BUILDER=default tutor images build openedx' not in build:
    raise SystemExit('Open edX default-builder isolation missing')
if 'BUILDX_BUILDER="$MFE_BUILDER" tutor images build mfe' not in build:
    raise SystemExit('dedicated cached MFE build missing')
for data, name in ((build, 'clean rebuild'), (standard_build, 'standard build')):
    if 'grep -R -Fq -- "--fpt-auth-brand-surface"' not in data:
        raise SystemExit(f'{name} does not verify the durable compiled Authn V12 surface token')
    if 'grep -R -Fq "FPT Polytechnic V12 white-logo dark-surface contract"' in data:
        raise SystemExit(f'{name} incorrectly expects a source comment to survive CSS minimization')
if 'docker buildx prune' in build or 'docker system prune' in build or 'docker volume prune' in build:
    raise SystemExit('destructive prune command found in clean build path')

print('[fpt-ui-static] Hero + native logo + light-only + footer contrast PASS')
PY

log "ALL STATIC/FIXTURE TESTS PASS"
