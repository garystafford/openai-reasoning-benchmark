"""Executable specifications for nine complex workflows; never read golden answers.

Every solver derives its inputs from the rendered source pack. Searches enumerate
finite candidate spaces; integer/Fraction arithmetic avoids floating-point gates.
"""

import copy
import itertools
import json
from collections import defaultdict
from fractions import Fraction
from pathlib import Path

DIRECTORY = Path(__file__).parent
VERSION = "complex-decisions-v3"
ORDERED_FIELDS = {"chain_ids", "sequence"}


def base_cases():
    return json.loads((DIRECTORY / "cases.json").read_text())["cases"]


def cases():
    return [{**c, "base_id": c["id"], "variant_kind": "base"} for c in base_cases()]


def documents(case):
    return {d["id"]: d["data"] for d in case["documents"]}


def evidence(case):
    return sorted(
        d["id"] for d in case["documents"] if d["status"] not in ("draft", "superseded")
    )


def render(case):
    return (
        "Prepare a decision for " + case["workflow"] + ".\n\n"
        "This is a fictional, self-contained reasoning task. No external facts, tools, or actions are required.\n\n"
        "SOURCE PACK\n"
        + json.dumps(case["documents"], indent=2)
        + "\n\nTASK\n"
        + case["question"]
        + "\n\nReturn one JSON object without Markdown or explanation. "
        "The following describes keys and types, not answers:\n"
        + json.dumps(case["schema"], indent=2)
    )


def build_prompts():
    return {
        "version": 2,
        "scenarios": [
            dict(id=c["id"], difficulty="complex", prompt=render(c)) for c in cases()
        ],
    }


def subsets(values):
    for bits in itertools.product((False, True), repeat=len(values)):
        yield [v for v, enabled in zip(values, bits) if enabled]


def half_up(value):
    value = Fraction(value)
    if value < 0:
        return -half_up(-value)
    return (2 * value.numerator + value.denominator) // (2 * value.denominator)


def ceil(value):
    value = Fraction(value)
    return -(-value.numerator // value.denominator)


def id_set(value, allowed):
    return (
        isinstance(value, list)
        and all(isinstance(x, str) for x in value)
        and len(value) == len(set(value))
        and set(value) <= set(allowed)
    )


def sourcing_plan(case, selected):
    d = documents(case)
    demand = d["DEMAND"]
    amendment = d["AMENDMENT"]
    offers = {r["id"]: r for r in d["OFFERS"]["offers"]}
    if not id_set(selected, offers) or any(not offers[i]["approved"] for i in selected):
        return None
    if len({offers[i]["supplier"] for i in selected if offers[i]["X"] > 0}) < 2:
        return None
    stock = {
        sku: demand["opening"][sku] - demand["quarantine"][sku] for sku in ("X", "Y")
    }
    ending = {}
    for day, usage in enumerate(demand["daily_demand"], 1):
        for i in selected:
            offer = offers[i]
            arrival = (
                amendment["confirmed_day"]
                if i == amendment["offer_id"]
                else offer["day"]
            )
            if arrival == day:
                for sku in stock:
                    stock[sku] += offer[sku]
        if any(stock[s] > demand["storage_cap"][s] for s in stock):
            return None
        for sku in stock:
            stock[sku] -= usage[sku]
        if any(v < 0 for v in stock.values()):
            return None
        ending[str(day)] = dict(stock)
    cost = sum(offers[i]["cost"] for i in selected)
    if set(amendment["discount_if_both"]) <= set(selected):
        cost -= amendment["discount_dollars"]
    return {"net_cost_dollars": cost, "ending_stock": ending}


def solve_sourcing(case):
    ids = sorted(r["id"] for r in documents(case)["OFFERS"]["offers"])
    feasible = [
        (p["net_cost_dollars"], selected, p)
        for selected in subsets(ids)
        if (p := sourcing_plan(case, selected)) is not None
    ]
    if not feasible:
        return {
            "selected_offer_ids": [],
            "net_cost_dollars": None,
            "ending_stock": None,
        }
    _, selected, plan = min(feasible, key=lambda x: (x[0], x[1]))
    return {"selected_offer_ids": selected, **plan}


def liquidity_plan(case, draws, bridge):
    d = documents(case)
    facility = d["FACILITIES"]
    if (
        not isinstance(draws, dict)
        or set(draws) != {"1", "2"}
        or type(bridge) is not bool
        or any(
            type(v) is not int or v < 0 or v % facility["increment"]
            for v in draws.values()
        )
        or sum(draws.values()) > facility["revolver_limit"]
    ):
        return None
    flows = defaultdict(Fraction)
    seen = {}
    for row in d["CASH-LEDGER"]["rows"]:
        if row["id"] in seen:
            if seen[row["id"]] != row:
                raise ValueError("Conflicting cash ledger ID")
            continue
        seen[row["id"]] = row
        if row["state"] == "settled":
            flows[row["period"]] += row["amount"] * Fraction(*d["FX"][row["currency"]])
    fee = (3 * draws["1"] + 2 * draws["2"]) * Fraction(
        facility["monthly_interest_bps"], 10000
    )
    if bridge:
        fee += facility["bridge_fee"]
    free = Fraction(d["BANK"]["cash_dollars"] - d["BANK"]["restricted_dollars"])
    ending = {}
    for period in (1, 2, 3):
        free += flows[period] + draws.get(str(period), 0)
        if period == 2 and bridge:
            free += facility["bridge_amount"]
        if period == 3:
            free -= (
                sum(draws.values()) + (facility["bridge_amount"] if bridge else 0) + fee
            )
        if free < facility["minimum_free_cash"]:
            return None
        assert free.denominator == 1
        ending[str(period)] = int(free)
    assert fee.denominator == 1
    return {"finance_cost_dollars": int(fee), "ending_free_cash": ending}


def solve_liquidity(case):
    f = documents(case)["FACILITIES"]
    amounts = range(0, f["revolver_limit"] + 1, f["increment"])
    best = None
    for a, b, bridge in itertools.product(amounts, amounts, (False, True)):
        plan = liquidity_plan(case, {"1": a, "2": b}, bridge)
        if plan is None:
            continue
        rank = (
            plan["finance_cost_dollars"],
            a + b + (f["bridge_amount"] if bridge else 0),
            a,
        )
        if best is None or rank < best[0]:
            best = (rank, a, b, bridge, plan)
    if best is None:
        return {
            "decision": "infeasible",
            "revolver_draws": {"1": 0, "2": 0},
            "use_bridge": False,
            "ending_free_cash": None,
            "finance_cost_dollars": None,
            "missing_evidence": [],
        }
    _, a, b, bridge, plan = best
    return {
        "decision": "finance",
        "revolver_draws": {"1": a, "2": b},
        "use_bridge": bridge,
        **plan,
        "missing_evidence": [],
    }


def portfolio_plan(case, selected):
    d = documents(case)
    limits = d["CAPITAL"]
    initiatives = {r["id"]: r for r in d["INITIATIVES"]["initiatives"]}
    if not id_set(selected, initiatives):
        return None
    rows = [initiatives[i] for i in selected]
    if any(
        not r["approved"]
        or not set(r["requires"]) <= set(selected)
        or set(r["incompatible"]) & set(selected)
        for r in rows
    ):
        return None
    cost = sum(r["cost"] for r in rows)
    engineers = sum(r["engineers"] for r in rows)
    if cost > limits["budget"] or engineers > limits["engineering_capacity"]:
        return None
    populations = {p for r in rows for p in r["benefits"]}
    annual = {
        str(y + 1): sum(
            max(r["benefits"].get(p, [0, 0])[y] for r in rows) for p in populations
        )
        for y in (0, 1)
    }
    discount = 1 + Fraction(limits["discount_bps"], 10000)
    npv = sum(Fraction(annual[str(y)]) / discount**y for y in (1, 2)) - cost
    return {
        "annual_gross_benefits": annual,
        "capital_dollars": cost,
        "engineering_used": engineers,
        "npv_cents": half_up(100 * npv),
        "_npv": npv,
    }


def solve_portfolio(case):
    ids = sorted(r["id"] for r in documents(case)["INITIATIVES"]["initiatives"])
    candidates = [
        (p["_npv"], selected, p)
        for selected in subsets(ids)
        if (p := portfolio_plan(case, selected)) is not None
    ]
    _, selected, plan = min(candidates, key=lambda x: (-x[0], x[1]))
    return {"selected_ids": selected, **{k: v for k, v in plan.items() if k != "_npv"}}


def staffing_plan(case, assignments):
    d = documents(case)
    work = d["WORKPLAN"]
    roles = work["roles"]
    people = {r["id"]: r for r in d["ROSTER"]["people"]}
    if not isinstance(assignments, dict) or set(assignments) != {
        r["id"] for r in roles
    }:
        return None
    assigned = defaultdict(int)
    weekly = {"1": 0, "2": 0, "3": 0}
    for role in roles:
        id = assignments[role["id"]]
        if not isinstance(id, str) or id not in people:
            return None
        person = people[id]
        if role["skill"] not in person["skills"] or (
            role["independent"]
            and (not person["independent"] or person["client_member"])
        ):
            return None
        assigned[id, role["week"]] += role["hours"]
        weekly[str(role["week"])] += role["hours"] * person["rate"]
    if assignments["review"] in (assignments["discovery"], assignments["model"]):
        return None
    if any(
        hours
        > people[id]["available"][week - 1] - d["RESERVATIONS"]["hours"][id][week - 1]
        for (id, week), hours in assigned.items()
    ):
        return None
    cost = sum(weekly.values())
    margin = Fraction((work["revenue"] - cost) * 10000, work["revenue"])
    if margin < work["minimum_margin_bps"]:
        return None
    return {
        "weekly_cost_dollars": weekly,
        "total_cost_dollars": cost,
        "margin_basis_points": int(margin),
    }


def solve_staffing(case):
    d = documents(case)
    roles = d["WORKPLAN"]["roles"]
    people = d["ROSTER"]["people"]
    eligible = [
        [
            p["id"]
            for p in people
            if r["skill"] in p["skills"]
            and (not r["independent"] or p["independent"] and not p["client_member"])
        ]
        for r in roles
    ]
    best = None
    for ids in itertools.product(*eligible):
        assignment = dict(zip((r["id"] for r in roles), ids))
        plan = staffing_plan(case, assignment)
        if plan is not None:
            rank = (plan["total_cost_dollars"], ids)
            if best is None or rank < best[0]:
                best = (rank, assignment, plan)
    if best is None:
        blockers = (
            ["independent_reviewer"] if not eligible[3] else ["capacity_or_margin"]
        )
        return {
            "decision": "unstaffable",
            "assignments": {},
            "weekly_cost_dollars": None,
            "total_cost_dollars": None,
            "margin_basis_points": None,
            "blockers": blockers,
        }
    return {"decision": "staff", "assignments": best[1], **best[2], "blockers": []}


def recovery_chain(case, ids):
    d = documents(case)
    target = d["RECOVERY-TARGET"]
    backups = {r["id"]: r for r in d["BACKUPS"]["backups"]}
    if not id_set(ids, backups) or not ids:
        return None
    keys = set(target["available_keys"]) | ({"K2"} if target["K2_available"] else set())
    parent, cutoff = None, -1
    for id in ids:
        r = backups[id]
        if (
            r["parent"] != parent
            or not r["verified"]
            or r["schema"] != target["engine_schema"]
            or r["key"] not in keys
            or not cutoff < r["cutoff"] <= target["incident_minute"]
        ):
            return None
        parent, cutoff = id, r["cutoff"]
    rpo = target["incident_minute"] - cutoff
    if rpo > target["max_rpo_minutes"]:
        return None
    return {
        "rpo_minutes": rpo,
        "restore_minutes": sum(backups[i]["minutes"] for i in ids),
    }


def recovery_jobs(case, restore_minutes):
    return [
        {"id": "RESTORE", "resource": "IO", "minutes": restore_minutes, "after": []},
        *documents(case)["RUNBOOK"]["jobs"],
    ]


def schedule_candidates(jobs):
    """Enumerate resource orders, then solve the induced DAG by longest paths."""
    by_resource = defaultdict(list)
    for job in jobs:
        by_resource[job["resource"]].append(job["id"])
    durations = {j["id"]: j["minutes"] for j in jobs}
    for orders in itertools.product(
        *(list(itertools.permutations(ids)) for ids in by_resource.values())
    ):
        dependencies = {j["id"]: set(j["after"]) for j in jobs}
        for order in orders:
            for before, after in zip(order, order[1:]):
                dependencies[after].add(before)
        starts, pending = {}, set(dependencies)
        while pending:
            ready = sorted(id for id in pending if dependencies[id] <= starts.keys())
            if not ready:
                break
            for id in ready:
                starts[id] = max(
                    (starts[p] + durations[p] for p in dependencies[id]), default=0
                )
                pending.remove(id)
        if not pending:
            yield max(starts[id] + durations[id] for id in starts), starts


def recovery_plan(case, ids, starts):
    chain = recovery_chain(case, ids)
    if chain is None or not isinstance(starts, dict):
        return None
    jobs = recovery_jobs(case, chain["restore_minutes"])
    by_id = {j["id"]: j for j in jobs}
    if (
        set(starts) != set(by_id)
        or any(type(v) is not int or v < 0 for v in starts.values())
        or starts["RESTORE"] != 0
    ):
        return None
    for j in jobs:
        if any(starts[j["id"]] < starts[p] + by_id[p]["minutes"] for p in j["after"]):
            return None
    for a, b in itertools.combinations(jobs, 2):
        if a["resource"] == b["resource"] and not (
            starts[a["id"]] + a["minutes"] <= starts[b["id"]]
            or starts[b["id"]] + b["minutes"] <= starts[a["id"]]
        ):
            return None
    finish = max(starts[j["id"]] + j["minutes"] for j in jobs)
    if finish > documents(case)["RECOVERY-TARGET"]["max_rto_minutes"]:
        return None
    return {"rpo_minutes": chain["rpo_minutes"], "finish_minutes": finish}


def solve_recovery(case):
    backups = {r["id"]: r for r in documents(case)["BACKUPS"]["backups"]}
    candidates = []
    for leaf in backups:
        ids = []
        current = leaf
        while current is not None and current not in ids:
            ids.append(current)
            current = backups[current]["parent"]
        if current is not None:
            continue
        ids.reverse()
        chain = recovery_chain(case, ids)
        if chain is None:
            continue
        for finish, starts in schedule_candidates(
            recovery_jobs(case, chain["restore_minutes"])
        ):
            if recovery_plan(case, ids, starts) is None:
                continue
            rank = (
                finish,
                chain["rpo_minutes"],
                ids,
                tuple(starts[k] for k in sorted(starts)),
            )
            candidates.append((rank, starts))
    if not candidates:
        return {
            "decision": "infeasible",
            "chain_ids": [],
            "rpo_minutes": None,
            "finish_minutes": None,
            "start_minutes": {},
        }
    rank, starts = min(candidates, key=lambda c: c[0])
    return {
        "decision": "recover",
        "chain_ids": rank[2],
        "rpo_minutes": rank[1],
        "finish_minutes": rank[0],
        "start_minutes": starts,
    }


def solve_canary(case):
    d = documents(case)
    policy = d["RELEASE-POLICY"]
    rows = d["METRICS"]["segments"]
    assert sum(policy["weights_bps"].values()) == 10000
    control = sum(
        policy["weights_bps"][r["id"]]
        * Fraction(r["control_errors"], r["control_requests"])
        for r in rows
    )
    canary = sum(
        policy["weights_bps"][r["id"]]
        * Fraction(r["canary_errors"], r["canary_requests"])
        for r in rows
    )
    delta = canary - control
    projected = ceil(max(0, delta) * Fraction(policy["next_canary_requests"], 10000))
    missing = sorted(
        r["id"]
        for r in rows
        if min(r["control_requests"], r["canary_requests"])
        < policy["minimum_requests_per_segment"]
    )
    decision = (
        "hold"
        if missing
        else (
            "rollback"
            if (
                delta > policy["maximum_delta_bps"]
                or projected > policy["remaining_error_budget"]
                or max(r["canary_p95_ms"] for r in rows) > policy["maximum_p95_ms"]
            )
            else "promote"
        )
    )
    raw = lambda prefix: half_up(
        Fraction(
            10000 * sum(r[prefix + "_errors"] for r in rows),
            sum(r[prefix + "_requests"] for r in rows),
        )
    )
    return {
        "decision": decision,
        "standardized_control_bps": half_up(control),
        "standardized_canary_bps": half_up(canary),
        "delta_bps": half_up(delta),
        "raw_control_bps": raw("control"),
        "raw_canary_bps": raw("canary"),
        "projected_incremental_errors": projected,
        "missing_evidence": missing,
        "confirmed_root_cause": None,
    }


SOLVERS = {
    "sourcing_network": solve_sourcing,
    "liquidity_facilities": solve_liquidity,
    "transformation_roadmap": solve_portfolio,
    "engagement_capacity": solve_staffing,
    "recovery_chain": solve_recovery,
    "canary_segment_analysis": solve_canary,
}


def solve(case):
    return {**SOLVERS[case["base_id"]](case), "evidence_ids": evidence(case)}


def derive_answers():
    return {c["id"]: solve(c) for c in cases()}


def feasibility(case, actual, expected):
    """Check submitted choices independently of their optimality and numeric claims."""
    kind = case["selection"]
    base = case["base_id"]
    plan = None
    violation = False
    detail = ""
    if kind == "sourcing":
        ids = actual.get("selected_offer_ids")
        plan = sourcing_plan(case, ids)
        violation = isinstance(ids, list) and bool(ids) and plan is None
        if plan:
            detail = f"Feasible cost ${plan['net_cost_dollars']}; optimum ${expected['net_cost_dollars']}"
    elif kind == "liquidity":
        plan = liquidity_plan(
            case, actual.get("revolver_draws"), actual.get("use_bridge")
        )
        violation = actual.get("decision") == "finance" and plan is None
        if (
            actual.get("decision") == "infeasible"
            and expected["decision"] == "infeasible"
        ):
            plan = {}
    elif kind == "portfolio":
        ids = actual.get("selected_ids")
        plan = portfolio_plan(case, ids)
        violation = isinstance(ids, list) and bool(ids) and plan is None
        if plan:
            detail = f"Feasible NPV {plan['npv_cents']} cents; optimum {expected['npv_cents']} cents"
    elif kind == "staffing":
        plan = staffing_plan(case, actual.get("assignments"))
        violation = actual.get("decision") == "staff" and plan is None
        if (
            actual.get("decision") == "unstaffable"
            and expected["decision"] == "unstaffable"
        ):
            plan = {}
    elif kind == "recovery":
        plan = recovery_plan(case, actual.get("chain_ids"), actual.get("start_minutes"))
        violation = actual.get("decision") == "recover" and plan is None
        if (
            actual.get("decision") == "infeasible"
            and expected["decision"] == "infeasible"
        ):
            plan = {}
    elif kind == "canary":
        violation = (
            actual.get("decision") == "promote" and expected["decision"] != "promote"
        )
        plan = (
            {}
            if actual.get("decision") in ("hold", "rollback", "promote")
            and not violation
            else None
        )
    elif kind == "events":
        violation = (
            actual.get("decision") == "publish" and expected["decision"] != "publish"
        )
        plan = (
            {}
            if actual.get("decision") in ("hold", "publish") and not violation
            else None
        )
    else:
        # Arithmetic bridge tasks have no operational action to mark unsafe.
        plan = {}
    return plan is not None, violation, detail
