from django.urls import path

from .views import (
    quiz_session_lock,
    quiz_session_reset,
    quiz_session_runtime_js,
    quiz_session_start,
    quiz_session_status,
    quiz_session_timeout,
    quiz_timer_config_upsert,
    reset_unit_attempt,
    reset_unit_status,
)

urlpatterns = [
    path("v1/reset/", reset_unit_attempt, name="reset_unit_attempt"),
    path("v1/status/", reset_unit_status, name="reset_unit_status"),
    path("v1/quiz-session/status", quiz_session_status, name="quiz_session_status"),
    path("v1/quiz-session/status/", quiz_session_status, name="quiz_session_status_slash"),
    path("v1/quiz-session/start", quiz_session_start, name="quiz_session_start"),
    path("v1/quiz-session/start/", quiz_session_start, name="quiz_session_start_slash"),
    path("v1/quiz-session/timeout", quiz_session_timeout, name="quiz_session_timeout"),
    path("v1/quiz-session/timeout/", quiz_session_timeout, name="quiz_session_timeout_slash"),
    path("v1/quiz-session/lock", quiz_session_lock, name="quiz_session_lock"),
    path("v1/quiz-session/lock/", quiz_session_lock, name="quiz_session_lock_slash"),
    path("v1/quiz-session/reset", quiz_session_reset, name="quiz_session_reset"),
    path("v1/quiz-session/reset/", quiz_session_reset, name="quiz_session_reset_slash"),
    path("v1/quiz-config/upsert", quiz_timer_config_upsert, name="quiz_timer_config_upsert"),
    path("v1/quiz-config/upsert/", quiz_timer_config_upsert, name="quiz_timer_config_upsert_slash"),
    path("v1/quiz-session/runtime.js", quiz_session_runtime_js, name="quiz_session_runtime_js"),
]
