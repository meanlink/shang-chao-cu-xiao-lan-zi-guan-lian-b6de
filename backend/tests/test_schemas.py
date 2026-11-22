import pytest
from pydantic import ValidationError

from src.schemas.sample import TicketBatchIn, VoidBatchIn


def _ticket(**overrides):
    base = {
        "external_id": "POS-1120-0003",
        "store_code": "BJ-CY",
        "lines": [{"sku": "CF-001", "quantity": 2}],
    }
    base.update(overrides)
    return base


def test_batch_requires_at_least_one_ticket():
    with pytest.raises(ValidationError):
        TicketBatchIn(batch_no="COR-20261122-01", tickets=[])


def test_batch_no_pattern_enforced():
    with pytest.raises(ValidationError):
        TicketBatchIn(batch_no="bad batch no!", tickets=[_ticket()])


def test_line_quantity_must_be_positive_int():
    with pytest.raises(ValidationError):
        TicketBatchIn(batch_no="COR-20261122-01",
                      tickets=[_ticket(lines=[{"sku": "CF-001", "quantity": 0}])])


def test_void_requires_reason():
    with pytest.raises(ValidationError):
        VoidBatchIn(batch_no="VOID-20261122-01",
                    tickets=[{"external_id": "POS-1120-0003", "reason": ""}])


def test_void_payload_accepts_expected_version():
    payload = VoidBatchIn(batch_no="VOID-20261122-01", tickets=[
        {"external_id": "POS-1120-0003", "expected_version": 2, "reason": "收银撤销"}])
    assert payload.tickets[0].expected_version == 2
