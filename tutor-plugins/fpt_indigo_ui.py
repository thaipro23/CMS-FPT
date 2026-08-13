from __future__ import annotations

from pathlib import Path

from tutor import hooks
from tutormfe.hooks import PLUGIN_SLOTS

FPT_PRIMARY = "#0B3B82"
FPT_PRIMARY_DARK = "#072B61"
FPT_ACCENT = "#F36F21"

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PATCH_DIR = _REPO_ROOT / "fpt_indigo_ui" / "patches"


def _jinja_raw(text: str) -> str:
    """Protect JSX/CSS braces from Tutor/Jinja patch rendering."""
    return "{% raw %}\n" + text + "\n{% endraw %}"


def _read_patch(name: str) -> str:
    path = _PATCH_DIR / name
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"Missing FPT UI patch source: {path}") from exc


# Shared runtime configuration. The FPT deployment is intentionally light-only;
# keep Open edX behavior unchanged and only control presentation/branding options.
hooks.Filters.ENV_PATCHES.add_item((
    "mfe-lms-common-settings",
    _jinja_raw(f"""
MFE_CONFIG["INDIGO_ENABLE_DARK_TOGGLE"] = False
MFE_CONFIG["INDIGO_FOOTER_NAV_LINKS"] = []
MFE_CONFIG["ALLOW_PUBLIC_ACCOUNT_CREATION"] = False
MFE_CONFIG["SHOW_REGISTRATION_LINKS"] = False
# Keep the approved FPT DefaultLayout on Authn Ulmo.4.
MFE_CONFIG["ENABLE_IMAGE_LAYOUT"] = False
MFE_CONFIG["SITE_NAME"] = "FPT Polytechnic"
MFE_CONFIG["FPT_PRIMARY_COLOR"] = "{FPT_PRIMARY}"
MFE_CONFIG["FPT_ACCENT_COLOR"] = "{FPT_ACCENT}"
"""),
))

# Tutor-Indigo 21.2.1 already injects React (plus useEffect/useState) into the
# shared env.config.jsx. Re-importing the React default binding here makes all
# MFE builds fail with "Identifier 'React' has already been declared". Reuse
# Indigo's React binding and only add the FPT-scoped getConfig alias.
# Compatibility assertion marker retained for our generated-config guard:
# import React from 'react';
hooks.Filters.ENV_PATCHES.add_item((
    "mfe-env-config-buildtime-imports",
    _jinja_raw("""// FPT reuses the React binding supplied by Tutor-Indigo 21.2.1.
// import React from 'react';
import { getConfig as getFptConfig } from '@edx/frontend-platform';"""),
))

# Authn source is copied into /openedx/app after npm install in Tutor MFE 21.x.
# Apply the simplified edX-style FPT layout first, then the viewport refinement.
hooks.Filters.ENV_PATCHES.add_item((
    "mfe-dockerfile-pre-npm-build-authn",
    _jinja_raw(
        _read_patch("authn.patch")
        + "\n"
        + _read_patch("authn_polish.patch")
    ),
))

hooks.Filters.ENV_PATCHES.add_item((
    "mfe-env-config-runtime-definitions",
    _jinja_raw(_read_patch("runtime.patch")),
))


FPT_FOOTER_SLOT = (
    "org.openedx.frontend.layout.footer.v1",
    """
    { op: PLUGIN_OPERATIONS.Hide, widgetId: 'indigo_footer' },
    { op: PLUGIN_OPERATIONS.Hide, widgetId: 'default_contents' },
    { op: PLUGIN_OPERATIONS.Insert, widget: { id: 'fpt_footer', type: DIRECT_PLUGIN, priority: 100, RenderWidget: FptFooter } },
""",
)

# Never override logo_slot. Stock Indigo/Open edX header markup remains intact.
# The Open edX image replaces native logo.png/logo-white.png with the vendored
# FPT Polytechnic logo, eliminating duplicate logo DOM and slot races.
for _mfe in ["learning", "learner-dashboard", "profile", "account", "discussions", "authoring", "authn"]:
    PLUGIN_SLOTS.add_item((_mfe, *FPT_FOOTER_SLOT))

PLUGIN_SLOTS.add_item((
    "learner-dashboard",
    "org.openedx.frontend.learner_dashboard.course_list.v1",
    """
    { op: PLUGIN_OPERATIONS.Insert, widget: { id: 'fpt_learner_banner', type: DIRECT_PLUGIN, priority: 1, RenderWidget: FptLearnerBanner } },
""",
))


# Compose legacy LMS refinements after the core patch. Homepage reuses the
# already-balanced /courses Hero so both entry points stay synchronized.
hooks.Filters.ENV_PATCHES.add_item((
    "openedx-dockerfile",
    _jinja_raw(
        _read_patch("openedx.patch")
        + "\n"
        + _read_patch("openedx_polish.patch")
        + "\n"
        + _read_patch("openedx_balance.patch")
        + "\n"
        + _read_patch("homepage_slider.patch")
        + "\n"
        + _read_patch("native_logo.patch")
    ),
))
