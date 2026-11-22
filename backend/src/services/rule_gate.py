"""Pure business gates for rule review and publish concurrency."""

from ..constants.enums import RuleStatus

MIN_SUPPORT = 0.08


def publish_blockers(rule, current_sample_version):
    """Return all publish blockers. A stale sample version both blocks the
    publish and marks the live candidate as invalidated."""
    reasons = []
    if rule["status"] != RuleStatus.approved.value:
        reasons.append("需要先完成运营审核")
    if float(rule["support"]) < MIN_SUPPORT:
        reasons.append("组合支持度低于 8%")
    if not rule["store_consistent"]:
        reasons.append("跨门店支持度口径偏差超过 12 个百分点")
    if int(rule["sample_version"]) != int(current_sample_version):
        reasons.append("样本已被补传更正，候选依据已失效，请基于新样本重算")
    return reasons


def should_invalidate_on_gate(rule, current_sample_version):
    return (int(rule["sample_version"]) != int(current_sample_version)
            and rule["status"] in (
                RuleStatus.draft.value, RuleStatus.submitted.value, RuleStatus.approved.value))
