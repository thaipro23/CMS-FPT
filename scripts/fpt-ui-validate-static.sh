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
bash -n scripts/fpt-ui-setup.sh
bash -n scripts/fpt-ui-build.sh
bash -n scripts/fpt-ui-smoke.sh
bash -n scripts/fpt-ui-validate-static.sh
python -m py_compile tutor-plugins/fpt_indigo_ui.py

log "Checking vendored assets"
for asset in \
  fpt_indigo_ui/assets/fpt-polytechnic-logo.png \
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

log "Extracting and syntax-checking Authn Node patch"
python - "$TMP_DIR/authn.js" <<'PY'
from pathlib import Path
import sys

text = Path('fpt_indigo_ui/patches/authn.patch').read_text(encoding='utf-8')
start_marker = "RUN node - <<'JS2'\n"
end_marker = "\nJS2"
if text.count(start_marker) != 1:
    raise SystemExit('expected exactly one Authn Node heredoc')
code = text.split(start_marker, 1)[1].rsplit(end_marker, 1)[0]
Path(sys.argv[1]).write_text(code, encoding='utf-8')
PY
node --check "$TMP_DIR/authn.js"

log "Applying Authn patch to an isolated fixture"
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
node "$TMP_DIR/authn-fixture.js"

python - "$FIXTURE_APP" <<'PY'
from pathlib import Path
import sys

app = Path(sys.argv[1])
layout = app / 'src/base-container/components/default-layout'
for name in ('LargeLayout.jsx', 'MediumLayout.jsx', 'SmallLayout.jsx'):
    path = layout / name
    if not path.is_file():
        raise SystemExit(f'missing generated {name}')
    text = path.read_text(encoding='utf-8')
    if "import React from 'react';" not in text:
        raise SystemExit(f'{name} does not preserve React import')
    if 'export default' not in text or 'FPT Polytechnic' not in text:
        raise SystemExit(f'{name} branding/component output is incomplete')

large = (layout / 'LargeLayout.jsx').read_text(encoding='utf-8')
if large.count('className="fpt-auth-wedge"') != 1:
    raise SystemExit('LargeLayout must contain exactly one orange wedge')

scss = (app / 'src/index.scss').read_text(encoding='utf-8')
required = [
    'FPT Polytechnic V8 production branding overlay',
    '.fpt-auth-wedge',
    'clip-path:polygon(82% 0,100% 0,42% 100%,0 100%)',
    'flex:0 0 53%',
    '@media (min-width:768px) and (max-width:1199.98px)',
    '@media (max-width:767.98px)',
]
for marker in required:
    if marker not in scss:
        raise SystemExit(f'missing Authn responsive marker: {marker}')
if scss.count('.fpt-auth-wedge {') != 1:
    raise SystemExit('wedge CSS must be defined exactly once')
print('[fpt-ui-static] Authn fixture PASS')
PY

log "Extracting, compiling and applying Open edX patch twice"
python - "$TMP_DIR/openedx.py" <<'PY'
from pathlib import Path
import sys

text = Path('fpt_indigo_ui/patches/openedx.patch').read_text(encoding='utf-8')
start_marker = "RUN python - <<'PY2'\n"
end_marker = "\nPY2"
if text.count(start_marker) != 1:
    raise SystemExit('expected exactly one Open edX Python heredoc')
code = text.split(start_marker, 1)[1].rsplit(end_marker, 1)[0]
compile(code, '<openedx.patch>', 'exec')
Path(sys.argv[1]).write_text(code, encoding='utf-8')
PY

FIXTURE_OPENEDX="$TMP_DIR/openedx"
mkdir -p \
  "$FIXTURE_OPENEDX/themes/indigo/lms/templates/courseware" \
  "$FIXTURE_OPENEDX/themes/indigo/lms/templates" \
  "$FIXTURE_OPENEDX/edx-platform/lms/templates/header"
printf '<html><body><section class="courses-container">COURSES</section></body></html>\n' > "$FIXTURE_OPENEDX/themes/indigo/lms/templates/courseware/courses.html"
printf '<footer>upstream</footer>\n' > "$FIXTURE_OPENEDX/themes/indigo/lms/templates/footer.html"
printf '<h1 class="header-logo">upstream</h1>\n' > "$FIXTURE_OPENEDX/edx-platform/lms/templates/header/navbar-logo-header.html"

python - "$TMP_DIR/openedx.py" "$TMP_DIR/openedx-fixture.py" "$FIXTURE_OPENEDX" <<'PY'
from pathlib import Path
import sys

source = Path(sys.argv[1]).read_text(encoding='utf-8')
source = source.replace('/openedx/', Path(sys.argv[3]).as_posix().rstrip('/') + '/')
Path(sys.argv[2]).write_text(source, encoding='utf-8')
PY
python "$TMP_DIR/openedx-fixture.py"
python "$TMP_DIR/openedx-fixture.py"

python - "$FIXTURE_OPENEDX" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])
courses = (root / 'themes/indigo/lms/templates/courseware/courses.html').read_text(encoding='utf-8')
footer = (root / 'themes/indigo/lms/templates/footer.html').read_text(encoding='utf-8')
if courses.count('FPT_DISCOVERY_V8_START') != 1 or courses.count('FPT_DISCOVERY_V8_END') != 1:
    raise SystemExit('Discovery patch is not idempotent')
if courses.count('id="fpt-hero-slider"') != 1:
    raise SystemExit('Discovery hero must exist exactly once')
for marker in ('.fpt-card--main', '.fpt-card--top', '.fpt-card--bottom', '@media(max-width:820px)', '@media(max-width:520px)'):
    if marker not in courses:
        raise SystemExit(f'Discovery responsive/card marker missing: {marker}')
if 'fpt-lms-footer' not in footer or 'fpt-polytechnic-logo.png' not in footer:
    raise SystemExit('Legacy footer branding missing')
print('[fpt-ui-static] Open edX fixture/idempotence PASS')
PY

log "Checking plugin/runtime production guardrails"
python - <<'PY'
from pathlib import Path

plugin = Path('tutor-plugins/fpt_indigo_ui.py').read_text(encoding='utf-8')
authn = Path('fpt_indigo_ui/patches/authn.patch').read_text(encoding='utf-8')
runtime = Path('fpt_indigo_ui/patches/runtime.patch').read_text(encoding='utf-8')
openedx = Path('fpt_indigo_ui/patches/openedx.patch').read_text(encoding='utf-8')

checks = [
    ('mfe-dockerfile-pre-npm-build-authn' in plugin, 'safe Authn pre-build hook missing'),
    ('mfe-dockerfile-post-npm-install-authn' not in plugin, 'unsafe Authn post-install hook present'),
    ("import React from 'react';" in plugin, 'runtime JSX React import missing'),
    ('getConfig as getFptConfig' in plugin, 'runtime getConfig alias missing'),
    (authn.count("import React from 'react';") == 3, 'Authn React imports must be exactly three'),
    (authn.count('<div className="fpt-auth-wedge"') == 1, 'single-wedge contract violated'),
    ('useEffect(' not in runtime and 'getConfig()' not in runtime, 'unscoped runtime dependency present'),
    ('getFptConfig()' in runtime, 'runtime scoped config alias unused'),
    (openedx.count('COPY --from=edx-platform /fpt_indigo_ui/assets/') == 4, 'vendored asset COPY count must be four'),
]
for ok, message in checks:
    if not ok:
        raise SystemExit(message)
for path, data in [('plugin', plugin), ('authn', authn), ('runtime', runtime), ('openedx', openedx)]:
    if 'curl ' in data.lower():
        raise SystemExit(f'external download command found in {path}')
print('[fpt-ui-static] Production guardrails PASS')
PY

log "ALL STATIC/FIXTURE TESTS PASS"
