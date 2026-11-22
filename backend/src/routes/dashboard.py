from fastapi import APIRouter, Depends
from ..db import fetch_one, fetch_all
from ..security import current_user
router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/summary")
def summary(user=Depends(current_user)):
    return {
        "orders": fetch_one(
            "SELECT COUNT(*) c FROM basket_orders WHERE status='imported'")["c"],
        "voided_orders": fetch_one(
            "SELECT COUNT(*) c FROM basket_orders WHERE status='voided'")["c"],
        "sample_version": fetch_one(
            "SELECT current_version c FROM sample_state WHERE id=1")["c"],
        "families": fetch_one("SELECT COUNT(*) c FROM product_families")["c"],
        "rules": fetch_one("SELECT COUNT(*) c FROM association_rules")["c"],
        "published": fetch_one(
            "SELECT COUNT(*) c FROM association_rules WHERE status='published'")["c"],
        "blocked": fetch_one(
            "SELECT COUNT(*) c FROM association_rules WHERE status='blocked'")["c"],
        "invalidated": fetch_one(
            "SELECT COUNT(*) c FROM association_rules WHERE status='invalidated'")["c"],
        "recent": fetch_all(
            "SELECT antecedent,consequent,support,confidence,lift,status,sample_version "
            "FROM association_rules ORDER BY id DESC LIMIT 5"),
    }
