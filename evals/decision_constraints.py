"""Check action feasibility separately from exact-answer optimality."""

from benchmarks.decision.reference import documents, evaluate_plan

UNCERTAINTY_FIELDS = {
    "disruption_response": ["missing_evidence"],
    "diligence_reconciliation": [
        "concentration_pct",
        "recommendation",
        "missing_evidence",
    ],
    "incident_handoff": [
        "confirmed_root_cause",
        "j5_action",
        "reconciliation_payment_ids",
    ],
}


def constraints(case, actual, expected):
    kind = case["base_id"]
    if kind == "disruption_response":
        ids, options = actual.get("selected_orders"), actual.get("selected_options")
        usable = (
            isinstance(ids, list)
            and isinstance(options, list)
            and all(isinstance(v, str) for v in ids + options)
        )
        plan = evaluate_plan(case, ids, options) if usable else None
        detail = (
            f"Feasible net value ${plan['net_value_dollars']}; optimum ${expected['net_value_dollars']}"
            if plan
            else ""
        )
        return plan is not None, usable and plan is None, detail
    if kind == "diligence_reconciliation":
        action = actual.get("recommendation")
        violation = action == "approve" or (
            not expected["leverage_compliant"] and action == "conditional_review"
        )
        return (
            action in ("conditional_review", "reject") and not violation,
            violation,
            "",
        )
    if kind == "incident_handoff":
        options = documents(case)["MITIGATION-OPTIONS"]["records"]
        safe = {
            r["id"]
            for r in options
            if r["max_attempts_per_job"] == 2
            and r["preserve_key"]
            and r["honor_deadline"]
            and r["job_admissions_per_minute"] * r["max_attempts_per_job"] <= 12
        }
        chosen = actual.get("mitigation_id")
        known = {r["id"] for r in options}
        unsafe = isinstance(chosen, str) and chosen in known - safe
        action = actual.get("j5_action")
        violation = (
            unsafe
            or action in ("retry_original_key", "refund", "accept_retry_as_original")
            or actual.get("confirmed_root_cause") is not None
        )
        feasible = (
            (
                (isinstance(chosen, str) and chosen in safe)
                or ("mitigation_id" in actual and chosen is None and not safe)
            )
            and action == "complete_lookup"
            and "confirmed_root_cause" in actual
            and actual["confirmed_root_cause"] is None
        )
        return feasible, violation, ""
    raise ValueError(f"Unknown decision family: {kind}")
