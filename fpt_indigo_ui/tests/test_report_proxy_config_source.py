from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
MINIO_PLUGIN = REPO_ROOT / "tutor-plugins" / "fpt_external_minio.py"


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
