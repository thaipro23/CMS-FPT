from __future__ import annotations

from typing import Any, Mapping


def _positive_slot_number(value: Any, field_name: str) -> int | None:
    if value is None or value == '':
        return None
    if isinstance(value, bool):
        raise ValueError(f'{field_name} must be a positive integer')
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f'{field_name} must be a positive integer') from exc
    if number <= 0 or str(value).strip() not in {str(number), f'+{number}'}:
        raise ValueError(f'{field_name} must be a positive integer')
    return number


def resolve_problem_bank_slot_number(slot: Mapping[str, Any]) -> int:
    """Resolve the stable Problem Bank discriminator from slot_m0/slot_no."""
    slot_m0 = _positive_slot_number(slot.get('slot_m0'), 'slot_m0')
    slot_no = _positive_slot_number(slot.get('slot_no'), 'slot_no')
    if slot_m0 is None and slot_no is None:
        raise ValueError('Problem Bank slot requires slot_m0 or slot_no')
    if slot_m0 is not None and slot_no is not None and slot_m0 != slot_no:
        raise ValueError(f'Problem Bank slot metadata mismatch: slot_m0={slot_m0}, slot_no={slot_no}')
    return slot_m0 if slot_m0 is not None else slot_no  # type: ignore[return-value]


def problem_bank_slot_display_name(slot: Mapping[str, Any]) -> str:
    """Build the primary Studio display name with the slot discriminator."""
    slot_number = resolve_problem_bank_slot_number(slot)
    family_names = slot.get('family_names')
    labels: list[str] = []
    if isinstance(family_names, (list, tuple)):
        for value in family_names:
            label = str(value or '').strip()
            if label and label not in labels:
                labels.append(label)
    elif family_names:
        label = str(family_names).strip()
        if label:
            labels.append(label)

    suffix = ' / '.join(labels)
    if not suffix:
        suffix = str(slot.get('difficulty') or '').strip().upper()

    display_name = f'Problem Bank Slot {slot_number:02d}'
    if suffix:
        display_name = f'{display_name} - {suffix}'
    return display_name
