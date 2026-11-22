"""HTTP-level smoke test with the whole FastAPI app on an in-memory database."""

import pytest
from fastapi.testclient import TestClient

from src import db as db_module
from src.security import issue_token
from src.services import ticket_import, promotion as promotion_service
from src.routes import rules as rules_route, reviews as reviews_route, orders as orders_route
from src import security as security_module

from .fake_db import FakeCursor, fake_transaction


@pytest.fixture()
def client(monkeypatch):
    cursor = FakeCursor()
    monkeypatch.setattr(ticket_import, "transaction", lambda: fake_transaction(cursor))
    monkeypatch.setattr(rules_route, "transaction", lambda: fake_transaction(cursor))
    monkeypatch.setattr(reviews_route, "transaction", lambda: fake_transaction(cursor))
    for module in (rules_route, orders_route, db_module, security_module):
        if hasattr(module, "fetch_one"):
            monkeypatch.setattr(module, "fetch_one",
                                lambda sql, args=(), _c=cursor: _fetch_one(_c, sql, args))
        if hasattr(module, "fetch_all"):
            monkeypatch.setattr(module, "fetch_all",
                                lambda sql, args=(), _c=cursor: _fetch_all(_c, sql, args))
    monkeypatch.setattr(promotion_service, "fetch_all",
                        lambda sql, args=(), _c=cursor: _fetch_all(_c, sql, args))

    # Avoid touching real MySQL/Redis in the FastAPI startup event.
    monkeypatch.setattr("src.main.init_db", lambda: None)
    monkeypatch.setattr("src.main.SEED_DEMO", False)

    from src.main import app
    with TestClient(app) as test_client:
        test_client.fake = cursor
        test_client.token = issue_token({"id": 7, "email": "ops@linli.local",
                                         "role": "analyst", "display_name": "林晨"})
        test_client.headers_v = {"Authorization": f"Bearer {test_client.token}"}
        yield test_client


def _fetch_one(cursor, sql, args=()):
    s = " ".join(sql.split())
    if s.startswith("SELECT id, email, display_name, role FROM users"):
        return {"id": 7, "email": "ops@linli.local", "display_name": "林晨", "role": "analyst"}
    if s.startswith("SELECT * FROM association_rules WHERE id"):
        return next((dict(r) for r in cursor.tables["association_rules"] if r["id"] == args[0]), None)
    if s.startswith("SELECT current_version, updated_at FROM sample_state"):
        return dict(cursor.tables["sample_state"][0])
    if s.startswith("SELECT 1"):
        return {"ok": 1}
    return None


def _fetch_all(cursor, sql, args=()):
    s = " ".join(sql.split())
    if s.startswith("SELECT r.*"):
        return [dict(r, created_by_name="林晨", reviewed_by_name=None,
                     sample_current_version=cursor.tables["sample_state"][0]["current_version"],
                     run_basket_count=2) for r in cursor.tables["association_rules"]]
    if s.startswith("SELECT external_id, store_code, basket_families"):
        return []
    if s.startswith("SELECT iv.*"):
        return [dict(iv) for iv in cursor.tables["rule_invalidations"]]
    if s.startswith("SELECT o.id, f.name"):
        return []
    if s.startswith("SELECT o.id,o.external_id"):
        return []
    if s.startswith("SELECT b.id,b.batch_no"):
        return []
    return []


def test_correction_endpoint_invalidates_live_candidates(client):
    payload = {
        "batch_no": "COR-HTTP-01",
        "source_file": "orders_correction.csv",
        "tickets": [{
            "external_id": "POS-3", "store_code": "BJ-CY",
            "lines": [{"sku": "CF-001", "quantity": 2}, {"sku": "ML-001", "quantity": 1}],
        }],
    }
    resp = client.post("/orders/corrections", json=payload, headers=client.headers_v)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["new_version"] == 2
    assert set(body["invalidated_rule_ids"]) == {1, 2}

    # Replaying the same batch number is idempotent.
    again = client.post("/orders/corrections", json=payload, headers=client.headers_v)
    assert again.status_code == 201 and again.json()["idempotent"] is True


def test_stale_correction_rejected_over_http(client):
    client.post("/orders/corrections", json={
        "batch_no": "COR-HTTP-01",
        "tickets": [{"external_id": "POS-3", "store_code": "BJ-CY",
                     "lines": [{"sku": "CF-001", "quantity": 2}]}],
    }, headers=client.headers_v)
    resp = client.post("/orders/corrections", json={
        "batch_no": "COR-HTTP-02",
        "tickets": [{"external_id": "POS-3", "store_code": "BJ-CY", "expected_version": 1,
                     "lines": [{"sku": "BK-002", "quantity": 9}]}],
    }, headers=client.headers_v)
    assert resp.status_code == 409 and "版本已过期" in resp.json()["detail"]


def test_void_then_late_correction_cannot_resurrect(client):
    void_resp = client.post("/orders/voids", json={
        "batch_no": "VOID-HTTP-01",
        "tickets": [{"external_id": "POS-4", "expected_version": 1, "reason": "收银撤销"}],
    }, headers=client.headers_v)
    assert void_resp.status_code == 201
    late = client.post("/orders/corrections", json={
        "batch_no": "COR-LATE-HTTP",
        "tickets": [{"external_id": "POS-4", "store_code": "BJ-CY", "expected_version": 1,
                     "lines": [{"sku": "ML-001", "quantity": 5}]}],
    }, headers=client.headers_v)
    assert late.status_code == 409 and "撤销" in late.json()["detail"]


def test_rules_list_exposes_sample_version_and_evidence_endpoint(client):
    listing = client.get("/rules", headers=client.headers_v)
    assert listing.status_code == 200
    assert all("sample_version" in row for row in listing.json())
    evidence = client.get("/rules/1/evidence", headers=client.headers_v)
    assert evidence.status_code == 200
    assert evidence.json()["rule"]["id"] == 1


def test_sample_version_endpoint(client):
    resp = client.get("/orders/sample-version", headers=client.headers_v)
    assert resp.status_code == 200 and resp.json()["current_version"] == 1
