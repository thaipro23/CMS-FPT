# Open edX FPT Presence

Small Open edX plugin that records authenticated backend activity in Redis and exposes an aggregate active-user count.

- Redis sorted set: `fpt:presence:active`
- Activity window: 10 minutes
- Per-worker touch throttle: 60 seconds per user
- Count cache: 30 seconds
- API: `GET /api/fpt-presence/v1/count`
- Redis failures are fail-open and never block LMS/CMS requests.

The plugin is installed and configured by `tutor-plugins/fpt_indigo_ui.py`. It does not use MySQL, MongoDB, Celery, or frontend heartbeats.
