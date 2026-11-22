from src.services.rule_gate import publish_blockers, should_invalidate_on_gate


def _rule(status="approved", support=0.25, consistent=1, sample_version=3):
    return {
        "status": status,
        "support": support,
        "store_consistent": consistent,
        "sample_version": sample_version,
    }


def test_fresh_qualified_rule_has_no_blockers():
    assert publish_blockers(_rule(), current_sample_version=3) == []


def test_stale_sample_blocks_publish():
    blockers = publish_blockers(_rule(sample_version=2), current_sample_version=3)
    assert any("样本已被补传更正" in b for b in blockers)


def test_statistical_gates_still_apply():
    assert any("支持度" in b for b in publish_blockers(_rule(support=0.04), 3))
    assert any("门店" in b for b in publish_blockers(_rule(consistent=0), 3))
    assert any("审核" in b for b in publish_blockers(_rule(status="submitted"), 3))


def test_published_rule_is_not_re_invalidated():
    assert should_invalidate_on_gate(_rule(status="published", sample_version=1), 3) is False
    assert should_invalidate_on_gate(_rule(status="approved", sample_version=1), 3) is True
    assert should_invalidate_on_gate(_rule(status="approved", sample_version=3), 3) is False
