from __future__ import annotations


def cms_staff_requested(payload) -> bool:
    """Accept only the explicit signed flag, never a truthy string."""

    return isinstance(payload, dict) and payload.get('require_staff') is True


def ensure_required_cms_staff(user, *, required: bool) -> dict:
    """Set and verify Django staff access without granting superuser access."""

    was_staff = bool(getattr(user, 'is_staff', False))
    if required and not was_staff:
        user.is_staff = True
        user.save(update_fields=['is_staff'])
        refresh = getattr(user, 'refresh_from_db', None)
        if callable(refresh):
            refresh(fields=['is_staff'])

    is_staff = bool(getattr(user, 'is_staff', False))
    if required and not is_staff:
        raise RuntimeError('Không thể xác nhận is_staff cho tài khoản CMS.')

    return {
        'is_staff': is_staff,
        'is_superuser': bool(getattr(user, 'is_superuser', False)),
        'staff_updated': bool(required and not was_staff and is_staff),
    }
