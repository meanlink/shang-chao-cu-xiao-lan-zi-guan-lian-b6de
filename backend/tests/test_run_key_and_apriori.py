from src.routes.rules import build_run_key, supporting_baskets
from src.services.association import apriori


def test_supporting_baskets_match_full_rule_families():
    rule = {"antecedent": ["咖啡与冲调", "乳品"], "consequent": ["早餐烘焙"]}
    baskets = [
        {"id": 1, "families": "咖啡与冲调 + 早餐烘焙 + 乳品"},
        {"id": 2, "families": "咖啡与冲调 + 乳品"},
        {"id": 3, "families": "早餐烘焙 + 零食坚果"},
    ]
    assert [b["id"] for b in supporting_baskets(rule, baskets)] == [1]


def test_run_key_is_stable_for_same_sample_and_params():
    a = build_run_key(3, 12, 0.08, 0.25)
    b = build_run_key(3, 12, 0.08, 0.25)
    assert a == b and a.startswith("run-v3-12-")


def test_run_key_changes_when_sample_or_params_change():
    base = build_run_key(3, 12, 0.08, 0.25)
    assert build_run_key(4, 12, 0.08, 0.25) != base
    assert build_run_key(3, 11, 0.08, 0.25) != base
    assert build_run_key(3, 12, 0.10, 0.25) != base


def test_apriori_deterministic_and_empty_safe():
    transactions = [
        {"咖啡与冲调", "乳品", "早餐烘焙"},
        {"咖啡与冲调", "乳品"},
        {"清洁纸品", "零食坚果"},
    ]
    first = apriori(transactions)
    second = apriori(transactions)
    assert first == second
    assert apriori([]) == []
