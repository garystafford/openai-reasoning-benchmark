"""Deterministic calculations for the decision pilot; no model answers or API calls."""

import copy
import itertools
import json
from fractions import Fraction
from pathlib import Path
from functools import lru_cache

DIRECTORY = Path(__file__).parent


def base_cases():
    return json.loads((DIRECTORY / "cases.json").read_text())["cases"]


def cases():
    return [{**c, "base_id": c["id"], "variant_kind": "base"} for c in base_cases()]


def documents(case):
    return {d["id"]: d for d in case["documents"]}


def fulfillment_inputs(case):
    docs = documents(case)
    orders = {r["id"]: r for r in docs["ORDER-REGISTER"]["records"]}
    options = {r["id"]: dict(r) for r in docs["SUPPLY-OPTIONS"]["records"]}
    # Signed final inventory, active hold/reservation, and confirmed receipt amendment.
    opening = {"A": {"X": 14 - 4, "Y": 9}, "B": {"X": 10, "Y": 12 - 3}}
    options["S1"]["day"] = 3
    return orders, options, opening


def evaluate_plan(case, selected_orders, selected_options):
    orders, options, opening = fulfillment_inputs(case)
    if len(set(selected_orders)) != len(selected_orders) or len(
        set(selected_options)
    ) != len(selected_options):
        return None
    if (
        not set(selected_orders) <= orders.keys()
        or not set(selected_options) <= options.keys()
    ):
        return None
    if not {"O1"} <= set(selected_orders) or any(
        orders[i]["status"] != "open" for i in selected_orders
    ):
        return None
    if {"S1", "S2"} <= set(selected_options):
        return None
    cost = sum(options[i]["cost"] for i in selected_options)
    if cost > 260:
        return None
    stock = {s: dict(v) for s, v in opening.items()}
    for day in range(1, 5):
        for id in selected_options:
            item = options[id]
            if item["day"] != day:
                continue
            for sku in ("X", "Y"):
                stock[item["site"]][sku] += item[sku]
                if item["type"] == "transfer_from_A":
                    stock["A"][sku] -= item[sku]
        if any(v < 0 for values in stock.values() for v in values.values()):
            return None
        for id in selected_orders:
            item = orders[id]
            if item["day"] == day:
                for sku in ("X", "Y"):
                    stock[item["site"]][sku] -= item[sku]
        if any(v < 0 for values in stock.values() for v in values.values()):
            return None
    contribution = sum(orders[i]["contribution"] for i in selected_orders)
    return dict(
        contribution_dollars=contribution,
        option_cost_dollars=cost,
        net_value_dollars=contribution - cost,
        ending_available=stock,
    )


def solve_disruption(case):
    orders, options, _ = fulfillment_inputs(case)
    optional = sorted(
        i for i, r in orders.items() if r["status"] == "open" and not r["mandatory"]
    )
    best = None
    for supply_bits in itertools.product((False, True), repeat=len(options)):
        supply = sorted(i for i, b in zip(options, supply_bits) if b)
        if {"S1", "S2"} <= set(supply) or sum(options[i]["cost"] for i in supply) > 260:
            continue
        for order_bits in itertools.product((False, True), repeat=len(optional)):
            selected = sorted(["O1"] + [i for i, b in zip(optional, order_bits) if b])
            result = evaluate_plan(case, selected, supply)
            if result is None:
                continue
            rank = (
                -result["net_value_dollars"],
                result["option_cost_dollars"],
                selected,
                supply,
            )
            if best is None or rank < best[0]:
                best = (rank, result)
    rank, result = best
    return dict(
        selected_orders=rank[2],
        selected_options=rank[3],
        **result,
        unfilled_optional_orders=sorted(set(optional) - set(rank[2])),
        evidence_ids=sorted(
            [
                "INV-FINAL",
                "HOLD-7",
                "RES-3",
                "ORDER-REGISTER",
                "SUPPLY-OPTIONS",
                "SUPPLIER-NOTICE",
                "EXECUTION-RULES",
            ]
        ),
        missing_evidence=[]
    )


def solve_diligence(case):
    docs = documents(case)
    base = (
        sum(
            r["domestic_revenue"] + r["export_revenue"] - r["operating_cost"]
            for r in docs["AUDIT-SCHEDULE"]["records"]
        )
        * 1000
    )
    applied = []
    for r in docs["CLOSE-REGISTER"]["records"]:
        if (
            r["signed"]
            and r["entity"] == "continuing"
            and r["effective"].startswith("2025-")
        ):
            base += r["delta"] * 1000 * (1 if r["account"] == "revenue" else -1)
            applied.append(r["id"])
    uncapped = base + 200000
    adjusted = min(Fraction(uncapped + 4300000), Fraction(uncapped) * 5 / 4)
    assert adjusted.denominator == 1
    adjusted = int(adjusted)
    allowed = adjusted - uncapped
    assert allowed == min(4300000, Fraction(adjusted, 5))
    gross = 32000000 + 14000000 + 3000000 * Fraction(6, 5)
    cash = max(7800000 - 2500000, 0)
    net = int(gross - cash)
    headroom = Fraction(13, 4) * adjusted - net
    assert headroom.denominator == 1
    ev = 8 * adjusted
    return dict(
        reported_ebitda_dollars=base,
        allowed_capped_addbacks_dollars=allowed,
        adjusted_ebitda_dollars=adjusted,
        net_debt_dollars=net,
        leverage_headroom_dollars=int(headroom),
        leverage_compliant=headroom >= 0,
        enterprise_value_dollars=ev,
        equity_value_dollars=ev - net,
        applied_close_ids=sorted(applied),
        concentration_pct=None,
        recommendation="reject" if headroom < 0 else "conditional_review",
        missing_evidence=["customer_revenue_schedule"],
        evidence_ids=sorted(
            [
                "AUDIT-SCHEDULE",
                "CLOSE-REGISTER",
                "ADD-BACK-REGISTER",
                "LOAN-SIGNED",
                "DEBT-CASH",
                "VALUATION-MANDATE",
                "DILIGENCE-CHECKLIST",
            ]
        ),
    )


def solve_incident(case):
    docs = documents(case)
    unique = {}
    for row in docs["TRACE"]["records"]:
        utc = row["local_seconds"] - ({"east": 90, "west": -30}[row["node"]])
        if 0 <= utc < 60:
            if row["event_id"] in unique:
                assert unique[row["event_id"]] == row
            unique[row["event_id"]] = row
    limited = [r for r in unique.values() if r["status"] == 429]
    builds = {r["build"] for r in limited}
    hypotheses = []
    if builds == {"new"}:
        hypotheses.append("H1")
    if len(unique) > 12 and builds == {"old", "new"}:
        hypotheses.append("H2")
    receipts = docs["CLIENT-RECEIPTS"]["records"]
    succeeded = sum(
        r["receipt"] is not None and r["receipt"] <= r["deadline"] for r in receipts
    )
    keys = {
        r["job"]: r["idempotency_key"] for r in unique.values() if r["attempt"] == 1
    }
    changed = sorted(
        {r["job"] for r in unique.values() if r["idempotency_key"] != keys[r["job"]]}
    )
    safe = [
        r
        for r in docs["MITIGATION-OPTIONS"]["records"]
        if r["max_attempts_per_job"] == 2
        and r["preserve_key"]
        and r["honor_deadline"]
        and r["job_admissions_per_minute"] * r["max_attempts_per_job"] <= 12
    ]
    choice = (
        min(safe, key=lambda r: (-r["job_admissions_per_minute"], r["id"]))
        if safe
        else None
    )
    return dict(
        unique_attempts=len(unique),
        rate_limited_attempts=len(limited),
        client_successes=succeeded,
        client_jobs=len(receipts),
        consistent_hypotheses=hypotheses,
        confirmed_root_cause=None,
        changed_key_jobs=changed,
        j5_action="complete_lookup",
        reconciliation_payment_ids=["P-55"],
        mitigation_id=choice["id"] if choice else None,
        worst_case_attempts_per_minute=(
            choice["job_admissions_per_minute"] * choice["max_attempts_per_job"]
            if choice
            else None
        ),
        evidence_ids=sorted(
            [
                "CLOCKS",
                "TRACE",
                "CLIENT-RECEIPTS",
                "VENDOR-CONTRACT",
                "HYPOTHESIS-RULES",
                "PAYMENT-LOOKUP",
                "MITIGATION-OPTIONS",
            ]
        ),
    )


def render(case):
    return (
        "You are preparing a decision for " + case["workflow"] + "\n\n"
        "Use only this fictional source pack. Document status and explicit overrides determine which facts apply. "
        "No external knowledge, tools, or actions are required.\n\nSOURCE PACK\n"
        + json.dumps(case["documents"], indent=2)
        + "\n\nTASK\n"
        + case["question"]
        + "\n\nReturn one JSON object without Markdown or explanation. "
        "The following describes required keys and value types; replace descriptions with values:\n"
        + json.dumps(case["schema"], indent=2)
    )


def build_prompts():
    return {
        "version": 2,
        "scenarios": [
            dict(id=c["id"], difficulty=c["difficulty"], prompt=render(c))
            for c in cases()
        ],
    }


@lru_cache(maxsize=1)
def derive_answers():
    solvers = {
        "disruption_response": solve_disruption,
        "diligence_reconciliation": solve_diligence,
        "incident_handoff": solve_incident,
    }
    return {c["id"]: solvers[c["base_id"]](c) for c in cases()}
