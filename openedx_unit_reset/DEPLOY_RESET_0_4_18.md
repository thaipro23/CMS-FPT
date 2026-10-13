# Combined reset and score-report fix

This change extends the existing `fpt-indigo-ui` score-report fix (`c85b701`),
without replacing Connector `25.9.16.5.108` or its read-only ItemBank policy.
Dash CMS-AI score refresh remains at `9a04bd2`; no extra Dash change is required
for the Unit Reset API additions.

## Unit Reset 0.4.18

- Reset uses the learner's persisted ItemBank selection, not every bank question.
- Existing selected blocks outside the requested Unit are rejected. Unexpected
  lookup errors abort before any deletion; only verified not-found Problems may
  be handled as retired selected leaves.
- Scored leaves go through the official reset API. Direct container cleanup is
  refused if a container unexpectedly owns a positive-denominator grade.
- The official completion handler resets selected leaf completion separately
  from grade signals; Quiz 1, Final and unselected questions are preserved.
- Reset and creation of the next timed attempt share one transaction. Failure
  or rollback publishes no reconciliation task.
- Session start/reset share the per-learner/Unit lock. Reset mutations and every
  reconciliation also share a stable per-learner/course lock, preventing an older
  plugin rebuild from overwriting a newer plugin reset.
- One durable grade reconciliation is published after commit. Retry/final
  failure, retry-publication failure and a missing rebuilt grade cannot be
  reported as `COMPLETED`. Duplicate completed messages do no extra rebuild.

## Migration and rollout

The new image MUST run migration `openedx_unit_reset.0003_unitresetgradereconciliation`
before it serves requests or consumes tasks requiring the new models.
Use the new image in the migration/init Job, then roll LMS and LMS-worker to the
same image. The migration adds two tables only: reconciliation and course lock.
It does not modify existing learner points or restore missing scores.

The underlying management command inside the new image is:

```sh
./manage.py lms migrate openedx_unit_reset 0003_unitresetgradereconciliation --settings=tutor.production
```

Verify Unit Reset version `0.4.18`, Connector runtime `25.9.16.5.108`, migration
applied, and task registration `openedx_unit_reset.tasks.reconcile_unit_reset_grade`.
No MFE rebuild is required for this server/plugin patch. The previously pushed
Dash score button fix still requires deploying the corresponding Dash image.

## Validation and limits

- Standalone Unit Reset suite: 30 passed. Django migrations/schema checked on a
  fresh in-memory database; no model/migration drift detected.
- Connector suite: 39 passed; read-only selection policy: 5 passed.
- Reset tests execute production plugin services/tasks and real Django storage;
  Open edX reset, completion and factory boundaries are substituted.
- `tests/test_reset_row_locks.py` contains three concurrency tests for a disposable
  PostgreSQL/MySQL database whose name begins `reset_test_`. The class is skipped
  on SQLite because SQLite cannot verify `SELECT FOR UPDATE` behavior.
- Full Open edX image integration and real database concurrency have not run in
  this workspace. Normal learner submissions do not acquire the plugin lock;
  the lock serializes plugin reset/reconciliation flows, not all LMS writers.

In staging, record Quiz 1/Quiz 2/Final scores and completions, reset Quiz 2,
verify only its selected attempt changed, wait for reconciliation `COMPLETED`,
then refresh Dash and compare the source payload with the saved snapshot/UI.
Repeat with failed publication, failed rebuild and concurrent reset requests.
Do not reset production student attempts merely to validate deployment.
