# FPT Presence Header Implementation Plan

**Goal:** Show a low-overhead active-user count in the Learning MFE header using backend activity only.

**Approved architecture:** Redis ZSET + fail-open Open edX middleware + aggregate count API + Learning header direct plugin. No frontend heartbeat, no MySQL/Mongo query, and no coupling to `openedx_unit_reset`.

## Contract

- Redis key: `fpt:presence:active`.
- Member: Open edX numeric user id; score: latest backend activity Unix timestamp.
- Active window: 600 seconds.
- Touch throttle: 60 seconds per user per worker.
- Count cache: 30 seconds.
- API: `GET /api/fpt-presence/v1/count` -> `{"online": <int>}`.
- The count endpoint itself is excluded from presence tracking.
- Anonymous, static, health, HEAD and OPTIONS traffic is excluded.
- LMS and Studio use the same Redis ZSET, so the same user is counted once.
- Redis failure is fail-open: LMS/CMS keep serving; count returns null and the badge hides.
- Learning MFE loads count immediately and every 60 seconds; there is no heartbeat.
- Header display: green dot + locale-formatted number + `online`, immediately before the user-menu area.

## TDD tasks

### 1. Backend presence behavior

1. Add failing tests for anonymous exclusion, authenticated tracking, count-endpoint exclusion, 60-second touch throttling, 600-second activity window, and Redis fail-open behavior.
2. Implement `PresenceService` with process-local touch throttling and Redis ZSET operations.
3. Implement `FPTPresenceMiddleware` after authentication/session middleware.
4. Re-run focused tests.

### 2. Plugin API registration

1. Add failing tests for integer count, null count on Redis failure, and GET-only behavior.
2. Register a standalone `openedx_fpt_presence` package for both `lms.djangoapp` and `cms.djangoapp`.
3. Register URL prefix `^api/fpt-presence/v1/` and `count` JSON view.
4. Re-run focused tests.

### 3. Tutor + Learning MFE integration

1. Add source regression tests for Tutor installation/configuration, middleware registration, Learning header slot, API fetch, 60-second refresh, Vietnamese number formatting, and green-dot marker.
2. Reuse the already-enabled `fpt_indigo_ui` Tutor plugin to install the backend package and configure Tutor Redis.
3. Add `presence_runtime.patch` and inject `FptPresenceBadge` through `org.openedx.frontend.layout.learning_header_actions.v1`.
4. Hide the badge on any count/API failure.
5. Verify `Intl.NumberFormat('vi-VN').format(1284)` returns `1.284`.

### 4. Verification and delivery

1. Compile all new Python files and the modified Tutor plugin.
2. Run focused backend behavior tests in the available harness and UI source tests.
3. Compare final changes against the starting `fpt-indigo-ui` commit; ensure no unit-reset/MySQL/Mongo changes.
4. Refresh branch HEAD and only fast-forward if it has not moved; never force-push.
5. Deployment requires rebuilding Open edX and MFE images and rolling LMS/CMS/MFE; no DB migration is required.
