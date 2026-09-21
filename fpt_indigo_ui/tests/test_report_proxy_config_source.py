from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
MINIO_PLUGIN = REPO_ROOT / "tutor-plugins" / "fpt_external_minio.py"
SETUP_SCRIPT = REPO_ROOT / "scripts" / "fpt-ui-setup.sh"


def test_grade_reports_use_current_django_storage_contract():
    source = MINIO_PLUGIN.read_text(encoding="utf-8")

    assert "FPT_REPORT_PROXY_V2" in source
    assert 'GRADES_DOWNLOAD = {' in source
    assert '"STORAGE_CLASS":' in source
    assert "openedx_fpt_report_proxy.storage.FPTReportProxyS3Storage" in source
    assert '"STORAGE_KWARGS": {' in source

    # ReportStore.from_config() treats STORAGE_TYPE as the legacy configuration
    # path and ignores STORAGE_CLASS there. Do not reintroduce a legacy
    # STORAGE_TYPE assignment into the generated settings.
    assert 'GRADES_DOWNLOAD["STORAGE_TYPE"] =' not in source
    assert 'GRADES_DOWNLOAD["BUCKET"] =' not in source
    assert 'GRADES_DOWNLOAD["ROOT_PATH"] =' not in source


def test_course_exports_use_authenticated_cms_artifact_proxy():
    source = MINIO_PLUGIN.read_text(encoding="utf-8")

    assert "FPT_ARTIFACT_PROXY_V1" in source
    assert (
        'USER_TASKS_ARTIFACT_STORAGE = '
        '"openedx_fpt_report_proxy.storage.FPTUserTaskArtifactProxyS3Storage"'
    ) in source
    assert 'STORAGES["user_task_artifacts"]' in source
    assert 'FPT_ARTIFACT_PROXY_BASE_URL = "https://{{ CMS_HOST }}"' in source
    assert '"bucket_name": "{{ FPT_MINIO_BUCKET_NAME }}"' in source


def test_setup_validates_rendered_cms_artifact_proxy_settings():
    source = SETUP_SCRIPT.read_text(encoding="utf-8")

    assert 'GENERATED_CMS_SETTINGS="$TUTOR_ROOT/env/apps/openedx/settings/cms/production.py"' in source
    assert "FPTUserTaskArtifactProxyS3Storage" in source
    assert "Rendered CMS artifact-proxy configuration PASS" in source
