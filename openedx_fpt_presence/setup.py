from setuptools import find_packages, setup


setup(
    name="openedx-fpt-presence",
    version="1.0.0",
    description="Low-overhead Redis presence tracking for FPT Open edX",
    packages=find_packages(),
    include_package_data=True,
    zip_safe=False,
    entry_points={
        "lms.djangoapp": [
            "openedx_fpt_presence = openedx_fpt_presence.apps:FPTPresenceConfig",
        ],
        "cms.djangoapp": [
            "openedx_fpt_presence = openedx_fpt_presence.apps:FPTPresenceConfig",
        ],
    },
)
