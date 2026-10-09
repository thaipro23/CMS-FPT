# Unit-reset analytics telemetry — 0.4.17

The plugin adds server-owned metadata to problem_check and emits successful quiz-session starts/resets using the persisted session scope and start time. Refreshes emit the same session nonce. Ordinary API request logs do not establish success. Definition and response hashes normalize generated CAPA IDs/references so equivalent clones can be compared while changed definitions and variants remain separate.

The hook targets `xmodule.capa_block.ProblemBlock.publish_unmasked`. Enrichment changes event metadata only; native grading, grade-isolated reset targeting, duration editing, timer expiry and timeout auto-submit retain their current behavior. Telemetry failures are logged and do not reject a learner submission. `UNIT_RESET_QUIZ_ANALYTICS_ENABLED` defaults to true; set false to disable the hook and additional session emissions.

Standalone verification:

```sh
PYTHONPATH=openedx_unit_reset pytest -q -c /dev/null --confcutdir=openedx_unit_reset/tests openedx_unit_reset/tests
```

All 15 tests pass, including real Django timer persistence, duration edits, reset boundary checks and CAPA clone metadata. LMS enrollment authorization and native grade resets require a full deployed LMS integration check; their boundaries are stubbed in the standalone tests.

After deployment, verify `ProblemBlock.publish_unmasked._acms_quiz_analytics`, then start, refresh, submit and reset a practice quiz. Confirm scoped nonce/started_at, content_version/question_hash on server problem_check, unchanged timer expiry and official grades. The accompanying CMS-AI fix uses this metadata; historical missing versions or expired raw logs are not recoverable.
