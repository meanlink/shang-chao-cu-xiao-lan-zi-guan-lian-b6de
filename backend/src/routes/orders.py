from fastapi import APIRouter, Depends, HTTPException

from ..db import fetch_all, fetch_one
from ..security import current_user, require_roles
from ..schemas.sample import TicketBatchIn, VoidBatchIn
from ..services import ticket_import

router = APIRouter(prefix="/orders", tags=["orders"])
analyst = Depends(require_roles("analyst"))
reader = Depends(current_user)


@router.get("")
def orders(user=reader):
    return fetch_all(
        "SELECT o.id,o.external_id,o.order_date,o.total_amount,o.source_file,o.status,o.version,"
        "o.change_seq,s.code store_code,s.name store_name, COUNT(i.id) item_count, "
        "b.batch_no latest_batch_no, o.voided_at "
        "FROM basket_orders o JOIN stores s ON s.id=o.store_id "
        "LEFT JOIN basket_items i ON i.order_id=o.id "
        "LEFT JOIN import_batches b ON b.id=o.latest_batch_id "
        "GROUP BY o.id ORDER BY o.id DESC")


@router.get("/sample-version")
def sample_version(user=reader):
    return fetch_one("SELECT current_version, updated_at FROM sample_state WHERE id=1")


@router.get("/batches")
def batches(user=reader):
    return fetch_all(
        "SELECT b.id,b.batch_no,b.source_file,b.op,b.ticket_count,b.base_version,b.new_version,"
        "b.note,b.created_at,u.display_name created_by_name "
        "FROM import_batches b JOIN users u ON u.id=b.created_by ORDER BY b.id DESC")


@router.get("/tickets/{external_id}")
def ticket_history(external_id: str, user=reader):
    order = fetch_one("SELECT * FROM basket_orders WHERE external_id=%s", (external_id,))
    if not order:
        raise HTTPException(404, "小票不存在")
    revisions = fetch_all(
        "SELECT r.id,r.revision_seq,r.op,r.snapshot_json,r.created_at,b.batch_no,b.source_file,"
        "u.display_name created_by_name "
        "FROM basket_order_revisions r JOIN import_batches b ON b.id=r.batch_id "
        "JOIN users u ON u.id=r.created_by "
        "WHERE r.order_id=%s ORDER BY r.revision_seq", (order["id"],))
    return {"order": order, "revisions": revisions}


@router.post("/corrections", status_code=201)
def corrections(data: TicketBatchIn, user=analyst):
    """Full-ticket corrections. A stale expected_version is rejected, so a late
    old statement can never overwrite a newer correction or resurrect a void."""
    return ticket_import.ingest_corrections(data, user)


@router.post("/voids", status_code=201)
def voids(data: VoidBatchIn, user=analyst):
    """Whole-ticket revocations. Voided tickets leave the recalculation sample."""
    return ticket_import.ingest_voids(data, user)


@router.post("/import")
def import_orders(data: TicketBatchIn, user=analyst):
    """Back-compat entry: a batch of full tickets is ingested as create statements."""
    for ticket in data.tickets:
        if not ticket.store_code:
            raise HTTPException(422, f"小票 {ticket.external_id} 缺少门店编码")
    return ticket_import.ingest_create(data, user)
