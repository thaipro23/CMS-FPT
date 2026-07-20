from setuptools import setup, find_packages

setup(
    name="openedx-ai-connector",
    version="0.1.5",
    packages=find_packages(),
    include_package_data=True,
    entry_points={
        "lms.djangoapp": [
            "openedx_ai_connector = openedx_ai_connector.apps:OpenEdxAIConnectorConfig",
        ],
        "cms.djangoapp": [
            "openedx_ai_connector = openedx_ai_connector.apps:OpenEdxAIConnectorConfig",
        ],
    },
)
