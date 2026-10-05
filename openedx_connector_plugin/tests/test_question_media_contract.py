from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MEDIA = (ROOT / "openedx_ai_connector" / "media_publish.py").read_text(encoding="utf-8")
STUDIO = (ROOT / "openedx_ai_connector" / "studio.py").read_text(encoding="utf-8")


def test_library_media_uses_core_static_reference_not_authoring_url():
    assert "learner_ref = f'/{static_path}'" in MEDIA
    assert "studio_url = str(getattr(static_file, 'url', '')" in MEDIA
    assert "final_olx = final_olx.replace(asset['placeholder'], learner_ref)" in MEDIA
    assert "OLX learner không được chứa URL authoring" in MEDIA


def test_acms_media_path_is_staging_safe():
    assert "path.startswith('static/acms-legacy/')" in MEDIA
    assert "if not path.startswith('static/acms/'):" in MEDIA
    assert "staged_name = f'staged-content-temp/{path}'" in MEDIA
    assert "if len(staged_name) > 100:" in MEDIA


def test_library_component_uses_core_publish_api():
    assert "publish_component_changes," in MEDIA
    assert "publish_component_changes(usage_key, user_id)" in MEDIA


def test_existing_and_new_itembank_children_use_core_sync_and_persisted_asset_verification():
    assert "mode': 'native_sync_library_content_existing_child'" in STUDIO
    assert "notices = sync_library_content(existing, publish_request, store)" in STUDIO
    assert "_require_clean_static_file_notices(notices, upstream_ref)" in STUDIO
    assert "_verify_core_synced_static_assets(existing, upstream_ref)" in STUDIO
    assert "_verify_core_synced_static_assets(child, upstream_ref)" in STUDIO
    assert "build_components_import_path" in STUDIO
    assert "contentstore().find(asset_key)" in STUDIO
