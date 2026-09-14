from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
TUTOR_PLUGIN = REPO_ROOT / "tutor-plugins" / "fpt_indigo_ui.py"
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
    assert "org.openedx.frontend.layout.learning_header_actions.v1" in tutor_source


def test_presence_widget_is_learning_only_and_not_in_shared_mfe_runtime():
    source = TUTOR_PLUGIN.read_text(encoding="utf-8")

    runtime_block = source.split('"mfe-env-config-runtime-definitions"', 1)[1]
    runtime_block = runtime_block.split("FPT_FOOTER_SLOT", 1)[0]

    assert '_read_patch("runtime.patch")' in runtime_block
    assert "presence_runtime.patch" not in runtime_block
    assert "FPT_PRESENCE_LEARNING_ONLY_V1" in source
    assert "/api/fpt-presence/v1/count" in source
    assert "credentials: 'include'" in source
    assert "60000" in source
    assert "Intl.NumberFormat('vi-VN')" in source
    assert "fpt-presence-badge" in source
    assert "online" in source


def test_authn_login_labels_are_student_and_staff_without_changing_provider_routing():
    canonical = AUTHN_PATCH.read_text(encoding="utf-8")
    labels = AUTHN_LABELS_PATCH.read_text(encoding="utf-8")

    assert "FPT_AUTHN_CANONICAL_V1" in canonical
    assert "Student Login" in labels
    assert "Staff Login" in labels
    assert "Sign in with FEID" in labels
    assert "Sign in with Google" in labels
    assert "provider.loginUrl" in canonical
