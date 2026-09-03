#!/usr/bin/env bash
set -euo pipefail

log() { printf '[fpt-ui-homepage] %s\n' "$*"; }
fail() { printf '[fpt-ui-homepage] ERROR: %s\n' "$*" >&2; exit 1; }

command -v python >/dev/null 2>&1 || fail "python is required"

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
[ -n "$REPO_ROOT" ] || fail "Run inside the CMS-FPT repository"
cd "$REPO_ROOT"

PATCH="fpt_indigo_ui/patches/slider_images.patch"
PLUGIN="tutor-plugins/fpt_indigo_ui.py"
[ -s "$PATCH" ] || fail "Missing responsive slider patch: $PATCH"

TMP_DIR="$(mktemp -d)"
cleanup() { rm -rf "$TMP_DIR"; }
trap cleanup EXIT

log "Extracting and compiling responsive slider patch"
python - "$PATCH" "$TMP_DIR/slider.py" <<'PY'
from pathlib import Path
import sys

text = Path(sys.argv[1]).read_text(encoding='utf-8')
start = "RUN python - <<'PY_SLIDER_V11'\n"
end = "\nPY_SLIDER_V11"
if text.count(start) != 1:
    raise SystemExit('expected exactly one responsive slider Python heredoc')
code = text.split(start, 1)[1].rsplit(end, 1)[0]
compile(code, '<slider_images.patch>', 'exec')
Path(sys.argv[2]).write_text(code, encoding='utf-8')
PY

FIXTURE="$TMP_DIR/openedx"
mkdir -p "$FIXTURE/themes/indigo/lms/templates/courseware"

cat > "$FIXTURE/themes/indigo/lms/templates/courseware/courses.html" <<'HTML'
<html><body>
<!-- FPT_DISCOVERY_V8_START -->
<section id="fpt-hero-slider" class="fpt-hero">
  <div class="fpt-slide is-active">legacy courses hero</div>
</section>
<!-- FPT_DISCOVERY_V8_END -->
<section class="courses-container">COURSES</section>
</body></html>
HTML

cat > "$FIXTURE/themes/indigo/lms/templates/index.html" <<'HTML'
<html><body>
<main id="main" aria-label="Content" tabindex="-1">
<!-- FPT_DISCOVERY_V8_START -->
<section id="fpt-hero-slider" class="fpt-hero">
  <div class="fpt-slide is-active">legacy homepage hero</div>
</section>
<!-- FPT_DISCOVERY_V8_END -->
<section class="home style-logout">HOME</section>
</main>
</body></html>
HTML

python - "$TMP_DIR/slider.py" "$TMP_DIR/slider-fixture.py" "$FIXTURE" <<'PY'
from pathlib import Path
import sys

source = Path(sys.argv[1]).read_text(encoding='utf-8')
source = source.replace('/openedx/', Path(sys.argv[3]).as_posix().rstrip('/') + '/')
Path(sys.argv[2]).write_text(source, encoding='utf-8')
PY

log "Applying responsive slider patch twice"
python "$TMP_DIR/slider-fixture.py"
python "$TMP_DIR/slider-fixture.py"

python - "$FIXTURE" "$PLUGIN" <<'PY'
from pathlib import Path
import re
import sys

root = Path(sys.argv[1])
plugin = Path(sys.argv[2]).read_text(encoding='utf-8')
courses = (root / 'themes/indigo/lms/templates/courseware/courses.html').read_text(encoding='utf-8')
home = (root / 'themes/indigo/lms/templates/index.html').read_text(encoding='utf-8')
start = '<!-- FPT_DISCOVERY_V8_START -->'
end = '<!-- FPT_DISCOVERY_V8_END -->'
assets = (
    'fpt-slider-01-male-desktop.webp',
    'fpt-slider-01-male-mobile.webp',
    'fpt-slider-02-female-desktop.webp',
    'fpt-slider-02-female-mobile.webp',
    'fpt-slider-03-group-desktop.webp',
    'fpt-slider-03-group-mobile.webp',
)

def hero(text: str) -> str:
    if text.count(start) != 1 or text.count(end) != 1:
        raise SystemExit('Hero markers must exist exactly once')
    return start + text.split(start, 1)[1].split(end, 1)[0] + end

for label, text in (('homepage', home), ('courses', courses)):
    block = hero(text)
    if block.count('id="fpt-hero-slider"') != 1:
        raise SystemExit(f'{label} must contain exactly one slider id')
    if 'FPT_DISCOVERY_V11_IMAGE_ONLY' not in block:
        raise SystemExit(f'{label} is missing the V11 image-only marker')
    if len(re.findall(r'class="fpt-slide(?: is-active)?"', block)) != 3:
        raise SystemExit(f'{label} must contain exactly three slides')
    if block.count('<picture class="fpt-slide__picture">') != 3:
        raise SystemExit(f'{label} must contain exactly three picture elements')
    if 'fpt-slide__copy' in block or 'fpt-collage' in block:
        raise SystemExit(f'{label} still contains legacy copy/collage DOM')
    if block.count('media="(max-width: 820px)"') != 3:
        raise SystemExit(f'{label} must define three mobile image sources')
    for name in assets:
        static_ref = f'/static/indigo/images/fpt/{name}'
        if block.count(static_ref) != 1:
            raise SystemExit(f'{label} must reference {name} exactly once')

if hero(home) != hero(courses):
    raise SystemExit('homepage and /courses Hero must be byte-identical')

required_order = [
    '_read_patch("openedx.patch")',
    '_read_patch("slider_images.patch")',
    '_read_patch("native_logo.patch")',
]
positions = [plugin.find(item) for item in required_order]
if any(pos < 0 for pos in positions):
    raise SystemExit('Open edX slider patch composition is incomplete')
if positions != sorted(positions):
    raise SystemExit('slider_images.patch must run after openedx.patch and before native_logo.patch')

print('[fpt-ui-homepage] Shared / and /courses responsive image-only Hero PASS')
PY

log "ALL HOMEPAGE SLIDER TESTS PASS"
