"""Versioned POS ticket ingestion.

Orders are never overwritten silently. Each statement (create / correct /
void) is recorded as an append-only revision, the basket order row carries a
monotonic ``version`` and the whole sample carries a global
``sample_state.current_version``. Late statements carrying an outdated
``expected_version`` are rejected, so an old correction can never resurrect a
ticket that a newer revocation already removed.

Every accepted batch bumps the sample version and invalidates still-live
candidate rules (draft / submitted / approved) derived from the older sample.
Published rules keep their frozen publish-time basis and are never touched.
"""

import json
from datetime import date, datetime

from fastapi import HTTPException
from pymysql.err import IntegrityError

from ..db import transaction
from ..constants.enums import RuleStatus

LIVE_CANDIDATE_STATUSES = (
    RuleStatus.draft.value, RuleStatus.submitted.value, RuleStatus.approved.value,
)


def check_version_conflict(order, expected_version):
    """Optimistic concurrency rule shared by corrections and revocations.

    A late statement carrying a stale ``expected_version`` is rejected, and an
    already-voided ticket can never be brought back by any late statement.
    """
    if order["status"] == "voided":
        return "小票已撤销，晚到的旧版本不能恢复该票"
    if expected_version is not None and int(expected_version) != int(order["version"]):
        return (f"小票的版本已过期：客户端依据 v{expected_version}，"
                f"当前为 v{order['version']}，请基于最新整票重传")
    return None


def select_rules_to_invalidate(rules, base_version):
    """Only live candidates pinned to the pre-change sample go stale.

    Published rules keep their frozen basis; blocked/rejected rules are
    terminal and are never resurrected or rewritten by later batches.
    """
    return [r for r in rules
            if r["status"] in LIVE_CANDIDATE_STATUSES and int(r["sample_version"]) == base_version]


def build_invalidation_reason(batch_id, ticket_refs):
    shown = "、".join(ticket_refs[:5])
    return f"样本已由补传批次 #{batch_id} 变更（{shown}），旧样本候选失效"[:240]


def _one(cur, sql, args=()):
    cur.execute(sql, args)
    return cur.fetchone()


def _lock_sample(cur):
    row = _one(cur, "SELECT current_version FROM sample_state WHERE id=1 FOR UPDATE")
    return int(row["current_version"])


def _catalog(cur):
    cur.execute("SELECT id, sku, price FROM products WHERE status='active'")
    products = {r["sku"]: r for r in cur.fetchall()}
    cur.execute("SELECT id, code FROM stores WHERE active=1")
    stores = {r["code"]: r for r in cur.fetchall()}
    return products, stores


def _validate_lines(lines, products):
    if not lines:
        raise HTTPException(422, "整票更正必须携带完整明细，空票请走撤销接口")
    seen = set()
    total = 0.0
    normalized = []
    for line in lines:
        if line.sku not in products:
            raise HTTPException(422, f"SKU {line.sku} 不在在售商品目录中")
        if line.sku in seen:
            raise HTTPException(422, f"票内 SKU {line.sku} 重复，请合并件数后整票重传")
        seen.add(line.sku)
        unit_price = float(line.unit_price) if line.unit_price is not None else float(products[line.sku]["price"])
        total += unit_price * line.quantity
        normalized.append((products[line.sku]["id"], line.sku, line.quantity, unit_price))
    return normalized, round(total, 2)


def _parse_date(raw):
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date() if raw else date.today()
    except ValueError as exc:
        raise HTTPException(422, "order_date 必须是 YYYY-MM-DD") from exc


def _snapshot(order, lines):
    return {
        "external_id": order["external_id"],
        "store_id": order["store_id"],
        "order_date": str(order["order_date"]),
        "total_amount": float(order["total_amount"]),
        "lines": [{"product_id": pid, "sku": sku, "quantity": qty, "unit_price": price}
                  for pid, sku, qty, price in lines],
    }


def _invalidate_live_rules(cur, base_version, batch_id, ticket_refs, user_id):
    """Mark live candidates whose sample changed. Blocked/rejected/published
    rules are terminal and keep their original basis untouched."""
    cur.execute(
        "SELECT id, antecedent, consequent, sample_version, status FROM association_rules "
        "WHERE status IN %s FOR UPDATE", (LIVE_CANDIDATE_STATUSES,))
    rules = cur.fetchall()
    affected = []
    reason = build_invalidation_reason(batch_id, ticket_refs)
    for rule in select_rules_to_invalidate(rules, base_version):
        cur.execute(
            "UPDATE association_rules SET status=%s, invalidated_at=UTC_TIMESTAMP(), "
            "invalidate_reason=%s WHERE id=%s",
            (RuleStatus.invalidated.value, reason, rule["id"]))
        cur.execute(
            "INSERT INTO rule_invalidations(rule_id, sample_version_before, sample_version_after, "
            "trigger_batch_id, trigger_ticket, reason, invalidated_by) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s)",
            (rule["id"], base_version, base_version + 1, batch_id,
             "、".join(ticket_refs)[:60], reason, user_id))
        affected.append(rule["id"])
    return affected


def _apply_ticket(cur, ticket, op, products, stores, base_version, batch_id, user_id,
                  reason=None, source_file=""):
    order = _one(cur, "SELECT * FROM basket_orders WHERE external_id=%s FOR UPDATE", (ticket.external_id,))
    if op == "create":
        if order:
            raise HTTPException(409, f"小票 {ticket.external_id} 已存在（v{order['version']}），补传请走整票更正")
        if not ticket.store_code or ticket.store_code not in stores:
            raise HTTPException(422, f"小票 {ticket.external_id} 缺少有效门店编码")
        lines, total = _validate_lines(ticket.lines, products)
        order_date = _parse_date(ticket.order_date)
        cur.execute(
            "INSERT INTO basket_orders(external_id,store_id,order_date,total_amount,source_file,status,"
            "version,change_seq,latest_batch_id) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (ticket.external_id, stores[ticket.store_code]["id"], order_date, total,
             source_file, "imported", 1, 1, batch_id))
        order_id = cur.lastrowid
        for product_id, _sku, qty, price in lines:
            cur.execute(
                "INSERT INTO basket_items(order_id,product_id,quantity,unit_price) VALUES(%s,%s,%s,%s)",
                (order_id, product_id, qty, price))
        cur.execute(
            "INSERT INTO basket_order_revisions(order_id,revision_seq,op,batch_id,external_id,store_id,"
            "order_date,total_amount,snapshot_json,created_by) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (order_id, 1, "create", batch_id, ticket.external_id,
             stores[ticket.store_code]["id"], order_date, total,
             json.dumps({"lines": [{"sku": s, "quantity": q, "unit_price": p} for _, s, q, p in lines]},
                        ensure_ascii=False), user_id))
        return order_id

    if not order:
        raise HTTPException(404, f"小票 {ticket.external_id} 不存在，无法更正或撤销")
    conflict = check_version_conflict(order, ticket.expected_version)
    if conflict:
        raise HTTPException(409, f"小票 {ticket.external_id} {conflict}")
    if op == "void":
        next_seq = int(order["change_seq"]) + 1
        cur.execute(
            "UPDATE basket_orders SET status='voided', version=version+1, change_seq=%s, "
            "latest_batch_id=%s, voided_at=UTC_TIMESTAMP() WHERE id=%s",
            (next_seq, batch_id, order["id"]))
        cur.execute(
            "INSERT INTO basket_order_revisions(order_id,revision_seq,op,batch_id,external_id,store_id,"
            "order_date,total_amount,snapshot_json,created_by) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (order["id"], next_seq, "void", batch_id, ticket.external_id, order["store_id"],
             order["order_date"], order["total_amount"], json.dumps({"reason": reason}, ensure_ascii=False),
             user_id))
        return order["id"]

    # correct: full-ticket replacement under the same external id.
    if not ticket.store_code or ticket.store_code not in stores:
        raise HTTPException(422, f"小票 {ticket.external_id} 缺少有效门店编码")
    lines, total = _validate_lines(ticket.lines, products)
    order_date = _parse_date(ticket.order_date)
    next_seq = int(order["change_seq"]) + 1
    cur.execute(
        "UPDATE basket_orders SET store_id=%s,order_date=%s,total_amount=%s,status='imported',"
        "version=version+1,change_seq=%s,latest_batch_id=%s,voided_at=NULL WHERE id=%s",
        (stores[ticket.store_code]["id"], order_date, total, next_seq, batch_id, order["id"]))
    cur.execute("DELETE FROM basket_items WHERE order_id=%s", (order["id"],))
    for product_id, _sku, qty, price in lines:
        cur.execute(
            "INSERT INTO basket_items(order_id,product_id,quantity,unit_price) VALUES(%s,%s,%s,%s)",
            (order["id"], product_id, qty, price))
    snapshot = _snapshot(
        {"external_id": ticket.external_id, "store_id": stores[ticket.store_code]["id"],
         "order_date": order_date, "total_amount": total}, lines)
    cur.execute(
        "INSERT INTO basket_order_revisions(order_id,revision_seq,op,batch_id,external_id,store_id,"
        "order_date,total_amount,snapshot_json,created_by) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (order["id"], next_seq, "correct", batch_id, ticket.external_id,
         stores[ticket.store_code]["id"], order_date, total,
         json.dumps(snapshot, ensure_ascii=False), user_id))
    return order["id"]


def _ingest(data, op, user, reason_by_ticket=None):
    try:
        with transaction() as cur:
            base_version = _lock_sample(cur)
            # Idempotent replay of the whole batch.
            existing = _one(cur, "SELECT id, new_version FROM import_batches WHERE batch_no=%s",
                            (data.batch_no,))
            if existing:
                return {"batch_no": data.batch_no, "idempotent": True,
                        "new_version": existing["new_version"], "invalidated_rule_ids": []}
            products, stores = _catalog(cur)
            new_version = base_version + 1
            cur.execute(
                "INSERT INTO import_batches(batch_no,source_file,op,ticket_count,base_version,"
                "new_version,created_by,note) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
                (data.batch_no, data.source_file, op, len(data.tickets), base_version,
                 new_version, user["id"], data.note))
            batch_id = cur.lastrowid
            ticket_refs, order_ids = [], []
            for ticket in data.tickets:
                ticket_refs.append(ticket.external_id)
                reason = reason_by_ticket.get(ticket.external_id) if reason_by_ticket else None
                order_ids.append(_apply_ticket(
                    cur, ticket, op, products, stores, base_version, batch_id, user["id"],
                    reason=reason, source_file=data.source_file))
            invalidated = _invalidate_live_rules(
                cur, base_version, batch_id, ticket_refs, user["id"])
            cur.execute("UPDATE sample_state SET current_version=%s, updated_at=UTC_TIMESTAMP() WHERE id=1",
                        (new_version,))
            cur.execute(
                "INSERT INTO audit_logs(actor_id,action,entity_type,entity_id,detail) "
                "VALUES(%s,%s,%s,%s,%s)",
                (user["id"], f"import_{op}", "import_batch", batch_id,
                 f"{data.batch_no} {op} {len(data.tickets)} 票，样本 v{base_version}→v{new_version}"))
            return {"batch_no": data.batch_no, "batch_id": batch_id, "op": op,
                    "base_version": base_version, "new_version": new_version,
                    "ticket_count": len(data.tickets), "invalidated_rule_ids": invalidated,
                    "idempotent": False}
    except IntegrityError as exc:
        if exc.args[0] == 1062:
            raise HTTPException(409, "批次号或票内明细冲突，请检查后重试") from exc
        raise


def ingest_create(data, user):
    return _ingest(data, "create", user)


def ingest_corrections(data, user):
    return _ingest(data, "correct", user)


def ingest_voids(data, user):
    reasons = {t.external_id: t.reason for t in data.tickets}
    return _ingest(data, "void", user, reason_by_ticket=reasons)
