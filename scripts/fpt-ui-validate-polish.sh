#!/usr/bin/env bash
set -euo pipefail

log() { printf '[fpt-ui-polish] %s\n' "$*"; }
fail() { printf '[fpt-ui-polish] ERROR: %s\n' "$*" >&2; exit 1; }

command -v python >/dev/null 2>&1 || fail "python is required"

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
[ -n "$REPO_ROOT" ] || fail "Run inside the CMS-FPT repository"
cd "$REPO_ROOT"

PATCH="fpt_indigo_ui/patches/openedx_polish.patch"
BALANCE="fpt_indigo_ui/patches/openedx_balance.patch"
PLUGIN="tutor-plugins/fpt_indigo_ui.py"
RUNTIME="fpt_indigo_ui/patches/runtime.patch"
[ -s "$PATCH" ] || fail "Missing polish patch: $PATCH"
[ -s "$BALANCE" ] || fail "Missing balance patch: $BALANCE"

TMP_DIR="$(mktemp -d)"
cleanup() { rm -rf "$TMP_DIR"; }
trap cleanup EXIT

log "Compiling legacy polish Python heredoc"
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
<style>
.fpt-kicker{display:inline-flex;align-items:center;gap:8px;margin-bottom:14px;color:#F36F21;font-size:11px;line-height:1.2;font-weight:850;letter-spacing:.13em}
.fpt-slide__cta{display:inline-flex!important;align-items:center;gap:8px;background:#F36F21;color:#fff!important;padding:12px 19px;border-radius:9px;font-weight:800;text-decoration:none!important;box-shadow:0 9px 22px rgba(243,111,33,.22);transition:transform .18s ease,box-shadow .18s ease,background .18s ease}
.fpt-slide__cta:hover{background:#E86414;transform:translateY(-1px);box-shadow:0 12px 26px rgba(243,111,33,.28)}
</style>
<div class="fpt-hero__nav" role="tablist" aria-label="Chọn slide"></div>
<script>root.addEventListener('focusin',stop);root.addEventListener('focusout',start);</script>
</section>
HTML
cat > "$FIXTURE/themes/indigo/lms/templates/footer.html" <<'HTML'
<footer class="fpt-lms-footer"><div><strong class="fpt-lms-footer__title">Trụ sở chính</strong><p>Tòa nhà FPT Polytechnic, Hà Nội</p></div></footer>
HTML

python - "$TMP_DIR/polish.py" "$TMP_DIR/polish-fixture.py" "$FIXTURE" <<'PY'
from pathlib import Path
import sys
source = Path(sys.argv[1]).read_text(encoding='utf-8')
source = source.replace('/openedx/', Path(sys.argv[3]).as_posix().rstrip('/') + '/')
Path(sys.argv[2]).write_text(source, encoding='utf-8')
PY
python "$TMP_DIR/polish-fixture.py"
python "$TMP_DIR/polish-fixture.py"

python - "$FIXTURE" "$BALANCE" "$PLUGIN" "$RUNTIME" <<'PY'
from pathlib import Path
import sys
root = Path(sys.argv[1])
balance = Path(sys.argv[2]).read_text(encoding='utf-8')
plugin = Path(sys.argv[3]).read_text(encoding='utf-8')
runtime = Path(sys.argv[4]).read_text(encoding='utf-8')
courses = (root / 'themes/indigo/lms/templates/courseware/courses.html').read_text(encoding='utf-8')
footer = (root / 'themes/indigo/lms/templates/footer.html').read_text(encoding='utf-8')

for marker in (
    'role="group" aria-label="Chọn slide"',
    "root.addEventListener('focusout',function(e){if(!root.contains(e.relatedTarget)){start()}});",
    'color:#C94E08;font-size:11px',
    'background:#C65310;color:#fff!important',
):
    if courses.count(marker) != 1:
        raise SystemExit(f'polished Hero marker missing/idempotence failure: {marker}')
if 'role="tablist" aria-label="Chọn slide"' in courses:
    raise SystemExit('obsolete tablist semantics remain')
if "root.addEventListener('focusout',start);" in courses:
    raise SystemExit('obsolete focusout behavior remains')

for marker in (
    'ĐỊA CHỈ',
    'Tòa nhà FPT Polytechnic, 13 phố Phan Tây Nhạc',
    'phường Xuân Phương',
):
    if marker not in footer:
        raise SystemExit(f'final legacy footer wording missing: {marker}')
    if marker not in runtime:
        raise SystemExit(f'final MFE footer wording missing: {marker}')

for obsolete in ('Trụ sở chính', 'Cổng Ong'):
    if obsolete in footer or obsolete in runtime:
        raise SystemExit(f'obsolete footer wording remains: {obsolete}')

for marker in (
    'FPT_LMS_FOOTER_V10_CONTRAST',
    'background:#071A33!important',
    'color:#FFFFFF!important',
    'color:#8ED0FF!important',
    'color:#E3ECF6!important',
    'color:#C7D5E5!important',
    'FPT_LIGHT_ONLY_THEME_TOGGLE_DISABLED',
):
    if marker not in balance:
        raise SystemExit(f'final legacy contrast/light marker missing: {marker}')

for marker in (
    '.fpt-ui-footer{border-top:1px solid #2F4765;background:#071A33',
    '.fpt-ui-footer__title{color:#FFFFFF',
    '.fpt-ui-footer a{color:#8ED0FF',
    '.fpt-ui-footer__address{color:#E3ECF6',
    '.fpt-ui-footer__copyright{border-top:1px solid #2F4765',
    '.theme-toggle-button,.light-theme-icon,.dark-theme-icon,.toggle-switch{display:none!important}',
):
    if marker not in runtime:
        raise SystemExit(f'final MFE contrast/light marker missing: {marker}')

if '_read_patch("openedx_polish.patch")' not in plugin or '_read_patch("openedx_balance.patch")' not in plugin:
    raise SystemExit('Tutor plugin is not composing final legacy polish layers')
if plugin.find('_read_patch("openedx_polish.patch")') > plugin.find('_read_patch("openedx_balance.patch")'):
    raise SystemExit('balance patch must run after openedx_polish.patch')

print('[fpt-ui-polish] Final accessibility/light-only/footer contrast contract PASS')
PY

log "ALL POLISH TESTS PASS"