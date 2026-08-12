#!/usr/bin/env bash
set -euo pipefail

log() { printf '[fpt-ui-smoke] %s\n' "$*"; }
fail() { printf '[fpt-ui-smoke] ERROR: %s\n' "$*" >&2; exit 1; }
warn() { printf '[fpt-ui-smoke] WARN: %s\n' "$*" >&2; }

command -v curl >/dev/null 2>&1 || fail "curl is required"

LMS_URL="${1:-${LMS_URL:-}}"
[ -n "$LMS_URL" ] || fail "Usage: $0 https://cms-test.poly.edu.vn"
LMS_URL="${LMS_URL%/}"

CURL_COMMON=(--silent --show-error --location --connect-timeout 5 --max-time 20)
ASSETS=(
  fpt-polytechnic-logo.png
  fpt-students.png
  fpt-campus-primary.jpg
  fpt-campus-secondary.jpg
)
TMP_FILES=()
cleanup() {
  if [ "${#TMP_FILES[@]}" -gt 0 ]; then
    rm -f "${TMP_FILES[@]}" 2>/dev/null || true
  fi
}
trap cleanup EXIT

log "Target: $LMS_URL"
log "Waiting for LMS login route to become ready"
READY=0
LAST_STATUS="000"
for attempt in $(seq 1 30); do
  LAST_STATUS="$(curl "${CURL_COMMON[@]}" --output /dev/null --write-out '%{http_code}' "$LMS_URL/login" 2>/dev/null || printf '000')"
  case "$LAST_STATUS" in
    200|30[12378])
      READY=1
      log "LMS ready on attempt $attempt (HTTP $LAST_STATUS)"
      break
      ;;
  esac
  if [ "$attempt" -lt 30 ]; then
    sleep 2
  fi
done
[ "$READY" -eq 1 ] || fail "LMS did not become ready after 30 attempts (last HTTP $LAST_STATUS)"

for name in "${ASSETS[@]}"; do
  url="$LMS_URL/static/indigo/images/fpt/$name"
  headers="$(mktemp)"
  body="$(mktemp)"
  TMP_FILES+=("$headers" "$body")
  status="$(curl "${CURL_COMMON[@]}" --output "$body" --dump-header "$headers" --write-out '%{http_code}' "$url" 2>/dev/null || printf '000')"
  [ "$status" = "200" ] || fail "$name returned HTTP $status"
  bytes="$(wc -c < "$body" | tr -d ' ')"
  [ "$bytes" -gt 1000 ] || fail "$name is unexpectedly small: ${bytes} bytes"
  content_type="$(awk 'BEGIN{IGNORECASE=1}/^content-type:/{gsub("\r","");print $2;exit}' "$headers")"
  case "$content_type" in
    image/*) ;;
    *) fail "$name content-type is '${content_type:-unknown}', expected image/*" ;;
  esac
  log "PASS asset $name (${bytes} bytes, $content_type)"
done

courses_tmp="$(mktemp)"
TMP_FILES+=("$courses_tmp")
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

cat <<'CHECKLIST'

Manual authenticated UAT checks still required for business behavior:
  1. FEID + forgot password work; no Register.
  2. Learner Home course list/enrollment behavior is unchanged.
  3. Discovery Search + Filters + Course Grid still work.
  4. Learning Course / Progress / Instructor render; Unit / Quiz / Unit Reset behavior is unchanged.
CHECKLIST

log "HTTP/static smoke checks PASS"
