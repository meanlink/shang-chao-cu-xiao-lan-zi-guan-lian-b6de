import pytest
from fastapi import HTTPException

from src.services import ticket_import
from src.schemas.sample import TicketBatchIn, VoidBatchIn, TicketIn, VoidTicketIn
from src.constants.enums import RuleStatus

from .fake_db import FakeCursor, fake_transaction

USER = {"id": 7}


def correction(batch_no, external_id, lines, *, expected_version=None, store="BJ-CY"):
    return TicketBatchIn(batch_no=batch_no, tickets=[TicketIn(
        external_id=external_id, store_code=store, expected_version=expected_version, lines=lines)])


def void_batch(batch_no, external_id, *, expected_version=None, reason="收银撤销"):
    return VoidBatchIn(batch_no=batch_no, tickets=[VoidTicketIn(
        external_id=external_id, expected_version=expected_version, reason=reason)])


@pytest.fixture()
def patched(monkeypatch):
    cursor = FakeCursor()
    monkeypatch.setattr(ticket_import, "transaction", lambda: fake_transaction(cursor))
    return cursor


def test_correction_replaces_full_ticket_and_bumps_version(patched):
    cursor = patched
    result = ticket_import.ingest_corrections(correction(
        "COR-01", "POS-3", [{"sku": "CF-001", "quantity": 2}, {"sku": "ML-001", "quantity": 1}]), USER)
    assert result["idempotent"] is False
    assert (result["base_version"], result["new_version"]) == (1, 2)
    order = next(o for o in cursor.tables["basket_orders"] if o["external_id"] == "POS-3")
    assert order["version"] == 2 and order["status"] == "imported"
    skus = {i["product_id"]: i["quantity"] for i in cursor.tables["basket_items"]
            if i["order_id"] == order["id"]}
    assert skus == {1: 2, 2: 1}
    # Every statement is retained append-only.
    revisions = [r for r in cursor.tables["basket_order_revisions"] if r["order_id"] == order["id"]]
    assert [r["op"] for r in revisions] == ["correct"]
    assert revisions[0]["revision_seq"] == 2


def test_live_candidates_are_invalidated_but_published_and_blocked_keep_basis(patched):
    cursor = patched
    ticket_import.ingest_corrections(correction(
        "COR-01", "POS-3", [{"sku": "CF-001", "quantity": 2}]), USER)
    statuses = {r["id"]: r["status"] for r in cursor.tables["association_rules"]}
    assert statuses[1] == RuleStatus.invalidated.value
    assert statuses[2] == RuleStatus.invalidated.value
    assert statuses[3] == RuleStatus.published.value
    assert statuses[4] == RuleStatus.blocked.value
    invalidations = cursor.tables["rule_invalidations"]
    assert {iv["rule_id"] for iv in invalidations} == {1, 2}
    assert all(iv["trigger_ticket"] == "POS-3" for iv in invalidations)
    assert cursor.tables["sample_state"][0]["current_version"] == 2


def test_batch_replay_is_idempotent(patched):
    cursor = patched
    payload = correction("COR-01", "POS-3", [{"sku": "CF-001", "quantity": 2}])
    first = ticket_import.ingest_corrections(payload, USER)
    second = ticket_import.ingest_corrections(payload, USER)
    assert first["idempotent"] is False and second["idempotent"] is True
    assert cursor.tables["sample_state"][0]["current_version"] == 2
    assert len(cursor.tables["import_batches"]) == 1
    order = next(o for o in cursor.tables["basket_orders"] if o["external_id"] == "POS-3")
    assert order["version"] == 2


def test_same_sample_rerun_would_share_run_key():
    # Mirrors the run-side idempotency: version change is what allows new candidates.
    from src.routes.rules import build_run_key
    key_before = build_run_key(1, 2, 0.08, 0.25)
    key_after = build_run_key(2, 2, 0.08, 0.25)
    assert key_before != key_after


def test_stale_correction_is_rejected_and_changes_nothing(patched):
    cursor = patched
    ticket_import.ingest_corrections(correction(
        "COR-01", "POS-3", [{"sku": "CF-001", "quantity": 2}]), USER)
    with pytest.raises(HTTPException) as exc:
        ticket_import.ingest_corrections(correction(
            "COR-02", "POS-3", [{"sku": "BK-002", "quantity": 9}], expected_version=1), USER)
    assert exc.value.status_code == 409 and "版本已过期" in exc.value.detail
    order = next(o for o in cursor.tables["basket_orders"] if o["external_id"] == "POS-3")
    # The stale statement never replaced the current full-ticket content.
    assert order["version"] == 2
    assert all(i["product_id"] != 3 for i in cursor.tables["basket_items"]
               if i["order_id"] == order["id"])
    assert cursor.tables["sample_state"][0]["current_version"] == 2


def test_void_removes_ticket_and_late_old_version_cannot_resurrect_it(patched):
    cursor = patched
    ticket_import.ingest_voids(void_batch("VOID-01", "POS-4", expected_version=1), USER)
    order = next(o for o in cursor.tables["basket_orders"] if o["external_id"] == "POS-4")
    assert order["status"] == "voided" and order["version"] == 2
    # A late correction (even claiming the pre-void version) cannot add the ticket back.
    with pytest.raises(HTTPException) as exc:
        ticket_import.ingest_corrections(correction(
            "COR-LATE", "POS-4", [{"sku": "ML-001", "quantity": 5}], expected_version=1), USER)
    assert exc.value.status_code == 409 and "撤销" in exc.value.detail
    order = next(o for o in cursor.tables["basket_orders"] if o["external_id"] == "POS-4")
    assert order["status"] == "voided"
    # A void with no expected_version is still rejected on an already-voided ticket.
    with pytest.raises(HTTPException) as exc2:
        ticket_import.ingest_voids(void_batch("VOID-02", "POS-4", expected_version=None), USER)
    assert "撤销" in exc2.value.detail


def test_correction_of_unknown_ticket_fails(patched):
    with pytest.raises(HTTPException) as exc:
        ticket_import.ingest_corrections(correction(
            "COR-X", "POS-NOPE", [{"sku": "CF-001", "quantity": 1}]), USER)
    assert exc.value.status_code == 404


def test_unknown_sku_rejected(patched):
    with pytest.raises(HTTPException) as exc:
        ticket_import.ingest_corrections(correction(
            "COR-X", "POS-3", [{"sku": "NO-SKU", "quantity": 1}]), USER)
    assert exc.value.status_code == 422
