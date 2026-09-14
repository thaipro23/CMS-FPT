# Online Presence Header Implementation Plan

**Goal:** Show `🟢 1.284 online` in the Learning MFE header using a low-cost Redis-backed 10-minute activity window.

**Architecture:** LMS and Studio middleware record authenticated backend activity in one Redis sorted set. Each user is touched at most once per 60 seconds. The count endpoint caches its result for 30 seconds. The Learning MFE reads that count on load and every 60 seconds; no heartbeat is sent.

**Constraints:** No MySQL/Mongo/MinIO writes; count polling must not keep users online; Redis/backend failures must never break normal Open edX requests; indicator hides on API failure.

## Backend — CMS-FPT / `fpt-indigo-ui`

1. Add tests for presence core behavior: 60-second touch throttle, 10-minute ZCOUNT window, count cache, excluded paths, fail-open behavior, and URL/settings wiring.
2. Add `openedx_ai_connector/presence.py` with middleware + count endpoint using the default django-redis connection.
3. Mount `/api/presence/v1/count` and install middleware immediately after Django authentication middleware in both LMS and CMS through plugin settings.
4. Keep count endpoint and mechanical/static paths excluded from activity tracking.

## Frontend — frontend-app-learning / `mfe-unit-reset`

1. Add tests for `OnlinePresence`: renders `🟢 1.284 online`, refreshes every 60 seconds, and hides on API failure.
2. Add `OnlinePresence` component using authenticated/session-capable fetch to `${LMS_BASE_URL}/api/presence/v1/count`.
3. Integrate the indicator immediately before `.user-dropdown` in the Learning Header via a small React portal mount, without forking `frontend-component-header`.
4. Keep mobile behavior compact and non-blocking.

## Verification

- Backend unit tests for new presence behavior.
- Frontend focused Jest tests + lint/build if CI permits.
- Verify branch CI before merging to production branches.
- After build/deploy, verify endpoint response and header rendering in production.