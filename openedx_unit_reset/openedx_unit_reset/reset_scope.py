"""Pure helpers that keep a Unit reset scoped to the requested Unit."""


def official_reset_targets(unit_key, structural_keys, expanded_keys):
    """Return the smallest target set for Open edX's recursive reset API.

    ``reset_student_attempts`` recursively resets the structural descendants of
    ``unit_key``. Problem-bank selections discovered in learner state are not
    necessarily structural descendants, so they are appended as explicit
    targets. Keys from unrelated Units are never introduced here.
    """
    structural = set(structural_keys)
    expanded = set(expanded_keys)
    selected_dynamic_keys = sorted(expanded - structural, key=str)
    return [unit_key, *selected_dynamic_keys]
