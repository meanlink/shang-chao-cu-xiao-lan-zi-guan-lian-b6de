"""Publish-time concurrency: a candidate whose sample moved must be rejected
and invalidated inside the same transaction."""

import pytest
from fastapi import HTTPException

from src.routes import reviews as reviews_route
from src.constants.enums import RuleStatus

from .fake_db import FakeCursor, fake_transaction

MANAGER = {"id": 9, "display_name": "周宁"}


def _prepare(monkeypatch, rule_status, rule_version, current_version):
    cursor = FakeCursor()
    cursor.tables["sample_state"][0]["current_version"] = current_version
    for rule in cursor.tables["association_rules"]:
        rule["status"] = rule_status
        rule["sample_version"] = rule_version
    monkeypatch.setattr(reviews_route, "transaction", lambda: fake_transaction(cursor))
    return cursor


def test_publish_fails_and_invalidates_stale_approved_candidate(monkeypatch):
    cursor = _prepare(monkeypatch, RuleStatus.approved.value, rule_version=1, current_version=2)
    # Simulate a prior correction batch so the invalidation row can reference it.
    cursor.tables["import_batches"].append({
        "id": 1, "batch_no": "COR-PRIOR", "new_version": 2,
    })
    target = next(r for r in cursor.tables["association_rules"] if r["id"] == 1)
    with pytest.raises(HTTPException) as exc:
        reviews_route.publish(1, MANAGER)
    assert exc.value.status_code == 409 and "样本已被补传更正" in exc.value.detail
    assert target["status"] == RuleStatus.invalidated.value
    assert target["invalidate_reason"]
    assert any(iv["rule_id"] == 1 and iv["trigger_batch_id"] == 1
               for iv in cursor.tables["rule_invalidations"])


def test_publish_freezes_basis_when_sample_is_current(monkeypatch):
    cursor = _prepare(monkeypatch, RuleStatus.approved.value, rule_version=2, current_version=2)
    target = next(r for r in cursor.tables["association_rules"] if r["id"] == 1)
    target.update(support=0.25, confidence=0.6, lift=1.8, store_consistent=1,
                  source_run="run-v2-2-80-250")

    original_execute = cursor.execute

    def execute(sql, args=()):
        if "SELECT basket_count FROM analysis_runs" in " ".join(sql.split()):
            return cursor._set_result([{"basket_count": 2}])
        return original_execute(sql, args)

    cursor.execute = execute
    result = reviews_route.publish(1, MANAGER)
    assert result["status"] == "published"
    assert target["status"] == RuleStatus.published.value
    assert float(target["published_support"]) == 0.25
    assert target["published_basket_count"] == 2
