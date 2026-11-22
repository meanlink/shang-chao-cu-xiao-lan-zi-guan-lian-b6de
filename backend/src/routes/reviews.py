from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from ..db import transaction
from ..security import current_user, require_roles
from ..services.rule_gate import publish_blockers, should_invalidate_on_gate
router = APIRouter(prefix="/reviews", tags=["reviews"])


class ReviewIn(BaseModel):
    decision: str
    comment: str = ""


@router.post("/{rule_id}")
def review(rule_id: int, data: ReviewIn, user=Depends(require_roles("manager"))):
    if data.decision not in ("approve", "reject"):
        raise HTTPException(400, "decision 必须为 approve 或 reject")
    with transaction() as cur:
        cur.execute("SELECT current_version FROM sample_state WHERE id=1 FOR UPDATE")
        sample_version = int(cur.fetchone()["current_version"])
        cur.execute("SELECT * FROM association_rules WHERE id=%s FOR UPDATE", (rule_id,))
        rule = cur.fetchone()
        if not rule:
            raise HTTPException(404, "规则不存在")
        if rule["status"] != "submitted":
            raise HTTPException(409, "只有待审核规则可审核")
        if data.decision == "approve" and int(rule["sample_version"]) != sample_version:
            reason = "审核期间样本已被补传更正，候选依据失效"
            cur.execute(
                "UPDATE association_rules SET status='invalidated', invalidated_at=UTC_TIMESTAMP(), "
                "invalidate_reason=%s WHERE id=%s", (reason, rule_id))
            cur.execute(
                "SELECT id, batch_no FROM import_batches WHERE new_version=%s ORDER BY id DESC LIMIT 1",
                (sample_version,))
            batch = cur.fetchone()
            if batch:
                cur.execute(
                    "INSERT INTO rule_invalidations(rule_id, sample_version_before, sample_version_after,"
                    " trigger_batch_id, trigger_ticket, reason, invalidated_by) "
                    "VALUES(%s,%s,%s,%s,%s,%s,%s)",
                    (rule_id, rule["sample_version"], sample_version, batch["id"],
                     batch["batch_no"], reason, user["id"]))
            raise HTTPException(409, "审核期间样本已变更，该候选已失效，请基于新样本重算")
        status = "approved" if data.decision == "approve" else "rejected"
        cur.execute(
            "UPDATE association_rules SET status=%s,reviewed_by=%s,review_comment=%s WHERE id=%s",
            (status, user["id"], data.comment, rule_id))
        cur.execute(
            "INSERT INTO audit_logs(actor_id,action,entity_type,entity_id,detail) "
            "VALUES(%s,%s,%s,%s,%s)",
            (user["id"], "review_rule", "association_rule", rule_id, data.decision))
    return {"status": status}


@router.post("/{rule_id}/publish")
def publish(rule_id: int, user=Depends(require_roles("manager"))):
    with transaction() as cur:
        # The sample row is the concurrency anchor: a correction batch and a
        # publish cannot both commit against the same sample version.
        cur.execute("SELECT current_version FROM sample_state WHERE id=1 FOR UPDATE")
        sample_version = int(cur.fetchone()["current_version"])
        cur.execute("SELECT * FROM association_rules WHERE id=%s FOR UPDATE", (rule_id,))
        rule = cur.fetchone()
        if not rule:
            raise HTTPException(404, "规则不存在")
        reasons = publish_blockers(rule, sample_version)
        if reasons:
            if should_invalidate_on_gate(rule, sample_version):
                reason = "发布时发现样本已被补传更正，候选依据失效"
                cur.execute(
                    "UPDATE association_rules SET status='invalidated', invalidated_at=UTC_TIMESTAMP(), "
                    "invalidate_reason=%s WHERE id=%s", (reason, rule_id))
                cur.execute(
                    "SELECT id, batch_no FROM import_batches WHERE new_version=%s ORDER BY id DESC LIMIT 1",
                    (sample_version,))
                batch = cur.fetchone()
                if batch:
                    cur.execute(
                        "INSERT INTO rule_invalidations(rule_id, sample_version_before, sample_version_after,"
                        " trigger_batch_id, trigger_ticket, reason, invalidated_by) "
                        "VALUES(%s,%s,%s,%s,%s,%s,%s)",
                        (rule_id, rule["sample_version"], sample_version, batch["id"],
                         batch["batch_no"], reason, user["id"]))
            else:
                cur.execute(
                    "UPDATE association_rules SET status='blocked',review_comment=%s WHERE id=%s",
                    ("；".join(reasons), rule_id))
            raise HTTPException(409, "；".join(reasons))
        cur.execute("SELECT basket_count FROM analysis_runs WHERE run_key=%s", (rule["source_run"],))
        run_row = cur.fetchone()
        basket_count = run_row["basket_count"] if run_row else None
        # Freeze the publish-time basis; later corrections never mutate it.
        cur.execute(
            "UPDATE association_rules SET status='published',published_at=UTC_TIMESTAMP(),"
            "published_support=support,published_confidence=confidence,published_lift=lift,"
            "published_basket_count=%s WHERE id=%s",
            (basket_count, rule_id))
        cur.execute(
            "INSERT INTO audit_logs(actor_id,action,entity_type,entity_id,detail) "
            "VALUES(%s,%s,%s,%s,%s)",
            (user["id"], "publish_rule", "association_rule", rule_id,
             f"样本 v{sample_version} 冻结发布依据，{basket_count} 个购物篮"))
    return {"status": "published", "message": "已进入门店执行队列，发布时依据已冻结"}
