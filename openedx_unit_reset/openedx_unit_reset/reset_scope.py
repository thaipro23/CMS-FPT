"""Pure helpers that keep a Unit reset scoped to the learner's active attempt."""


def active_child_keys(structural_children, selected_children, dynamic=False):
    """Return children that belong to the learner's active block path.

    Ordinary containers expose every structural child. Item-bank containers
    expose only the children already stored in the learner's ``selected``
    state. An empty selection stays empty: reset must never randomize a new set.
    """
    structural_children = list(structural_children)
    if not dynamic:
        return structural_children

    selected_children = list(selected_children)
    selected = set(selected_children)
    structural = set(structural_children)
    # Keep stale selected keys too. A selected problem can have score/submission
    # state even after an author removes it from the current bank structure.
    return [key for key in selected_children if key in structural] + sorted(selected - structural, key=str)


def partition_persisted_reset_keys(persisted_keys, container_keys):
    """Split learner state into official leaf resets and container cleanup.

    Leaf state must pass through Open edX's supported reset API so submissions
    and score state are removed together. Container rows only hold navigation
    or ItemBank selection state and may be deleted directly after leaf resets.
    """
    persisted = set(persisted_keys)
    containers = set(container_keys)
    official = sorted(persisted - containers, key=str)
    cleanup = sorted(persisted & containers, key=str)
    return official, cleanup
