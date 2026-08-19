#!/usr/bin/env bash
set -euo pipefail

log() { printf '[fpt-ui-smoke] %s\n' "$*"; }
fail() { printf '[fpt-ui-smoke] ERROR: %s\n' "$*" >&2; exit 1; }
warn() { printf '[fpt-ui-smoke] WARN: %s\n' "$*" >&2; }

command -v curl >/dev/null 2>&1 || fail "curl is required"

LMS_URL="${1:-${LMS_URL:-}}"
MFE_URL="${2:-${MFE_URL:-}}"
[ -n "$LMS_URL" ] || fail "Usage: $0 https://cms-test.poly.edu.vn [https://app.cms-test.poly.edu.vn]"
LMS_URL="${LMS_URL%/}"
MFE_URL="${MFE_URL%/}"

CURL_COMMON=(--silent --show-error --location --connect-timeout 5 --max-time 20)
ASSETS=(
  fpt-polytechnic-logo.png
  fpt-polytechnic-logo-white.png
  fpt-students.png
  fpt-campus-primary.jpg
  fpt-campus-secondary.jpg
)
TMP_FILES=()
COLOUR_LOGO_BODY=""
WHITE_LOGO_BODY=""
cleanup() {
  if [ "${#TMP_FILES[@]}" -gt 0 ]; then
    rm -f "${TMP_FILES[@]}" 2>/dev/null || true
  fi
}
trap cleanup EXIT

new_tmp() {
  local f
  f="$(mktemp)"
  TMP_FILES+=("$f")
  printf '%s' "$f"
}

wait_http_ready() {
  local label="$1"
  local url="$2"
  local status="000"
  log "Waiting for $label: $url"
  for attempt in $(seq 1 30); do
    status="$(curl "${CURL_COMMON[@]}" --output /dev/null --write-out '%{http_code}' "$url" 2>/dev/null || printf '000')"
    case "$status" in
      200|30[12378])
        log "$label ready on attempt $attempt (HTTP $status)"
        return 0
        ;;
    esac
    if [ "$attempt" -lt 30 ]; then
      sleep 2
    fi
  done
  fail "$label did not become ready after 30 attempts (last HTTP $status)"
}

check_html_route() {
  local label="$1"
  local url="$2"
  local body headers status bytes content_type
  body="$(new_tmp)"
  headers="$(new_tmp)"
  status="$(curl "${CURL_COMMON[@]}" --output "$body" --dump-header "$headers" --write-out '%{http_code}' "$url" 2>/dev/null || printf '000')"
  [ "$status" = "200" ] || fail "$label returned HTTP $status: $url"
  bytes="$(wc -c < "$body" | tr -d ' ')"
  [ "$bytes" -gt 200 ] || fail "$label returned an unexpectedly small body: ${bytes} bytes"
  content_type="$(awk 'BEGIN{IGNORECASE=1}/^content-type:/{gsub("\r","");print $2;exit}' "$headers")"
  case "$content_type" in
    text/html*) ;;
    *) fail "$label content-type is '${content_type:-unknown}', expected text/html" ;;
  esac
  log "PASS $label (${bytes} bytes, $content_type)"
}

log "LMS target: $LMS_URL"
wait_http_ready "LMS login route" "$LMS_URL/login"

for name in "${ASSETS[@]}"; do
  url="$LMS_URL/static/indigo/images/fpt/$name"
  headers="$(new_tmp)"
  body="$(new_tmp)"
  status="$(curl "${CURL_COMMON[@]}" --output "$body" --dump-header "$headers" --write-out '%{http_code}' "$url" 2>/dev/null || printf '000')"
  [ "$status" = "200" ] || fail "$name returned HTTP $status"
  bytes="$(wc -c < "$body" | tr -d ' ')"
  [ "$bytes" -gt 1000 ] || fail "$name is unexpectedly small: ${bytes} bytes"
  content_type="$(awk 'BEGIN{IGNORECASE=1}/^content-type:/{gsub("\r","");print $2;exit}' "$headers")"
  case "$content_type" in
    image/*) ;;
    *) fail "$name content-type is '${content_type:-unknown}', expected image/*" ;;
  esac
  case "$name" in
    fpt-polytechnic-logo.png) COLOUR_LOGO_BODY="$body" ;;
    fpt-polytechnic-logo-white.png) WHITE_LOGO_BODY="$body" ;;
  esac
  log "PASS asset $name (${bytes} bytes, $content_type)"
done

[ -n "$COLOUR_LOGO_BODY" ] && [ -n "$WHITE_LOGO_BODY" ] || fail "Logo smoke files were not captured"
if cmp -s "$COLOUR_LOGO_BODY" "$WHITE_LOGO_BODY"; then
  fail "Colour and white logo endpoints returned identical artwork"
fi
log "PASS distinct colour/white logo artwork"

courses_tmp="$(new_tmp)"
courses_status="$(curl "${CURL_COMMON[@]}" --output "$courses_tmp" --write-out '%{http_code}' "$LMS_URL/courses" 2>/dev/null || printf '000')"
case "$courses_status" in
  200)
    if grep -Fq 'fpt-hero-slider' "$courses_tmp"; then
      log "PASS legacy /courses FPT hero marker"
    else
      warn "/courses is 200 but FPT hero marker is not present. Another discovery route/template may be active."
    fi
    ;;
  30[12378]) warn "/courses redirected (HTTP $courses_status); validate in authenticated browser" ;;
  *) warn "/courses returned HTTP $courses_status; validate routing after restart" ;;
esac

login_status="$(curl "${CURL_COMMON[@]}" --output /dev/null --write-out '%{http_code}' "$LMS_URL/login" 2>/dev/null || printf '000')"
case "$login_status" in
  200|30[12378]) log "PASS login route reachable (HTTP $login_status)" ;;
  *) fail "login route returned HTTP $login_status after readiness passed" ;;
esac

if [ -n "$MFE_URL" ]; then
  log "MFE target: $MFE_URL"
  wait_http_ready "Authn MFE" "$MFE_URL/authn/"
  check_html_route "Authn MFE shell" "$MFE_URL/authn/"
  check_html_route "Learner Dashboard MFE shell" "$MFE_URL/learner-dashboard/"
else
  warn "MFE_URL not provided; direct Authn/Learner Dashboard route checks skipped"
fi

cat <<'CHECKLIST'

Manual authenticated UAT checks still required for business behavior:
  1. FEID + forgot password work; no Register.
  2. Learner Home course list/enrollment behavior is unchanged.
  3. Discovery Search + Filters + Course Grid still work.
  4. Learning Course / Progress / Instructor render; Unit / Quiz / Unit Reset behavior is unchanged.
CHECKLIST

log "HTTP/static smoke checks PASS"
