# Login labels + presence runtime fix

## Scope

- Preserve the currently approved Authn layout and styling.
- Rename FEID button text to `Student Login`.
- Rename Google button text to `Staff Login`.
- Keep FEID/Google provider routing unchanged.
- Fix FPT presence so the installed Django plugin configures its own middleware and reuses Open edX's existing Redis URL when Tutor runtime patches are stale/missing.
- Keep presence fail-open and keep the 10-minute active window / 60-second touch throttle / 30-second count cache.

## Root cause evidence

The deployed image contains and loads `openedx_fpt_presence`, and the count URL resolves, but runtime settings show no presence middleware and no `FPT_PRESENCE_REDIS`. The service therefore fell back to `redis:6379` and failed DNS resolution. The fix moves critical runtime configuration into the installed Django plugin itself instead of relying only on the Tutor `openedx-common-settings` patch.

## Verification

- Source regression tests for login labels.
- Presence plugin settings tests for middleware registration and Redis URL discovery.
- Existing presence service/middleware/count behavior tests remain unchanged.
- Source checks ensure no browser heartbeat is introduced.
