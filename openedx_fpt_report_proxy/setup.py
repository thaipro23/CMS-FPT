from setuptools import find_packages, setup


setup(
    name="openedx-fpt-report-proxy",
    version="1.1.0",
    description="Authenticated proxy downloads for private Open edX reports and artifacts",
    packages=find_packages(),
    include_package_data=True,
    zip_safe=False,
    entry_points={
        "lms.djangoapp": [
            "openedx_fpt_report_proxy = openedx_fpt_report_proxy.apps:FPTReportProxyConfig",
        ],
        "cms.djangoapp": [
            "openedx_fpt_report_proxy = openedx_fpt_report_proxy.apps:FPTReportProxyConfig",
        ],
    },
)
