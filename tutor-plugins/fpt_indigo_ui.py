from __future__ import annotations

from pathlib import Path

from tutor import hooks
from tutormfe.hooks import PLUGIN_SLOTS

FPT_PRIMARY = "#0B3B82"
FPT_PRIMARY_DARK = "#072B61"
FPT_ACCENT = "#F36F21"
FPT_TIME_ZONE = "Asia/Ho_Chi_Minh"

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

# FPT_TIMEZONE_V1
# Keep all server-side Open edX date handling on the FPT Polytechnic business
# timezone while preserving timezone-aware UTC storage semantics.
hooks.Filters.ENV_PATCHES.add_item((
    "openedx-common-settings",
    f"""
# FPT_TIMEZONE_V1
TIME_ZONE = "{FPT_TIME_ZONE}"
USE_TZ = True
""",
))

# FPT_PRESENCE_V2
# Install only the standalone Django plugin here. At runtime it reuses the Redis
# connection already configured by Open edX; it does not provision or reconfigure
# the external Redis service.
hooks.Filters.ENV_PATCHES.add_item((
    "openedx-dockerfile-pre-assets",
    r"""
# FPT_PRESENCE_V2
RUN if [ -n "$PIP_COMMAND" ]; then \
        $PIP_COMMAND install -e /openedx/edx-platform/openedx_fpt_presence; \
    else \
        pip install -e /openedx/edx-platform/openedx_fpt_presence; \
    fi
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
MFE_CONFIG["FPT_SSO_ONLY_AUTH"] = True
MFE_CONFIG["FPT_TIME_ZONE"] = "{FPT_TIME_ZONE}"
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

# Authn keeps the approved canonical layout/styles. The tiny follow-up patch only
# changes provider labels to Student Login / Staff Login; it does not touch CSS,
# layout, provider ordering, or OAuth URLs.
hooks.Filters.ENV_PATCHES.add_item((
    "mfe-dockerfile-pre-npm-build-authn",
    _jinja_raw(
        _read_patch("authn.patch")
        + "\n"
        + _read_patch("authn_labels.patch")
    ),
))

# Course Unit assessment/library-backed components are created through ACMS.
hooks.Filters.ENV_PATCHES.add_item((
    "mfe-dockerfile-pre-npm-build-authoring",
    _jinja_raw(_read_patch("authoring.patch")),
))

# Shared MFE runtime remains exactly the same as before presence was introduced.
hooks.Filters.ENV_PATCHES.add_item((
    "mfe-env-config-runtime-definitions",
    _jinja_raw(_read_patch("runtime.patch")),
))

# FPT_PRESENCE_HEADER_SLOTS_V2
# Open edX recommends extending MFEs through Frontend Plugin Framework slots
# instead of forking application source. Define the same small presence widget
# only in authenticated MFEs that expose supported header slots. Authn remains
# untouched so login layout/runtime cannot regress.
FPT_PRESENCE_STANDARD_HEADER_MFES = [
    "account",
    "admin-console",
    "communications",
    "discussions",
    "gradebook",
    "learner-dashboard",
    "ora-grading",
    "profile",
]
FPT_PRESENCE_STUDIO_HEADER_MFES = ["authoring"]
FPT_PRESENCE_MFE_APPS = [
    "learning",
    *FPT_PRESENCE_STANDARD_HEADER_MFES,
    *FPT_PRESENCE_STUDIO_HEADER_MFES,
]

for _mfe in FPT_PRESENCE_MFE_APPS:
    hooks.Filters.ENV_PATCHES.add_item((
        f"mfe-env-config-runtime-definitions-{_mfe}",
        _jinja_raw(_read_patch("presence_runtime.patch")),
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
for _mfe in ["learning", "learner-dashboard", "profile", "account", "discussions", "authoring", "authn"]:
    PLUGIN_SLOTS.add_item((_mfe, *FPT_FOOTER_SLOT))

# Header presence uses only documented Open edX frontend-component-header
# extension points. No DOM replacement and no core MFE source edits.
_FPT_PRESENCE_WIDGET = """
    { op: PLUGIN_OPERATIONS.Insert, widget: { id: 'fpt_presence_badge', type: DIRECT_PLUGIN, priority: 90, RenderWidget: FptPresenceBadge } },
"""

# Learning has a dedicated actions area immediately before the user menu.
PLUGIN_SLOTS.add_item((
    "learning",
    "org.openedx.frontend.layout.learning_header_actions.v1",
    _FPT_PRESENCE_WIDGET,
))

# Studio/Authoring exposes its own header actions slot.
for _mfe in FPT_PRESENCE_STUDIO_HEADER_MFES:
    PLUGIN_SLOTS.add_item((
        _mfe,
        "org.openedx.frontend.layout.studio_header_actions.v1",
        _FPT_PRESENCE_WIDGET,
    ))

# Other authenticated MFEs use the standard desktop secondary-header slot.
for _mfe in FPT_PRESENCE_STANDARD_HEADER_MFES:
    PLUGIN_SLOTS.add_item((
        _mfe,
        "org.openedx.frontend.layout.header_desktop_secondary_menu.v2",
        _FPT_PRESENCE_WIDGET,
    ))

PLUGIN_SLOTS.add_item((
    "learner-dashboard",
    "org.openedx.frontend.learner_dashboard.course_list.v1",
    """
    { op: PLUGIN_OPERATIONS.Insert, widget: { id: 'fpt_learner_banner', type: DIRECT_PLUGIN, priority: 1, RenderWidget: FptLearnerBanner } },
""",
))


# Legacy LMS branding/discovery is intentionally consolidated into one patch.
hooks.Filters.ENV_PATCHES.add_item((
    "openedx-dockerfile",
    _jinja_raw(
        _read_patch("openedx.patch")
        + "\n"
        + _read_patch("slider_images.patch")
        + "\n"
        + _read_patch("native_logo.patch")
        + "\n"
        + _read_patch("legacy_presence.patch")
    ),
))
