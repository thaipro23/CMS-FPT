"""FPT global grade-passing policy, independent of display rounding."""

from math import isfinite

# Floating-point accumulation tolerance: 1e-10 percentage points.
_PERFECT_GRADE_ABS_TOLERANCE = 1e-12


def has_perfect_grade(raw_percent):
    """Only a finite grade equal to 100% within numerical noise passes."""
    if isinstance(raw_percent, bool) or raw_percent is None:
        return False
    try:
        value = float(raw_percent)
    except (TypeError, ValueError, OverflowError):
        return False
    return isfinite(value) and abs(value - 1.0) <= _PERFECT_GRADE_ABS_TOLERANCE
