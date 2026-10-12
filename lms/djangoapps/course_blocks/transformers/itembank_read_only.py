"""Read-only ItemBank selection policy for reporting requests.

Normal learner requests may persist a newly randomized selection. Reporting
requests use this module to observe missing or stale selections without
creating learner state as a side effect.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field


@dataclass(frozen=True)
class StoredSelectionResolution:
    """A normalized view of already-persisted ItemBank selections."""

    selected: list
    invalid: list
    overlimit: list
    missing_count: int

    @property
    def requires_write(self):
        return bool(self.invalid or self.overlimit or self.missing_count)


@dataclass
class ItemBankSelectionObservation:
    """Collect selection state that a normal learner request would repair."""

    issues: list[dict] = field(default_factory=list)

    @property
    def requires_write(self):
        return bool(self.issues)

    @property
    def status(self):
        if not self.issues:
            return "ready"
        if any(issue.get("reason") == "selection_missing" for issue in self.issues):
            return "selection_missing"
        return "selection_stale"

    def record(self, usage_key, *, reason, missing_count=0, invalid=None, overlimit=None):
        self.issues.append({
            "usage_key": str(usage_key),
            "reason": reason,
            "missing_count": int(missing_count or 0),
            "invalid": [list(item) for item in (invalid or [])],
            "overlimit": [list(item) for item in (overlimit or [])],
        })


_READ_ONLY_OBSERVATION = ContextVar("itembank_read_only_observation", default=None)


def current_itembank_read_only_observation():
    return _READ_ONLY_OBSERVATION.get()


@contextmanager
def observe_itembank_selections_read_only():
    observation = ItemBankSelectionObservation()
    token = _READ_ONLY_OBSERVATION.set(observation)
    try:
        yield observation
    finally:
        _READ_ONLY_OBSERVATION.reset(token)


def resolve_stored_selection(selected, valid_block_keys, max_count):
    """Normalize persisted selection without adding a random child.

    This intentionally differs from ``ItemBankMixin.make_selection``: missing
    entries remain missing so a report cannot create or change learner state.
    """

    valid = set(valid_block_keys)
    target_count = len(valid) if max_count < 0 else min(max(int(max_count), 0), len(valid))
    seen = set()
    kept = []
    invalid = []

    for raw in selected or []:
        if not isinstance(raw, (list, tuple)) or len(raw) != 2:
            invalid.append(("invalid", repr(raw)))
            continue
        key = tuple(raw)
        if not all(isinstance(value, str) for value in key):
            invalid.append(("invalid", repr(raw)))
            continue
        if key not in valid:
            invalid.append(key)
            continue
        if key in seen:
            invalid.append(key)
            continue
        seen.add(key)
        kept.append(list(key))

    overlimit = [tuple(item) for item in kept[target_count:]]
    kept = kept[:target_count]
    missing_count = max(target_count - len(kept), 0)
    return StoredSelectionResolution(
        selected=kept,
        invalid=sorted(invalid),
        overlimit=sorted(overlimit),
        missing_count=missing_count,
    )
