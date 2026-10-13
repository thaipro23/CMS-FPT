# Open edX Quiz submit guard hot-path optimization — 0.4.19

## Production evidence (2026-10-13)

LMS has 9 uWSGI pods with 2 workers each. A 10-minute access-log sample
measured 554 `problem_check` POSTs: mean 9.96s, p95 39.65s,
max 48.65s. A read-only guard cProfile using a single representative learner
timed out at 10s (9.72 CPU seconds) after 494 modulestore `get_item`
calls. Mongo split-course structure deserialization dominated CPU: 988
`_pickle.loads` calls. The same probe recorded 17 SQL queries totaling
33ms. This is a measured problem in the submit guard; other work inside
`problem_check` was not independently profiled.

## Fix

- Reuse **successful** XBlock lookups only within one guard invocation.
  Do not cache errors or state across requests.
- Avoid the redundant full structural traversal before
  `collect_active_unit_usage_keys()`; the latter already performs it.
- Memoize selected active Unit keys for historical sessions of the **same
  learner and Unit**, within the guard call only.
- Reuse the latest-attempt lookup per timer-config ID, without joining timer
  config data that is not read.
- Within the active Unit traversal, use the native Open edX SplitMongo
  `bulk_operations(course_key, emit_signals=False)` read scope to reuse the
  same deserialized course structure across nested `get_item` calls.
  No source data is mutated by the guard.
- Optional emergency rollback for this scoped-read optimization:
  `UNIT_RESET_QUIZ_GUARD_BULK_READ_ENABLED = False` in LMS Django settings.
  The existing `UNIT_RESET_QUIZ_TIMER_SERVER_GUARD_ENABLED` must **remain
  enabled**; disabling it can admit late quiz answers.

## Invariants

- Do not change Open edX scoring, completion, grade reconciliation or reset.
- Do not widen ItemBank-selected children to include unselected problems.
- Historical RESET_READY attempts must not block a newer ACTIVE attempt.
- Expired/locked attempts still block selected problems unless the existing
  short-lived auto-submit grace rule allows them.
- Failed Mongo reads must not be cached or accepted as proof of scope.
- No schema migrations, Redis flush, session deletion or global cache.

## Validation

CI: `FPT UI Static Validation` runs the isolated Unit Reset tests after
installing Django, Celery and edx-opaque-keys. Test cases cover timer
expiration, auto-submit grace, retakes, ItemBank selection, deduplicated
lookups and single read-only SplitMongo traversal.

Before production rollout, compare a UAT execution of the same
read-only cProfile against the 2026-10-13 baseline. Then build a new
Open edX image and confirm the installed `openedx-unit-reset` distribution
reports version 0.4.19. Roll out gradually, observe `problem_check`
p50/p95/p99 latency, LMS CPU and 502/504, and confirm correct scoring on a
test student. An improvement in guard time does not automatically prove that
all Quiz navigation latency is resolved.
