import os

import django


os.environ.setdefault(
    "DJANGO_SETTINGS_MODULE",
    "openedx_fpt_report_proxy.tests.settings",
)
django.setup()
