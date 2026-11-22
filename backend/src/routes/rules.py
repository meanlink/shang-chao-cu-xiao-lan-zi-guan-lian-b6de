from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from ..db import fetch_all, fetch_one, execute, transaction
from ..security import current_user, require_roles
from ..services.association import apriori
router = APIRouter(prefix="/rules", tags=["rules"])


class RunIn(BaseModel):
    min_support: float = Field(0.08, ge=0.01, le=1)
    min_confidence: float = Field(0.25, ge=0.01, le=1)


def build_run_key(sample_version, basket_count, min_support, min_confidence):
    """Same parameters on the same sample version produce an identical key,
    so re-runs never generate duplicate candidate rows."""
    return (f"run-v{sample_version}-{basket_count}-"
            f"{int(min_support * 1000)}-{int(min_confidence * 1000)}")


def supporting_baskets(rule_item, baskets):
    """Baskets containing every family of the rule (the support set)."""
    needed = set(rule_item["antecedent"]) | set(rule_item["consequent"])
    result = []
    for basket in baskets:
        families = {name.strip() for name in basket["families"].split("+")}
        if needed.issubset(families):
            result.append(basket)
    return result


def active_transactions(cur=None):
    """Family-set baskets built only from fully imported, non-voided tickets."""
    sql = (
        "SELECT o.id, f.name FROM basket_orders o "
        "JOIN basket_items i ON i.order_id=o.id "
        "JOIN products p ON p.id=i.product_id "
        "JOIN product_families f ON f.id=p.family_id "
        "WHERE o.status='imported' ORDER BY o.id")
    if cur is not None:
        cur.execute(sql)
        rows = cur.fetchall()
    else:
        rows = fetch_all(sql)
    grouped = {}
    for r in rows:
        grouped.setdefault(r["id"], set()).add(r["name"])
    return list(grouped.values())


def _active_baskets(cur):
    cur.execute(
        "SELECT o.id, o.external_id, s.code store_code, GROUP_CONCAT(f.name ORDER BY f.name SEPARATOR ' + ') families "
        "FROM basket_orders o JOIN stores s ON s.id=o.store_id "
        "JOIN basket_items i ON i.order_id=o.id JOIN products p ON p.id=i.product_id "
        "JOIN product_families f ON f.id=p.family_id "
        "WHERE o.status='imported' GROUP BY o.id, o.external_id, s.code ORDER BY o.id")
    return cur.fetchall()


@router.get("")
def rules(user=Depends(current_user)):
    return fetch_all(
        "SELECT r.*, u.display_name created_by_name, m.display_name reviewed_by_name, "
        "v.current_version AS sample_current_version, ar.basket_count AS run_basket_count "
        "FROM association_rules r JOIN users u ON u.id=r.created_by "
        "LEFT JOIN users m ON m.id=r.reviewed_by "
        "LEFT JOIN analysis_runs ar ON ar.run_key=r.source_run "
        "CROSS JOIN sample_state v ORDER BY r.id DESC")


@router.post("/run")
def run(data: RunIn, user=Depends(require_roles("analyst"))):
    try:
        with transaction() as cur:
            cur.execute("SELECT current_version FROM sample_state WHERE id=1 FOR UPDATE")
            sample_version = int(cur.fetchone()["current_version"])
            transactions = active_transactions(cur)
            results = apriori(transactions, data.min_support, data.min_confidence)
            # Same parameters AND same sample version -> same run key, so a
            # re-run on an unchanged sample never produces duplicate candidates.
            run_key = build_run_key(sample_version, len(transactions),
                                    data.min_support, data.min_confidence)
            cur.execute("SELECT id FROM analysis_runs WHERE run_key=%s", (run_key,))
            existing = cur.fetchone()
            if existing:
                return {"run_key": run_key, "basket_count": len(transactions),
                        "rule_count": len(results), "sample_version": sample_version,
                        "idempotent": True, "rules": results[:12]}
            cur.execute(
                "INSERT INTO analysis_runs(run_key,min_support,min_confidence,basket_count,rule_count,"
                "status,created_by,sample_version) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
                (run_key, data.min_support, data.min_confidence, len(transactions), len(results),
                 "completed", user["id"], sample_version))
            run_id = cur.lastrowid
            baskets = _active_baskets(cur)
            for item in results[:12]:
                antecedent = " + ".join(item["antecedent"])
                consequent = " + ".join(item["consequent"])
                consistent = int(item["support"] >= 0.08)
                status = "draft" if consistent else "blocked"
                cur.execute(
                    "INSERT INTO association_rules(antecedent,consequent,support,confidence,lift,"
                    "store_scope,store_consistent,status,source_run,created_by,sample_version) "
                    "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (antecedent, consequent, item["support"], item["confidence"], item["lift"],
                     "全门店", consistent, status, run_key, user["id"], sample_version))
                rule_id = cur.lastrowid
                # Pin exactly the baskets containing every family of the rule,
                # i.e. the supporting basket set. Published rules keep this basis
                # even after later corrections change the sample.
                for basket in supporting_baskets(item, baskets):
                    cur.execute(
                        "INSERT INTO rule_basket_evidence(rule_id,analysis_run_id,sample_version,"
                        "order_id,external_id,store_code,basket_families,included) "
                        "VALUES(%s,%s,%s,%s,%s,%s,%s,1)",
                        (rule_id, run_id, sample_version, basket["id"], basket["external_id"],
                         basket["store_code"], basket["families"]))
            cur.execute(
                "INSERT INTO audit_logs(actor_id,action,entity_type,entity_id,detail) "
                "VALUES(%s,%s,%s,%s,%s)",
                (user["id"], "run_apriori", "analysis_runs", run_id,
                 f"样本 v{sample_version}，{len(transactions)} 个完整入篮购物篮"))
            return {"run_key": run_key, "basket_count": len(transactions),
                    "rule_count": len(results), "sample_version": sample_version,
                    "idempotent": False, "rules": results[:12]}
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover - defensive surface
        raise HTTPException(500, f"重算失败：{exc}") from exc


@router.post("/{rule_id}/submit")
def submit(rule_id: int, user=Depends(require_roles("analyst"))):
    rule = fetch_one("SELECT * FROM association_rules WHERE id=%s", (rule_id,))
    if not rule:
        raise HTTPException(404, "规则不存在")
    if rule["status"] != "draft":
        raise HTTPException(409, "只有草稿规则可提交审核")
    if float(rule["support"]) < 0.08 or not rule["store_consistent"]:
        raise HTTPException(409, "规则未达到发布前置条件，不能提交审核")
    execute("UPDATE association_rules SET status='submitted' WHERE id=%s", (rule_id,))
    execute(
        "INSERT INTO audit_logs(actor_id,action,entity_type,entity_id,detail) "
        "VALUES(%s,%s,%s,%s,%s)",
        (user["id"], "submit_rule", "association_rule", rule_id, "submitted"))
    return {"status": "submitted"}


@router.get("/{rule_id}/evidence")
def rule_evidence(rule_id: int, user=Depends(current_user)):
    """Which baskets the review/publish was based on, plus the correction
    ticket that later invalidated the candidate (if any)."""
    rule = fetch_one("SELECT * FROM association_rules WHERE id=%s", (rule_id,))
    if not rule:
        raise HTTPException(404, "规则不存在")
    evidence = fetch_all(
        "SELECT external_id, store_code, basket_families, sample_version, included "
        "FROM rule_basket_evidence WHERE rule_id=%s ORDER BY store_code, external_id",
        (rule_id,))
    invalidations = fetch_all(
        "SELECT iv.*, b.batch_no, b.source_file, b.op AS trigger_op, u.display_name invalidated_by_name "
        "FROM rule_invalidations iv JOIN import_batches b ON b.id=iv.trigger_batch_id "
        "JOIN users u ON u.id=iv.invalidated_by WHERE iv.rule_id=%s ORDER BY iv.id",
        (rule_id,))
    return {
        "rule": rule,
        "sample_version": rule["sample_version"],
        "evidence_basket_count": len(evidence),
        "evidence": evidence,
        "invalidations": invalidations,
        "published_basis": {
            "support": rule["published_support"],
            "confidence": rule["published_confidence"],
            "lift": rule["published_lift"],
            "basket_count": rule["published_basket_count"],
        } if rule["published_support"] is not None else None,
    }
