from setuptools import find_packages, setup

setup(
    name="openedx-fpt-auth",
    version="1.0.0",
    description="Existing-user-only FEID and Google account linking for Open edX",
    packages=find_packages(),
    include_package_data=True,
    zip_safe=False,
    entry_points={
        "lms.djangoapp": [
            "openedx_fpt_auth = openedx_fpt_auth.apps:FPTAuthConfig",
        ],
    },
)
