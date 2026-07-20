from setuptools import setup, find_packages

setup(
    name="openedx-unit-reset",
    version='0.4.14.6',
    description="Reset whole Open edX Unit attempt and Problem Bank randomization with server-side cooldown and custom timed-practice quiz runtime.",
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
