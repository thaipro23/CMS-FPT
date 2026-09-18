import runpy
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace


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
LEGACY_PRESENCE_PATCH = REPO_ROOT / "fpt_indigo_ui" / "patches" / "legacy_presence.patch"
LEGACY_CMS_PRESENCE_PATCH = REPO_ROOT / "fpt_indigo_ui" / "patches" / "legacy_cms_presence.patch"


def _hook_block(source: str, hook_name: str) -> str:
    anchor = f'"{hook_name}",'
    start = source.index(anchor)
    end = source.index("\n))", start)
    return source[start:end]


class _Collector:
    def __init__(self):
        self.items = []

    def add_item(self, item, **_kwargs):
        self.items.append(item)

    def add_items(self, items, **_kwargs):
        self.items.extend(items)


def _load_plugin_slots():
    config_defaults = _Collector()
    env_patches = _Collector()
    plugin_slots = _Collector()

    tutor_module = ModuleType("tutor")
    tutor_module.hooks = SimpleNamespace(
        Filters=SimpleNamespace(
            CONFIG_DEFAULTS=config_defaults,
            ENV_PATCHES=env_patches,
        )
    )

    tutormfe_module = ModuleType("tutormfe")
    tutormfe_module.__path__ = []
    tutormfe_hooks_module = ModuleType("tutormfe.hooks")
    tutormfe_hooks_module.PLUGIN_SLOTS = plugin_slots

    replacements = {
        "tutor": tutor_module,
        "tutormfe": tutormfe_module,
        "tutormfe.hooks": tutormfe_hooks_module,
    }
    previous = {name: sys.modules.get(name) for name in replacements}
    sys.modules.update(replacements)
    try:
        runpy.run_path(str(TUTOR_PLUGIN))
    finally:
        for name, module in previous.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    return plugin_slots.items


def test_tutor_presence_integration_markers():
    tutor_source = TUTOR_PLUGIN.read_text(encoding="utf-8")
    settings_source = PRESENCE_SETTINGS.read_text(encoding="utf-8")

    assert "openedx_fpt_presence" in tutor_source
    assert "FPT_PRESENCE_REDIS = {" not in tutor_source
    assert "FPTPresenceMiddleware" in settings_source
    assert "discover_redis_url" in settings_source
    assert "org.openedx.frontend.layout.learning_header_actions.v1" in tutor_source


def test_presence_widget_uses_supported_header_slots_without_touching_authn():
    tutor_source = TUTOR_PLUGIN.read_text(encoding="utf-8")
    presence_source = PRESENCE_PATCH.read_text(encoding="utf-8")
    legacy_source = LEGACY_PRESENCE_PATCH.read_text(encoding="utf-8")
    legacy_cms_source = LEGACY_CMS_PRESENCE_PATCH.read_text(encoding="utf-8")

    shared_runtime = _hook_block(
        tutor_source,
        "mfe-env-config-runtime-definitions",
    )

    assert '_read_patch("runtime.patch")' in shared_runtime
    assert "presence_runtime.patch" not in shared_runtime

    assert "FPT_PRESENCE_HEADER_SLOTS_V2" in tutor_source
    assert '"authn"' not in tutor_source.split("FPT_PRESENCE_MFE_APPS =", 1)[1].split("for _mfe in FPT_PRESENCE_MFE_APPS", 1)[0]
    assert "org.openedx.frontend.layout.learning_header_actions.v1" in tutor_source
    assert "org.openedx.frontend.layout.studio_header_search_button_slot.v1" in tutor_source
    assert "org.openedx.frontend.layout.header_desktop_secondary_menu.v1" in tutor_source
    assert '_read_patch("legacy_presence.patch")' in tutor_source
    assert '_read_patch("legacy_cms_presence.patch")' in tutor_source

    assert "FPT_PRESENCE_HEADER_SLOTS_V2" in presence_source
    assert "/api/fpt-presence/v1/count" in presence_source
    assert "credentials: 'include'" in presence_source
    assert "60000" in presence_source
    assert "Intl.NumberFormat('vi-VN')" in presence_source
    assert "fpt-presence-badge__dot" in presence_source
    assert "online" in presence_source

    assert "FPT_PRESENCE_LEGACY_HEADER_V1" in legacy_source
    assert "/api/fpt-presence/v1/count" in legacy_source
    assert "setInterval(refresh, 60000)" in legacy_source

    assert "FPT_PRESENCE_LEGACY_CMS_HEADER_V1" in legacy_cms_source
    assert "/api/fpt-presence/v1/count" in legacy_cms_source
    assert "setInterval(refresh, 60000)" in legacy_cms_source


def test_presence_slots_match_versions_locked_by_ulmo4_mfes():
    slots = _load_plugin_slots()
    presence_slots = {
        mfe: slot_name
        for mfe, slot_name, config in slots
        if "fpt_presence_badge" in config
    }

    assert presence_slots["learning"] == (
        "org.openedx.frontend.layout.learning_header_actions.v1"
    )
    assert presence_slots["authoring"] == (
        "org.openedx.frontend.layout.studio_header_search_button_slot.v1"
    )
    for mfe in (
        "account",
        "admin-console",
        "communications",
        "discussions",
        "gradebook",
        "learner-dashboard",
        "ora-grading",
        "profile",
    ):
        assert presence_slots[mfe] == (
            "org.openedx.frontend.layout.header_desktop_secondary_menu.v1"
        )

    assert "authn" not in presence_slots


def test_authn_login_labels_are_student_and_staff_without_changing_provider_routing():
    canonical = AUTHN_PATCH.read_text(encoding="utf-8")
    labels = AUTHN_LABELS_PATCH.read_text(encoding="utf-8")

    assert "FPT_AUTHN_CANONICAL_V1" in canonical
    assert "Student Login" in labels
    assert "Staff Login" in labels
    assert "Sign in with FEID" in labels
    assert "Sign in with Google" in labels
    assert "provider.loginUrl" in canonical

    # The follow-up patch must fail the image build if canonical Authn styling
    # disappears, preventing a repeat of the unstyled login rollout.
    assert "FPT_AUTHN_CANONICAL_V1" in labels
    assert ".fpt-auth-visual--large" in labels
    assert ".fpt-sso-provider--feid" in labels
