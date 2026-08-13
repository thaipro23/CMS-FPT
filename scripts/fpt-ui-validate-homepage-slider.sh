#!/usr/bin/env bash
set -euo pipefail

log() { printf '[fpt-ui-homepage] %s\n' "$*"; }
fail() { printf '[fpt-ui-homepage] ERROR: %s\n' "$*" >&2; exit 1; }

command -v python >/dev/null 2>&1 || fail "python is required"

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
[ -n "$REPO_ROOT" ] || fail "Run inside the CMS-FPT repository"
cd "$REPO_ROOT"

PATCH="fpt_indigo_ui/patches/homepage_slider.patch"
PLUGIN="tutor-plugins/fpt_indigo_ui.py"
[ -s "$PATCH" ] || fail "Missing homepage slider patch: $PATCH"

TMP_DIR="$(mktemp -d)"
cleanup() { rm -rf "$TMP_DIR"; }
trap cleanup EXIT

log "Extracting and compiling homepage slider patch"
python - "$PATCH" "$TMP_DIR/homepage.py" <<'PY'
from pathlib import Path
import sys

text = Path(sys.argv[1]).read_text(encoding='utf-8')
start = "RUN python - <<'PY5'\n"
end = "\nPY5"
if text.count(start) != 1:
    raise SystemExit('expected exactly one homepage slider Python heredoc')
code = text.split(start, 1)[1].rsplit(end, 1)[0]
compile(code, '<homepage_slider.patch>', 'exec')
Path(sys.argv[2]).write_text(code, encoding='utf-8')
PY

FIXTURE="$TMP_DIR/openedx"
mkdir -p "$FIXTURE/themes/indigo/lms/templates/courseware"

cat > "$FIXTURE/themes/indigo/lms/templates/courseware/courses.html" <<'HTML'
<html><body>
<!-- FPT_DISCOVERY_V8_START -->
<section id="fpt-hero-slider" class="fpt-hero">
<style id="fpt-discovery-v9-balance">/* FPT_DISCOVERY_V9_BALANCE */</style>
<div class="fpt-slide is-active">shared hero</div>
<script>window.FPT_SHARED_HERO=true;</script>
</section>
<!-- FPT_DISCOVERY_V8_END -->
<section class="courses-container">COURSES</section>
</body></html>
HTML

cat > "$FIXTURE/themes/indigo/lms/templates/index.html" <<'HTML'
<main id="main" aria-label="Content" tabindex="-1">
  <section class="home style-logout">
    <header>
      <div class="course-search">
        <form method="get" action="/courses"></form>
      </div>
    </header>
  </section>
</main>
HTML

python - "$TMP_DIR/homepage.py" "$TMP_DIR/homepage-fixture.py" "$FIXTURE" <<'PY'
from pathlib import Path
import sys

source = Path(sys.argv[1]).read_text(encoding='utf-8')
source = source.replace('/openedx/', Path(sys.argv[3]).as_posix().rstrip('/') + '/')
Path(sys.argv[2]).write_text(source, encoding='utf-8')
PY

log "Applying homepage slider patch twice"
python "$TMP_DIR/homepage-fixture.py"
python "$TMP_DIR/homepage-fixture.py"

python - "$FIXTURE" "$PLUGIN" <<'PY'
from pathlib import Path
import sys

root = Path(sys.argv[1])
plugin = Path(sys.argv[2]).read_text(encoding='utf-8')
courses = (root / 'themes/indigo/lms/templates/courseware/courses.html').read_text(encoding='utf-8')
home = (root / 'themes/indigo/lms/templates/index.html').read_text(encoding='utf-8')
start = '<!-- FPT_DISCOVERY_V8_START -->'
end = '<!-- FPT_DISCOVERY_V8_END -->'

def hero(text: str) -> str:
    return start + text.split(start, 1)[1].split(end, 1)[0] + end

if home.count(start) != 1 or home.count(end) != 1:
    raise SystemExit('homepage Hero markers must exist exactly once')
if home.count('id="fpt-hero-slider"') != 1:
    raise SystemExit('homepage must contain exactly one slider id')
if home.count('id="discovery-form"') != 1:
    raise SystemExit('homepage must contain exactly one discovery-form anchor')
if hero(home) != hero(courses):
    raise SystemExit('homepage Hero must be byte-identical to final /courses Hero')
if home.find(start) > home.find('<section class="home style-logout">'):
    raise SystemExit('homepage Hero must render before the default homepage content')
if 'FPT_DISCOVERY_V9_BALANCE' not in home:
    raise SystemExit('homepage did not inherit final V9 Hero balance/theme CSS')

required_order = [
    '_read_patch("openedx_balance.patch")',
    '_read_patch("homepage_slider.patch")',
    '_read_patch("native_logo.patch")',
]
positions = [plugin.find(item) for item in required_order]
if any(pos < 0 for pos in positions):
    raise SystemExit('homepage/native logo patch composition is incomplete')
if positions != sorted(positions):
    raise SystemExit('homepage slider patch must run after balance and before native logo replacement')

print('[fpt-ui-homepage] Shared / and /courses Hero contract PASS')
PY

log "ALL HOMEPAGE SLIDER TESTS PASS"
