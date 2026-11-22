import pytest

from src.services.ticket_import import (
    build_invalidation_reason,
    check_version_conflict,
    select_rules_to_invalidate,
)
from src.constants.enums import RuleStatus


def test_stale_expected_version_is_rejected():
    order = {"status": "imported", "version": 2}
    msg = check_version_conflict(order, expected_version=1)
    assert msg is not None and "版本已过期" in msg


def test_current_expected_version_passes():
    order = {"status": "imported", "version": 2}
    assert check_version_conflict(order, expected_version=2) is None


def test_missing_expected_version_does_not_block():
    order = {"status": "imported", "version": 3}
    assert check_version_conflict(order, expected_version=None) is None


def test_voided_ticket_can_never_be_resurrected():
    order = {"status": "voided", "version": 2}
    # Even a statement that thinks it is current must not bring a voided ticket back.
    assert "撤销" in check_version_conflict(order, expected_version=2)
    assert "撤销" in check_version_conflict(order, expected_version=None)


def test_only_live_candidates_on_old_sample_are_invalidated():
    rules = [
        {"id": 1, "status": RuleStatus.draft.value, "sample_version": 1},
        {"id": 2, "status": RuleStatus.submitted.value, "sample_version": 1},
        {"id": 3, "status": RuleStatus.approved.value, "sample_version": 1},
        # published rules keep their frozen publish-time basis
        {"id": 4, "status": RuleStatus.published.value, "sample_version": 1},
        # terminal candidate states are never rewritten
        {"id": 5, "status": RuleStatus.blocked.value, "sample_version": 1},
        {"id": 6, "status": RuleStatus.rejected.value, "sample_version": 1},
        # already on the new sample stays valid
        {"id": 7, "status": RuleStatus.draft.value, "sample_version": 2},
        # already invalidated stays invalidated
        {"id": 8, "status": RuleStatus.invalidated.value, "sample_version": 1},
    ]
    ids = {r["id"] for r in select_rules_to_invalidate(rules, base_version=1)}
    assert ids == {1, 2, 3}


def test_invalidation_reason_mentions_batch_and_is_capped():
    reason = build_invalidation_reason(9, ["POS-1", "POS-2"])
    assert "#9" in reason and "POS-1" in reason
    long_reason = build_invalidation_reason(1, [f"POS-{i}" for i in range(50)])
    assert len(long_reason) <= 240
