"""Property-based test of the JobStatus state machine.

Every illegal transition must raise ValueError; every legal one must
succeed.  Adding a new JobStatus enum value without updating
LEGAL_TRANSITIONS fails this test immediately — it is the safety net
against silent enum extension.
"""

from __future__ import annotations

import pytest
from app.models.job import LEGAL_TRANSITIONS, JobStatus, assert_transition_legal
from hypothesis import given
from hypothesis import strategies as st

all_states = st.sampled_from(list(JobStatus))


@given(src=all_states, dst=all_states)
def test_transition_legality(src: JobStatus, dst: JobStatus) -> None:
    if src == dst or (src, dst) in LEGAL_TRANSITIONS:
        # Legal: must not raise
        assert_transition_legal(src, dst)
    else:
        # Illegal: must raise ValueError with an informative message
        with pytest.raises(ValueError, match="illegal Job status transition"):
            assert_transition_legal(src, dst)


def test_terminal_statuses_have_no_outgoing_transitions() -> None:
    """Defines which statuses are terminal and verifies no edge leaves them."""
    terminals = {
        JobStatus.SUCCEEDED,
        JobStatus.FAILED,
        JobStatus.CANCELLED,
        JobStatus.TIMEOUT,
    }

    for term in terminals:
        outgoing = [dst for (src, dst) in LEGAL_TRANSITIONS if src == term]
        assert outgoing == [], (
            f"{term!r} should be terminal but has outgoing edges to {outgoing}"
        )


def test_every_non_terminal_status_is_reachable_from_queued_backend() -> None:
    """No active non-terminal JobStatus is an orphan.

    Every non-terminal state except known legacy dormant states must be
    reachable from QUEUED_BACKEND via legal transitions.  This catches
    accidental enum additions with no incoming edges.

    Terminal statuses (SUCCEEDED, FAILED, CANCELLED, TIMEOUT) are excluded
    from the reachability check: they are valid leaf nodes with no outgoing
    edges.  The companion test above already asserts they have no outgoing
    edges; here we only care that every *active* state is reachable so the
    workflow graph has no disconnected components.

    PENDING is a legacy SQLAlchemy ORM column default retained for backward
    compatibility with existing DB rows that pre-date Phase 6 (QUEUED_BACKEND).
    All new jobs start at QUEUED_BACKEND; no code path transitions *into*
    PENDING from another state, so it has no incoming edge in LEGAL_TRANSITIONS.
    It is excluded here deliberately.  If PENDING is ever removed from the
    enum, remove it from LEGACY_DORMANT as well.
    """
    terminals = {
        JobStatus.SUCCEEDED,
        JobStatus.FAILED,
        JobStatus.CANCELLED,
        JobStatus.TIMEOUT,
    }
    # States that exist in the enum for backward compat but have no incoming
    # transition edge and are intentionally excluded from reachability.
    legacy_dormant = {JobStatus.PENDING}

    reachable: set[JobStatus] = {JobStatus.QUEUED_BACKEND}
    changed = True
    while changed:
        changed = False
        for src, dst in LEGAL_TRANSITIONS:
            if src in reachable and dst not in reachable:
                reachable.add(dst)
                changed = True

    active_non_terminals = set(JobStatus) - terminals - legacy_dormant
    unreachable = active_non_terminals - reachable
    assert not unreachable, (
        f"non-terminal statuses unreachable from QUEUED_BACKEND: {unreachable}"
    )
