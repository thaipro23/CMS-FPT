from pathlib import Path

import pytest

from openedx_connector_plugin.openedx_ai_connector.itembank_naming import (
    problem_bank_slot_display_name,
    resolve_problem_bank_slot_number,
)


def test_slot_no_drives_primary_display_name():
    assert problem_bank_slot_display_name({
        'slot_no': 1,
        'family_names': ['Vector', 'Raster'],
        'difficulty': 'easy',
    }) == 'Problem Bank Slot 01 - Vector / Raster'


def test_slot_m0_is_accepted_as_primary_discriminator():
    assert problem_bank_slot_display_name({
        'slot_m0': 2,
        'difficulty': 'medium',
    }) == 'Problem Bank Slot 02 - MEDIUM'


def test_matching_slot_m0_and_slot_no_are_accepted():
    assert resolve_problem_bank_slot_number({'slot_m0': '3', 'slot_no': 3}) == 3


def test_slot_metadata_mismatch_is_rejected():
    with pytest.raises(ValueError, match='metadata mismatch'):
        resolve_problem_bank_slot_number({'slot_m0': 4, 'slot_no': 5})


def test_missing_or_invalid_slot_metadata_is_rejected():
    for slot in ({}, {'slot_no': 0}, {'slot_m0': 'not-a-number'}):
        with pytest.raises(ValueError):
            resolve_problem_bank_slot_number(slot)


def test_views_runtime_wiring_calls_an_existing_studio_title_normalizer():
    plugin_root = Path(__file__).resolve().parents[1]
    views_source = (plugin_root / 'openedx_ai_connector' / 'views.py').read_text(encoding='utf-8')
    studio_source = (plugin_root / 'openedx_ai_connector' / 'studio.py').read_text(encoding='utf-8')

    assert '_normalized_xblock_display_name' not in views_source
    assert 'def _normalize_xblock_title(' in studio_source
    assert '_studio._normalize_xblock_title(' in views_source
