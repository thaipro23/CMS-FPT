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
for f in "$PATCH" "$BALANCE" "$PLUGIN" "$RUNTIME"; do [ -s "$f" ] || fail "Missing $f"; done

log "Compiling legacy polish and balance Python heredocs"
python - "$PATCH" "$BALANCE" <<'PY'
from pathlib import Path
import sys
for filename, start, end in (
    (sys.argv[1], "RUN python - <<'PY3'\n", "\nPY3"),
    (sys.argv[2], "RUN python - <<'PY4'\n", "\nPY4"),
):
    text = Path(filename).read_text(encoding='utf-8')
    if text.count(start) != 1:
        raise SystemExit(f'expected exactly one Python heredoc in {filename}')
    code = text.split(start, 1)[1].rsplit(end, 1)[0]
    compile(code, filename, 'exec')
print('[fpt-ui-polish] Python patch syntax PASS')
PY

log "Checking final accessibility/layout/contrast contracts"
python - "$PATCH" "$BALANCE" "$PLUGIN" "$RUNTIME" <<'PY'
from pathlib import Path
import sys
polish = Path(sys.argv[1]).read_text(encoding='utf-8')
balance = Path(sys.argv[2]).read_text(encoding='utf-8')
plugin = Path(sys.argv[3]).read_text(encoding='utf-8')
runtime = Path(sys.argv[4]).read_text(encoding='utf-8')

for marker in (
    'role="group" aria-label="Chọn slide"',
    "root.addEventListener('focusout',function(e){if(!root.contains(e.relatedTarget)){start()}});",
    'color:#C94E08;font-size:11px',
    'background:#C65310;color:#fff!important',
    'ĐỊA CHỈ',
    'Tòa nhà FPT Polytechnic, 13 phố Phan Tây Nhạc',
    'phường Xuân Phương',
):
    if marker not in polish:
        raise SystemExit(f'legacy polish contract missing: {marker}')

for marker in (
    'FPT_LMS_FOOTER_V10_CONTRAST',
    'background:#071A33!important',
    'color:#FFFFFF!important',
    'color:#8ED0FF!important',
    'color:#E3ECF6!important',
    'color:#C7D5E5!important',
    'FPT_LIGHT_ONLY_THEME_TOGGLE_DISABLED',
    '.fpt-headline-line{display:block;white-space:nowrap',
    'FPT_DISCOVERY_V10_MOBILE_STACK',
    '@media(max-width:820px){',
    '.fpt-slide{grid-template-columns:minmax(0,1fr);gap:18px;',
    '.fpt-slide__copy{width:100%;min-width:0;max-width:680px}',
    '@media(max-width:520px){',
    '.fpt-collage{width:100%;max-width:390px;min-width:0;',
):
    if marker not in balance:
        raise SystemExit(f'final legacy balance/contrast contract missing: {marker}')

desktop_grid = balance.index(
    '.fpt-slide{grid-template-columns:minmax(0,.98fr) minmax(500px,1.02fr);'
)
tablet_media = balance.index('@media(max-width:820px){')
tablet_grid = balance.index(
    '.fpt-slide{grid-template-columns:minmax(0,1fr);gap:18px;',
    tablet_media,
)
mobile_media = balance.index('@media(max-width:520px){')
mobile_grid = balance.index(
    '.fpt-slide{grid-template-columns:minmax(0,1fr);gap:16px;',
    mobile_media,
)
compact_media = balance.index('@media(max-width:380px){')
if not desktop_grid < tablet_media < tablet_grid < mobile_media < mobile_grid < compact_media:
    raise SystemExit('final mobile one-column Hero rules do not win the CSS cascade')

for marker in (
    'ĐỊA CHỈ',
    'Tòa nhà FPT Polytechnic, 13 phố Phan Tây Nhạc',
    'phường Xuân Phương',
    '.fpt-ui-footer{border-top:1px solid #2F4765;background:#071A33',
    '.fpt-ui-footer__title{color:#FFFFFF',
    '.fpt-ui-footer a{color:#8ED0FF',
    '.fpt-ui-footer__address{color:#E3ECF6',
    '.fpt-ui-footer__copyright{border-top:1px solid #2F4765',
    '.theme-toggle-button,.light-theme-icon,.dark-theme-icon,.toggle-switch{display:none!important}',
):
    if marker not in runtime:
        raise SystemExit(f'final MFE polish contract missing: {marker}')

if '_read_patch("openedx_polish.patch")' not in plugin or '_read_patch("openedx_balance.patch")' not in plugin:
    raise SystemExit('Tutor plugin is not composing final legacy polish layers')
if plugin.find('_read_patch("openedx_polish.patch")') > plugin.find('_read_patch("openedx_balance.patch")'):
    raise SystemExit('balance patch must run after openedx_polish.patch')
if 'MFE_CONFIG["INDIGO_ENABLE_DARK_TOGGLE"] = False' not in plugin:
    raise SystemExit('MFE dark toggle remains enabled')

print('[fpt-ui-polish] Final accessibility/light-only/footer contrast contract PASS')
PY

log "ALL POLISH TESTS PASS"
