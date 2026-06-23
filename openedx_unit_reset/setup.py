from setuptools import setup, find_packages

setup(
    name="openedx-unit-reset",
    version="0.4.14",
    description="Open edX Unit reset and custom practice quiz timer runtime; v0.4.14 submit/check only, no Save click.",
    packages=find_packages(),
    include_package_data=True,
    entry_points={
        "lms.djangoapp": [
            "openedx_unit_reset = openedx_unit_reset.apps:UnitResetConfig",
        ],
        "cms.djangoapp": [
            "openedx_unit_reset = openedx_unit_reset.apps:UnitResetConfig",
        ],
    },
)
