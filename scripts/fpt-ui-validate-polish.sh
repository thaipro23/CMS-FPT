#!/usr/bin/env bash
set -euo pipefail

log() { printf '[fpt-ui-polish] %s\n' "$*"; }
fail() { printf '[fpt-ui-polish] ERROR: %s\n' "$*" >&2; exit 1; }

command -v python >/dev/null 2>&1 || fail "python is required"

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
[ -n "$REPO_ROOT" ] || fail "Run inside the CMS-FPT repository"
cd "$REPO_ROOT"

PATCH="fpt_indigo_ui/patches/openedx_polish.patch"
PLUGIN="tutor-plugins/fpt_indigo_ui.py"
RUNTIME="fpt_indigo_ui/patches/runtime.patch"
[ -s "$PATCH" ] || fail "Missing polish patch: $PATCH"

TMP_DIR="$(mktemp -d)"
cleanup() { rm -rf "$TMP_DIR"; }
trap cleanup EXIT

log "Extracting and compiling polish Python heredoc"
python - "$PATCH" "$TMP_DIR/polish.py" <<'PY'
from pathlib import Path
import sys

text = Path(sys.argv[1]).read_text(encoding='utf-8')
start = "RUN python - <<'PY3'\n"
end = "\nPY3"
if text.count(start) != 1:
    raise SystemExit('expected exactly one polish Python heredoc')
code = text.split(start, 1)[1].rsplit(end, 1)[0]
compile(code, '<openedx_polish.patch>', 'exec')
Path(sys.argv[2]).write_text(code, encoding='utf-8')
PY

FIXTURE="$TMP_DIR/openedx"
mkdir -p "$FIXTURE/themes/indigo/lms/templates/courseware" "$FIXTURE/themes/indigo/lms/templates"
cat > "$FIXTURE/themes/indigo/lms/templates/courseware/courses.html" <<'HTML'
<section id="fpt-hero-slider">
  <div class="fpt-hero__nav" role="tablist" aria-label="Chọn slide"></div>
  <script>
  root.addEventListener('focusin',stop);root.addEventListener('focusout',start);
  </script>
</section>
HTML
cat > "$FIXTURE/themes/indigo/lms/templates/footer.html" <<'HTML'
<footer class="fpt-lms-footer">
<style>
.fpt-lms-footer__grid{max-width:1340px;margin:0 auto;padding:40px 28px 28px;display:grid;grid-template-columns:minmax(240px,1.05fr) minmax(220px,.9fr) minmax(300px,1.25fr);gap:52px;align-items:start}
@media(max-width:760px){.fpt-lms-footer{margin-top:34px}.fpt-lms-footer__grid{grid-template-columns:1fr;gap:24px;padding:30px 22px 24px}.fpt-lms-footer__logo{width:205px}.fpt-lms-footer__copy{padding:17px 22px 22px}}
</style>
<div><strong class="fpt-lms-footer__title">Trụ sở chính</strong><p>Tòa nhà FPT Polytechnic, 13 Phan Tây Nhạc,<br/>Phường Xuân Phương, TP Hà Nội</p></div>
</footer>
HTML

python - "$TMP_DIR/polish.py" "$TMP_DIR/polish-fixture.py" "$FIXTURE" <<'PY'
from pathlib import Path
import sys

source = Path(sys.argv[1]).read_text(encoding='utf-8')
source = source.replace('/openedx/', Path(sys.argv[3]).as_posix().rstrip('/') + '/')
Path(sys.argv[2]).write_text(source, encoding='utf-8')
PY

log "Applying polish patch twice"
python "$TMP_DIR/polish-fixture.py"
python "$TMP_DIR/polish-fixture.py"

python - "$FIXTURE" "$PLUGIN" "$RUNTIME" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])
plugin = Path(sys.argv[2]).read_text(encoding='utf-8')
runtime = Path(sys.argv[3]).read_text(encoding='utf-8')
courses = (root / 'themes/indigo/lms/templates/courseware/courses.html').read_text(encoding='utf-8')
footer = (root / 'themes/indigo/lms/templates/footer.html').read_text(encoding='utf-8')

expected_courses = [
    'role="group" aria-label="Chọn slide"',
    "root.addEventListener('focusout',function(e){if(!root.contains(e.relatedTarget)){start()}});",
]
for marker in expected_courses:
    if courses.count(marker) != 1:
        raise SystemExit(f'polished Hero marker must exist exactly once: {marker}')
if 'role="tablist" aria-label="Chọn slide"' in courses:
    raise SystemExit('obsolete tablist semantics remain after polish')
if "root.addEventListener('focusout',start);" in courses:
    raise SystemExit('obsolete focusout autoplay behavior remains after polish')

contact_markers = [
    'ĐỊA CHỈ',
    'Tòa nhà FPT Polytechnic, 13 phố Phan Tây Nhạc',
    'phường Xuân Phương',
]
for marker in contact_markers:
    if marker not in footer:
        raise SystemExit(f'legacy footer contact marker missing: {marker}')
    if marker not in runtime:
        raise SystemExit(f'MFE footer contact marker missing: {marker}')

for obsolete in ('Trụ sở chính', 'Cổng Ong', 'Hà Nội'):
    if obsolete in footer or obsolete in runtime:
        raise SystemExit(f'obsolete footer wording remains rendered: {obsolete}')

legacy_layout_markers = [
    'gap:44px',
    '@media(max-width:960px)',
    'grid-template-columns:minmax(0,1fr) minmax(0,1fr)',
    '.fpt-lms-footer__grid>div:first-child{grid-column:1/-1}',
]
for marker in legacy_layout_markers:
    if marker not in footer:
        raise SystemExit(f'balanced legacy footer marker missing: {marker}')

runtime_layout_markers = [
    '@media(max-width:960px)',
    '.fpt-ui-footer__brand{grid-column:1/-1}',
    '@media(max-width:1050px)',
]
for marker in runtime_layout_markers:
    if marker not in runtime:
        raise SystemExit(f'balanced MFE responsive marker missing: {marker}')

if '_read_patch("openedx_polish.patch")' not in plugin:
    raise SystemExit('Tutor plugin is not composing openedx_polish.patch')
if plugin.find('_read_patch("openedx.patch")') > plugin.find('_read_patch("openedx_polish.patch")'):
    raise SystemExit('polish patch must be composed after the core Open edX patch')

print('[fpt-ui-polish] Accessibility/layout/contact fixture PASS')
PY

log "ALL POLISH TESTS PASS"