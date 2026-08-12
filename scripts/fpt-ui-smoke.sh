#!/usr/bin/env bash
set -euo pipefail

log() { printf '[fpt-ui-smoke] %s\n' "$*"; }
fail() { printf '[fpt-ui-smoke] ERROR: %s\n' "$*" >&2; exit 1; }
warn() { printf '[fpt-ui-smoke] WARN: %s\n' "$*" >&2; }

command -v curl >/dev/null 2>&1 || fail "curl is required"

LMS_URL="${1:-${LMS_URL:-}}"
[ -n "$LMS_URL" ] || fail "Usage: $0 https://cms-test.poly.edu.vn"
LMS_URL="${LMS_URL%/}"

CURL_COMMON=(--silent --show-error --location --max-time 30 --retry 2 --retry-delay 1)
ASSETS=(
  fpt-polytechnic-logo.png
  fpt-students.png
  fpt-campus-primary.jpg
  fpt-campus-secondary.jpg
)

log "Target: $LMS_URL"

for name in "${ASSETS[@]}"; do
  url="$LMS_URL/static/indigo/images/fpt/$name"
  headers="$(mktemp)"
  body="$(mktemp)"
  trap 'rm -f "$headers" "$body"' RETURN
  status="$(curl "${CURL_COMMON[@]}" --output "$body" --dump-header "$headers" --write-out '%{http_code}' "$url")"
  [ "$status" = "200" ] || fail "$name returned HTTP $status"
  bytes="$(wc -c < "$body" | tr -d ' ')"
  [ "$bytes" -gt 1000 ] || fail "$name is unexpectedly small: ${bytes} bytes"
  content_type="$(awk 'BEGIN{IGNORECASE=1}/^content-type:/{gsub("\r","");print $2;exit}' "$headers")"
  case "$content_type" in
    image/*) ;;
    *) warn "$name content-type is '${content_type:-unknown}'" ;;
  esac
  rm -f "$headers" "$body"
  trap - RETURN
  log "PASS asset $name (${bytes} bytes)"
done

courses_tmp="$(mktemp)"
trap 'rm -f "$courses_tmp"' EXIT
courses_status="$(curl "${CURL_COMMON[@]}" --output "$courses_tmp" --write-out '%{http_code}' "$LMS_URL/courses")"
case "$courses_status" in
  200)
    if grep -Fq 'fpt-hero-slider' "$courses_tmp"; then
      log "PASS legacy /courses FPT hero marker"
    else
      warn "/courses is 200 but FPT hero marker is not present. Check whether another discovery route/template is active."
    fi
    ;;
  30[12378]) warn "/courses redirected (HTTP $courses_status); validate in authenticated browser" ;;
  *) warn "/courses returned HTTP $courses_status; validate routing after restart" ;;
esac

login_status="$(curl "${CURL_COMMON[@]}" --output /dev/null --write-out '%{http_code}' "$LMS_URL/login")"
case "$login_status" in
  200|30[12378]) log "PASS login route reachable (HTTP $login_status)" ;;
  *) fail "login route returned HTTP $login_status" ;;
esac

cat <<'CHECKLIST'

Manual authenticated UAT checks still required:
  1. Login desktop >=1440: one orange wedge only; FEID + forgot password work; no Register.
  2. Login tablet 820 and mobile 390: no horizontal overflow and no form/wedge collision.
  3. Learner Home: existing course list/enrollment behavior unchanged; FPT header/footer/banner visible.
  4. Discovery: Search + Filters + Course Grid still work; slider uses three separate image cards.
  5. Learning: Course / Progress / Instructor render; Unit / Quiz / Unit Reset behavior unchanged.
  6. Legacy /courses: FPT logo/hero/footer render and no old edX diamond remains.
  7. No dark-mode toggle on branded routes.
CHECKLIST

log "HTTP/static smoke checks PASS"
