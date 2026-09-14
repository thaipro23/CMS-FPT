from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
TUTOR_PLUGIN = REPO_ROOT / "tutor-plugins" / "fpt_indigo_ui.py"
PRESENCE_PATCH = REPO_ROOT / "fpt_indigo_ui" / "patches" / "presence_runtime.patch"
PRESENCE_SETTINGS = (
    REPO_ROOT
    / "openedx_fpt_presence"
    / "openedx_fpt_presence"
    / "settings"
    / "common.py"
)
AUTHN_PATCH = REPO_ROOT / "fpt_indigo_ui" / "patches" / "authn.patch"
AUTHN_LABELS_PATCH = REPO_ROOT / "fpt_indigo_ui" / "patches" / "authn_labels.patch"


def test_tutor_presence_integration_markers():
    tutor_source = TUTOR_PLUGIN.read_text(encoding="utf-8")
    settings_source = PRESENCE_SETTINGS.read_text(encoding="utf-8")

    assert "openedx_fpt_presence" in tutor_source
    assert "FPT_PRESENCE_REDIS = {" not in tutor_source
    assert "FPTPresenceMiddleware" in settings_source
    assert "discover_redis_url" in settings_source
    assert "_read_patch(\"presence_runtime.patch\")" in tutor_source
    assert "org.openedx.frontend.layout.learning_header_actions.v1" in tutor_source
    assert "RenderWidget: FptPresenceBadge" in tutor_source


def test_learning_presence_badge_runtime_markers():
    source = PRESENCE_PATCH.read_text(encoding="utf-8")

    assert "const FptPresenceBadge" in source
    assert "/api/fpt-presence/v1/count" in source
    assert "credentials: 'include'" in source
    assert "60000" in source
    assert "Intl.NumberFormat('vi-VN')" in source
    assert "fpt-presence-badge__dot" in source
    assert "online" in source
    assert "heartbeat" not in source.lower().replace("no browser heartbeat", "")


def test_authn_login_labels_are_student_and_staff_without_changing_provider_routing():
    canonical = AUTHN_PATCH.read_text(encoding="utf-8")
    labels = AUTHN_LABELS_PATCH.read_text(encoding="utf-8")

    assert "FPT_AUTHN_CANONICAL_V1" in canonical
    assert "Student Login" in labels
    assert "Staff Login" in labels
    assert "Sign in with FEID" in labels
    assert "Sign in with Google" in labels
    assert "provider.loginUrl" in canonical
