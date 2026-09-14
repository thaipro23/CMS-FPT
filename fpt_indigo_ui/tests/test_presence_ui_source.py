from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
TUTOR_PLUGIN = REPO_ROOT / "tutor-plugins" / "fpt_indigo_ui.py"
PRESENCE_PATCH = REPO_ROOT / "fpt_indigo_ui" / "patches" / "presence_runtime.patch"


def test_tutor_presence_integration_markers():
    source = TUTOR_PLUGIN.read_text(encoding="utf-8")

    assert "openedx_fpt_presence" in source
    assert "FPTPresenceMiddleware" in source
    assert "FPT_PRESENCE_REDIS" in source
    assert "_read_patch(\"presence_runtime.patch\")" in source
    assert "org.openedx.frontend.layout.learning_header_actions.v1" in source
    assert "RenderWidget: FptPresenceBadge" in source


def test_learning_presence_badge_runtime_markers():
    source = PRESENCE_PATCH.read_text(encoding="utf-8")

    assert "const FptPresenceBadge" in source
    assert "/api/fpt-presence/v1/count" in source
    assert "credentials: 'include'" in source
    assert "60000" in source
    assert "Intl.NumberFormat('vi-VN')" in source
    assert "fpt-presence-badge__dot" in source
    assert "online" in source
