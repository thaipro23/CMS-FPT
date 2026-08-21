from __future__ import annotations

from pathlib import Path

from tutor import hooks
from tutormfe.hooks import PLUGIN_SLOTS

FPT_PRIMARY = "#0B3B82"
FPT_PRIMARY_DARK = "#072B61"
FPT_ACCENT = "#F36F21"

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PATCH_DIR = _REPO_ROOT / "fpt_indigo_ui" / "patches"


# Tutor core exposes only MYSQL_HOST for the primary database. Open edX itself
# already ships the read_replica alias and LMS ReadReplicaRouter, so expose the
# replica endpoint as first-class Tutor configuration without changing Open edX
# routing semantics. Falling back to the primary keeps non-HA deployments safe.
hooks.Filters.CONFIG_DEFAULTS.add_items([
    ("MYSQL_REPLICA_HOST", "{{ MYSQL_HOST }}"),
    ("MYSQL_REPLICA_PORT", "{{ MYSQL_PORT }}"),
])

# FPT_MYSQL_READ_REPLICA_V1
# Keep read_replica valid in both LMS and CMS settings. LMS automatically routes
# eligible reads through edx_django_utils.db.read_replica.ReadReplicaRouter;
# CMS does not register that router, but explicit .using("read_replica") helpers
# remain safe. Replica credentials/database/options intentionally mirror default.
hooks.Filters.ENV_PATCHES.add_item((
    "openedx-common-settings",
    """
# FPT_MYSQL_READ_REPLICA_V1
_fpt_read_replica = DATABASES["default"].copy()
_fpt_read_replica.pop("ATOMIC_REQUESTS", None)
_fpt_read_replica["HOST"] = "{{ MYSQL_REPLICA_HOST }}"
_fpt_read_replica["PORT"] = "{{ MYSQL_REPLICA_PORT }}"
DATABASES["read_replica"] = _fpt_read_replica
""",
))


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
# Compose the patches by responsibility: base FPT layout, viewport/branding
# polish, SSO-only FEID/Google interaction, then the final responsive shell lock.
hooks.Filters.ENV_PATCHES.add_item((
    "mfe-dockerfile-pre-npm-build-authn",
    _jinja_raw(
        _read_patch("authn.patch")
        + "\n"
        + _read_patch("authn_polish.patch")
        + "\n"
        + _read_patch("authn_sso.patch")
        + "\n"
        + _read_patch("authn_layout_fix.patch")
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


# Legacy LMS branding/discovery is intentionally consolidated into one patch.
# Native logo replacement remains separate because it operates on collected
# static assets after the theme/UI changes are rendered.
hooks.Filters.ENV_PATCHES.add_item((
    "openedx-dockerfile",
    _jinja_raw(
        _read_patch("openedx.patch")
        + "\n"
        + _read_patch("native_logo.patch")
    ),
))
