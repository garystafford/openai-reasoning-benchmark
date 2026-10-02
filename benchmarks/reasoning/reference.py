"""Executable task specifications. Never reads the independently authored answer key."""

import itertools
import json
from collections import Counter
from fractions import Fraction
from pathlib import Path
from benchmarks.complex.reference import (
    documents,
    evidence,
    render,
    subsets,
    half_up,
    id_set,
)

DIRECTORY = Path(__file__).parent
VERSION = "unique-reasoning-v4.1"


def cases():
    return json.loads((DIRECTORY / "cases.json").read_text())["cases"]


def build_prompts():
    return {
        "version": 2,
        "scenarios": [
            {"id": c["id"], "difficulty": "complex", "prompt": render(c)}
            for c in cases()
        ],
    }


def production_plan(c, cut, finish):
    d = documents(c)
    lots = {r["id"]: r for r in d["LOTS"]["lots"]}
    m = d["MAINTENANCE"]
    if (
        not isinstance(cut, dict)
        or not isinstance(finish, dict)
        or set(cut) != set(lots)
        or set(finish) != set(lots)
    ):
        return None
    if any(type(t) is not int or t < 0 for t in [*cut.values(), *finish.values()]):
        return None
    for i, r in lots.items():
        if cut[i] < r["release"] or finish[i] < cut[i] + r["cut"]:
            return None
        if finish[i] < m["end"] and finish[i] + r["finish"] > m["start"]:
            return None
    for starts, stage in [(cut, "cut"), (finish, "finish")]:
        order = sorted(lots, key=lambda i: starts[i])
        if any(
            starts[a] + lots[a][stage] > starts[b] for a, b in zip(order, order[1:])
        ):
            return None
    ends = {i: finish[i] + r["finish"] for i, r in lots.items()}
    late = {i: max(0, ends[i] - r["due"]) for i, r in lots.items()}
    return dict(
        cut_starts=cut,
        finish_starts=finish,
        finish_completions=ends,
        late_minutes=late,
        total_penalty_dollars=sum(late[i] * r["penalty"] for i, r in lots.items()),
        makespan_minutes=max(ends.values()),
    )


def production(c):
    d = documents(c)
    lots = {r["id"]: r for r in d["LOTS"]["lots"]}
    ids = sorted(lots)
    m = d["MAINTENANCE"]
    candidates = []
    for co in itertools.permutations(ids):
        cut = {}
        t = 0
        for i in co:
            cut[i] = max(t, lots[i]["release"])
            t = cut[i] + lots[i]["cut"]
        for fo in itertools.permutations(ids):
            finish = {}
            t = 0
            for i in fo:
                start = max(t, cut[i] + lots[i]["cut"])
                if start < m["end"] and start + lots[i]["finish"] > m["start"]:
                    start = m["end"]
                finish[i] = start
                t = start + lots[i]["finish"]
            p = production_plan(c, cut, finish)
            candidates.append(
                (
                    (
                        p["total_penalty_dollars"],
                        p["makespan_minutes"],
                        tuple(cut[i] for i in ids),
                        tuple(finish[i] for i in ids),
                    ),
                    p,
                )
            )
    return min(candidates, key=lambda x: x[0])[1]


def warehouse(c):
    d = documents(c)
    rows = {r["id"]: r for r in d["ORDERS"]["orders"]}
    starts = {"PICK": {}, "PACK": {}}
    end = {"PICK": {}, "PACK": {}}
    busy = {"PICK": 0, "PACK": 0}
    t = 0
    while len(end["PACK"]) < len(rows) or t < max(end["PACK"].values(), default=0):
        for stage in ("PICK", "PACK"):
            if busy[stage] > t or any(a <= t < b for a, b in d["SHIFT"][stage]):
                continue
            ready = [
                i
                for i, r in rows.items()
                if i not in starts[stage]
                and (
                    r["arrival"] <= t
                    if stage == "PICK"
                    else i in end["PICK"]
                    and end["PICK"][i] <= t
                    and r["hold_until"] <= t
                )
            ]
            if ready:
                i = min(ready, key=lambda i: (rows[i]["due"], i))
                starts[stage][i] = t
                end[stage][i] = t + rows[i][stage.lower()]
                busy[stage] = end[stage][i]
        t += 1
        if t > 10000:
            raise ValueError("Queue did not terminate")
    late = {i: max(0, end["PACK"][i] - r["due"]) for i, r in rows.items()}
    return dict(
        pick_start=starts["PICK"],
        pack_start=starts["PACK"],
        delivered_at=end["PACK"],
        late_order_ids=sorted(i for i, v in late.items() if v),
        total_lateness_minutes=sum(late.values()),
    )


def supplier_values(c, ids):
    d = documents(c)
    rows = {r["id"]: r for r in d["CONTRACTS"]["suppliers"]}
    policy = d["SCENARIOS"]
    if not id_set(ids, rows) or len(ids) != 2:
        return None
    net = {}
    for scenario in policy["names"]:
        remaining = policy["demand"]
        value = -sum(rows[i]["fee"] for i in ids)
        for i in sorted(ids, key=lambda i: (rows[i]["price"], i)):
            r = rows[i]
            n = (
                min(remaining, r["capacity"][scenario])
                if r["price"] < policy["value_per_unit"]
                else 0
            )
            value += n * (policy["value_per_unit"] - r["price"])
            remaining -= n
        net[scenario] = value
    return net


def supplier(c):
    rows = documents(c)["CONTRACTS"]["suppliers"]
    all_values = {
        pair: supplier_values(c, list(pair))
        for pair in itertools.combinations(sorted(r["id"] for r in rows), 2)
    }
    benchmarks = {
        s: max(v[s] for v in all_values.values())
        for s in documents(c)["SCENARIOS"]["names"]
    }
    ranked = []
    for pair, net in all_values.items():
        regret = {s: benchmarks[s] - v for s, v in net.items()}
        mean = Fraction(sum(net.values()), len(net))
        ranked.append(
            (
                (max(regret.values()), -mean, pair),
                dict(
                    chosen_ids=list(pair),
                    per_scenario_net_dollars=net,
                    per_scenario_regret_dollars=regret,
                    worst_regret_dollars=max(regret.values()),
                    mean_net_cents=half_up(mean * 100),
                ),
            )
        )
    return min(ranked, key=lambda x: x[0])[1]


def invoice(c):
    d = documents(c)
    receipts = d["RECEIPTS"]
    inv = d["INVOICES"]
    po = d["PO"]["lines"]
    sub = d["SIGNED-SUBSTITUTION"]
    duplicates = []

    def dedup(rows):
        counts = Counter(r["id"] for r in rows)
        duplicates.extend(i for i, n in counts.items() if n > 1)
        return list({r["id"]: r for r in rows}.values())

    received = dedup(receipts["rows"])
    returned = dedup(receipts["returns"])
    credits = dedup(inv["credits"])
    pay = {}
    held = {}
    reasons = {}
    for p in po:

        def equiv(r):
            return Fraction(r["quantity"]) * (
                1
                if r["sku"] == p["sku"]
                else (
                    Fraction(sub["numerator"], sub["denominator"])
                    if r["sku"] == sub["from"] and p["sku"] == sub["to"]
                    else 0
                )
            )

        net = sum(equiv(r) for r in received if r["line"] == p["id"]) - sum(
            equiv(r) for r in returned if r["line"] == p["id"]
        )
        i = next(r for r in inv["lines"] if r["id"] == p["id"])
        quantity = min(net, i["equivalents"], p["quantity"])
        held[p["id"]] = int(max(0, i["equivalents"] - quantity))
        price = i["price"] == p["unit_price"]
        pay[p["id"]] = (
            int(
                max(
                    0,
                    quantity * p["unit_price"]
                    - sum(r["amount"] for r in credits if r["line"] == p["id"]),
                )
            )
            if price
            else 0
        )
        reasons[p["id"]] = (
            "price_dispute"
            if not price
            else "short_receipt" if held[p["id"]] else "matched"
        )
    return dict(
        payable_dollars=pay,
        held_equivalents=held,
        reasons=reasons,
        total_payable_dollars=sum(pay.values()),
        deduplicated_record_ids=sorted(duplicates),
    )


def treasury(c):
    d = documents(c)
    agreement = d["NETTING-AGREEMENT"]
    positions = {
        f"{e}:{cur}": 0 for cur, pool in agreement["pools"].items() for e in pool
    }
    gross = []
    outside = {}
    seq = []
    for r in {r["id"]: r for r in d["INVOICES"]["rows"]}.values():
        if r["state"] == "void":
            continue
        cur = r["currency"]
        pool = agreement["pools"].get(cur, [])
        if r["from"] in pool and r["to"] in pool:
            positions[f"{r['from']}:{cur}"] -= r["amount"]
            positions[f"{r['to']}:{cur}"] += r["amount"]
        else:
            gross.append(r["id"])
            key = f"{r['from']}:{cur}"
            outside[key] = outside.get(key, 0) + r["amount"]
    for cur, pool in sorted(agreement["pools"].items()):
        amounts = {e: positions[f"{e}:{cur}"] for e in pool}
        for debtor in sorted(e for e, v in amounts.items() if v < 0):
            for creditor in sorted(e for e, v in amounts.items() if v > 0):
                n = min(-amounts[debtor], amounts[creditor])
                if n:
                    seq.append(f"{debtor}>{creditor}:{cur}:{n}")
                    amounts[debtor] += n
                    amounts[creditor] -= n
    cash = d["CASH"]["free"]
    funding = {
        key: max(
            0, max(0, -positions.get(key, 0)) + outside.get(key, 0) - cash.get(key, 0)
        )
        for key in sorted(set(cash) | set(positions) | set(outside))
    }
    total = sum(
        Fraction(n) * Fraction(*agreement["fx"][key.split(":")[1]])
        for key, n in funding.items()
    )
    return dict(
        positions=positions,
        settlement_sequence=seq,
        gross_obligation_ids=sorted(gross),
        funding_dollars=funding,
        total_external_usd_dollars=int(total),
    )


def refinance_plan(c, loan, p):
    d = documents(c)
    r = d["CLOSING"]
    q = next((q for q in d["QUOTES"]["quotes"] if q["id"] == loan), None)
    if (
        q is None
        or not q["signed"]
        or type(p) is not int
        or p % 10000
        or p < r["old_debt"]
    ):
        return None
    synergy = min(Fraction(r["proposed_synergies"]), Fraction(r["base_earnings"], 4))
    limit = 3 * (r["base_earnings"] + synergy)
    fee = q["fixed_fee"] + Fraction(q["fee_bps"] * p, 10000)
    debt = p + fee
    interest = Fraction(q["interest_bps"] * debt, 10000)
    distribution = (
        r["opening_free_cash"]
        + p
        - r["old_debt"]
        - r["closing_cost"]
        - fee
        - r["minimum_cash"]
    )
    if debt > limit or interest > r["annual_operating_cash"] or distribution < 0:
        return None
    return dict(
        loan_id=loan,
        principal_dollars=p,
        fee_dollars=int(fee),
        gross_debt_dollars=int(debt),
        allowed_synergies_dollars=int(synergy),
        leverage_limit_dollars=int(limit),
        annual_interest_dollars=int(interest),
        distributable_cash_dollars=int(distribution),
    )


def refinance(c):
    r = documents(c)["CLOSING"]
    upper = 3 * (r["base_earnings"] + r["proposed_synergies"])
    plans = [
        p
        for q in documents(c)["QUOTES"]["quotes"]
        for principal in range(r["old_debt"], upper + 1, 10000)
        if (p := refinance_plan(c, q["id"], principal))
    ]
    return min(
        plans,
        key=lambda p: (
            -p["distributable_cash_dollars"],
            p["annual_interest_dollars"],
            p["loan_id"],
            p["principal_dollars"],
        ),
    )


def cohort(c):
    d = documents(c)
    rows = d["CUSTOMERS"]["rows"]
    fx = d["FX"]
    opening = expansion = contraction = churn = new = acquired = organic = reported = (
        endcohort
    ) = gross = 0
    for r in rows:
        start = r["opening"] * Fraction(*fx["opening"][r["currency"]])
        end = (r["end"] if r["end_active"] else 0) * Fraction(
            *fx["opening"][r["currency"]]
        )
        reported += (r["end"] if r["end_active"] else 0) * Fraction(
            *fx["closing"][r["currency"]]
        )
        if r["acquired"]:
            acquired += end
            continue
        organic += end
        if r["opening_active"] and start > 0:
            opening += start
            endcohort += end
            gross += min(start, end)
            if not r["end_active"]:
                churn += start
            else:
                expansion += max(0, end - start)
                contraction += max(0, start - end)
        else:
            new += end
    return dict(
        opening_cohort_dollars=int(opening),
        expansion_dollars=int(expansion),
        contraction_dollars=int(contraction),
        churn_dollars=int(churn),
        new_reactivated_dollars=int(new),
        end_organic_constant_dollars=int(organic),
        acquired_constant_dollars=int(acquired),
        end_reported_dollars=int(reported),
        fx_effect_dollars=int(reported - organic - acquired),
        net_retention_bps=int(endcohort * 10000 // opening),
        gross_retention_bps=int(gross * 10000 // opening),
    )


def cash(c):
    d = documents(c)
    e = d["EARNINGS"]
    b = d["CASH-BRIDGE"]
    approved = [r for r in e["exceptional"] if r["approved"]]
    adj = e["reported_ebitda"] + sum(r["amount"] for r in approved)
    op = (
        e["reported_ebitda"]
        + b["noncash_expenses"]
        - b["cash_taxes"]
        - b["cash_interest"]
        - b["increase_AR_reported"]
        + b["AR_fx_only"]
        - b["increase_inventory"]
        + b["increase_AP"]
    )
    sustainable = op + sum(r["amount"] for r in approved if r["cash_paid"])
    maintenance = b["maintenance_capex"] + b["recurring_maintenance_in_growth"]
    fcf = sustainable - maintenance
    conversion = fcf * 10000 // adj
    return dict(
        adjusted_ebitda_dollars=adj,
        operating_cash_dollars=op,
        sustainable_operating_cash_dollars=sustainable,
        maintenance_capex_dollars=maintenance,
        sustainable_fcf_dollars=fcf,
        cash_conversion_bps=conversion,
        recommendation="investigate" if conversion < 6000 else "acceptable",
        confirmed_fraud=None,
    )


def impact(c):
    d = documents(c)
    protocol = d["STUDY-PROTOCOL"]
    means = {}
    missing = []
    for r in d["CELLS"]["rows"]:
        key = f"{r['group']}:{r['period']}"
        means[key] = means.get(key, 0) + Fraction(
            *protocol[r["segment"] + "_weight"]
        ) * Fraction(r["duration"], r["units"])
        if r["units"] < 20:
            missing.append(key + ":" + r["segment"])
    effect = means["T:pre"] - means["T:post"] - means["C:pre"] + means["C:post"]
    return dict(
        standardized_minutes_cents={k: half_up(v * 100) for k, v in means.items()},
        effect_minutes_cents=half_up(effect * 100),
        decision=(
            "insufficient"
            if missing
            else "extend_trial" if effect >= 2 else "stop_trial"
        ),
        missing_evidence=sorted(missing),
        missing_assumptions=(
            [] if protocol["parallel_trends_verified"] else ["parallel_trends"]
        ),
        confirmed_causal_effect=None,
    )


def automation_plan(c, policy):
    d = documents(c)
    p = next((p for p in d["POLICIES"]["policies"] if p["id"] == policy), None)
    if p is None:
        return None
    review = invalid = rejected = handling = 0
    for r in d["CALIBRATION"]["strata"]:
        route = p["routes"][r["id"]]
        review += r["cases"] if route == "review" else 0
        invalid += r["cases"] - r["valid"] if route == "approve" else 0
        rejected += r["valid"] if route == "reject" else 0
        handling += r["cases"] * (5 if route == "review" else 1)
        if r["regulated"] and route == "approve":
            return None
    if review > 120 or invalid > 5:
        return None
    error = invalid * 100 + rejected * 20
    return dict(
        policy_id=policy,
        review_cases=review,
        invalid_approvals=invalid,
        valid_rejections=rejected,
        handling_cost_dollars=handling,
        error_cost_dollars=error,
        total_cost_dollars=handling + error,
    )


def automation(c):
    plans = []
    bad = []
    for p in documents(c)["POLICIES"]["policies"]:
        plan = automation_plan(c, p["id"])
        if plan:
            plans.append(plan)
        else:
            bad.append(p["id"])
    return {
        **min(plans, key=lambda p: (p["total_cost_dollars"], p["policy_id"])),
        "ineligible_policy_ids": sorted(bad),
    }


def assignment_plan(c, assignment):
    d = documents(c)
    packages = {p["id"]: p for p in d["PACKAGES"]["packages"]}
    people = {p["id"]: p for p in d["PEOPLE"]["people"]}
    if (
        not isinstance(assignment, dict)
        or set(assignment) != set(packages)
        or any(p not in people for p in assignment.values())
    ):
        return None
    hours = dict.fromkeys(people, 0)
    labor = handoff = 0
    for i, p in packages.items():
        worker = people[assignment[i]]
        if p["skill"] not in worker["skills"]:
            return None
        hours[worker["id"]] += p["hours"]
        labor += worker["rate"] * p["hours"]
        handoff += 500 * sum(assignment[i] != assignment[j] for j in p["after"])
    if any(hours[i] > p["available"] - p["reserved"] for i, p in people.items()):
        return None
    if not people[assignment["V"]]["independent"] or assignment["V"] in (
        assignment["A"],
        assignment["B"],
    ):
        return None
    return dict(
        assignments=assignment,
        hours_used=hours,
        labor_cost_dollars=labor,
        handoff_cost_dollars=handoff,
        total_cost_dollars=labor + handoff,
    )


def assignment(c):
    d = documents(c)
    ids = sorted(p["id"] for p in d["PACKAGES"]["packages"])
    people = sorted(p["id"] for p in d["PEOPLE"]["people"])
    plans = [
        p
        for values in itertools.product(people, repeat=len(ids))
        if (p := assignment_plan(c, dict(zip(ids, values))))
    ]
    return min(
        plans,
        key=lambda p: (
            p["total_cost_dollars"],
            tuple(p["assignments"][i] for i in ids),
        ),
    )


def rescue_plan(c, ids):
    d = documents(c)
    b = d["BASELINE"]
    options = {r["id"]: r for r in d["OPTIONS"]["options"]}
    if not id_set(ids, options):
        return None
    rows = [options[i] for i in ids]
    if any(not r["signed"] or set(r["incompatible"]) & set(ids) for r in rows):
        return None
    day = b["base_day"] + sum(r["day_delta"] for r in rows)
    spend = b["base_future_cost"] + sum(r["cost"] for r in rows)
    success = min(
        10000, b["base_success_bps"] + sum(r["success_delta_bps"] for r in rows)
    )
    if day > 12 or spend > b["cash_cap"]:
        return None
    payout = (
        b["signed_remaining_fee"]
        + sum(r["scope_fee"] for r in rows)
        + (b["bonus"] if day <= 10 else 0)
    )
    return dict(
        selected_ids=sorted(ids),
        completion_day=day,
        success_bps=success,
        future_spend_dollars=spend,
        expected_incremental_net_cents=half_up(
            Fraction(payout * success, 100) - spend * 100
        ),
        historical_spend_dollars=b["past_spend"],
    )


def rescue(c):
    plans = [
        p
        for ids in subsets(sorted(r["id"] for r in documents(c)["OPTIONS"]["options"]))
        if (p := rescue_plan(c, ids))
    ]
    return min(
        plans,
        key=lambda p: (
            -p["expected_incremental_net_cents"],
            p["future_spend_dollars"],
            p["selected_ids"],
        ),
    )


def faults(c):
    d = documents(c)
    probes = d["PROBES"]["probes"]
    consistent = [
        ids
        for ids in subsets(sorted(d["TOPOLOGY"]["components"]))
        if all(
            r["status"] == "unknown"
            or (
                bool(set(r["path"]) & set(ids))
                if r["status"] == "failed"
                else not set(r["path"]) & set(ids)
            )
            for r in probes
        )
    ]
    n = min(map(len, consistent))
    explanations = sorted(ids for ids in consistent if len(ids) == n)
    confirmed = set.intersection(*(set(ids) for ids in explanations))
    action = (
        min(
            (
                r
                for r in d["CONTAINMENT"]["actions"]
                if r["approved"] and confirmed <= set(r["covers"])
            ),
            key=lambda r: (r["cost"], r["id"]),
        )
        if confirmed
        else None
    )
    return dict(
        minimal_explanations={f"E{i}": ids for i, ids in enumerate(explanations, 1)},
        confirmed_fault_ids=sorted(confirmed),
        action_id=action["id"] if action else None,
        action_cost_dollars=action["cost"] if action else None,
        missing_evidence=sorted(r["id"] for r in probes if r["status"] == "unknown"),
    )


def leases(c):
    d = documents(c)
    l = d["LEASES"]
    events = d["EVENTS"]
    valid = []
    rejected = []
    seq = set()
    for r in {r["id"]: r for r in events["writes"]}.values():
        t = r["local_time"] - l["offsets"][r["host"]]
        lease = next(x for x in l["leases"] if x["host"] == r["host"])
        fence = max(
            [events["initial_fence"]]
            + [
                f["token"]
                for f in events["fences"]
                if f["local_time"] - l["offsets"][f["host"]] <= t
            ]
        )
        ok = (
            lease["start"] <= t < lease["end"]
            and r["token"] == lease["token"]
            and r["token"] >= fence
        )
        if not ok:
            rejected.append(r["id"])
        elif r["ack"]:
            valid.append(r["id"])
            seq.add(r["seq"])
    prefix = 0
    while prefix + 1 in seq:
        prefix += 1
    grant = d["RECOVERY-GRANT"]
    ready = (
        grant["quorum"]
        and grant["token"] > max(r["token"] for r in l["leases"])
        and grant["fencing_installed"]
    )
    return dict(
        valid_ack_ids=sorted(valid),
        rejected_ids=sorted(rejected),
        safe_replay_sequence=prefix,
        recovery_token=grant["token"],
        decision="restart" if ready else "wait_for_fence",
        missing_evidence=[] if grant["fencing_installed"] else ["fencing_installation"],
    )


def restore_plan(c, ids):
    d = documents(c)
    stores = d["CHECKPOINTS"]
    policy = d["RECOVERY-POLICY"]
    if not isinstance(ids, dict) or set(ids) != set(stores):
        return None
    picked = {
        s: next((r for r in rows if r["id"] == ids[s] and r["verified"]), None)
        for s, rows in stores.items()
    }
    if any(r is None for r in picked.values()):
        return None
    included = []
    transactions = d["TRANSACTIONS"]["transactions"]
    for r in transactions:
        flags = [seq <= picked[s]["sequence"] for s, seq in r["sequences"].items()]
        if any(flags) and not all(flags):
            return None
        if all(flags):
            included.append(r["id"])
    if any(
        r["requires_order"] and r["requires_order"] not in included
        for r in transactions
        if r["id"] in included
    ):
        return None
    time = min(r["coordinator_time"] for r in picked.values())
    rpo = policy["incident"] - time
    if rpo > policy["max_rpo"]:
        return None
    return dict(
        checkpoint_ids=ids,
        included_transaction_ids=sorted(included),
        recoverable_time=time,
        rpo_minutes=rpo,
        reconstructed_balance_dollars=policy["opening_balance"]
        + sum(r["amount"] for r in transactions if r["id"] in included),
    )


def restore(c):
    stores = documents(c)["CHECKPOINTS"]
    order = ["ORDERS", "PAYMENTS"]
    plans = [
        p
        for values in itertools.product(*(stores[s] for s in order))
        if (p := restore_plan(c, {s: r["id"] for s, r in zip(order, values)}))
    ]
    sequence = {r["id"]: r["sequence"] for rows in stores.values() for r in rows}
    return min(
        plans,
        key=lambda p: (
            -p["recoverable_time"],
            -sum(sequence[i] for i in p["checkpoint_ids"].values()),
            tuple(p["checkpoint_ids"][s] for s in order),
        ),
    )


def regional_plan(c, ids):
    d = documents(c)
    rows = {r["id"]: r for r in d["CAPACITY"]["blocks"]}
    policy = d["FAILURE-MODEL"]
    if not id_set(ids, rows):
        return None
    selected = [rows[i] for i in ids]
    eligible = [r for r in selected if r["latency_ms"] <= policy["max_latency_ms"]]
    normal = sum(r["capacity"] for r in eligible)
    surviving = {
        domain: sum(r["capacity"] for r in eligible if r["domain"] != domain)
        for domain in policy["domains"]
    }
    if (
        not any(r["region"] == policy["primary_region"] for r in selected)
        or normal < policy["demand"]
        or min(surviving.values()) < policy["demand"]
    ):
        return None
    return dict(
        selected_ids=sorted(ids),
        total_cost_dollars=sum(r["cost"] for r in selected),
        eligible_normal_capacity=normal,
        surviving_capacity_by_domain=surviving,
    )


def regional(c):
    plans = [
        p
        for ids in subsets(sorted(r["id"] for r in documents(c)["CAPACITY"]["blocks"]))
        if (p := regional_plan(c, ids))
    ]
    return min(
        plans,
        key=lambda p: (
            p["total_cost_dollars"],
            len(p["selected_ids"]),
            p["selected_ids"],
        ),
    )


def migration_plan(c, sequence):
    d = documents(c)
    actions = d["MIGRATION"]["actions"]
    if not id_set(sequence, actions) or len(sequence) != len(actions):
        return None
    state = dict(d["MIGRATION"]["initial"])
    requirements = {
        "E": {},
        "D": {"schema": "both"},
        "A": {"schema": "both", "dual_write": True},
        "W": {"schema": "both", "backfilled": True},
        "B": {"dual_write": True, "api": "new"},
        "V": {"backfilled": True, "worker": "new"},
        "F": {"validated": True},
        "C": {"api": "new", "worker": "new", "validated": True, "fence": True},
    }
    updates = {
        "E": {"schema": "both"},
        "D": {"dual_write": True},
        "A": {"api": "new"},
        "W": {"worker": "new"},
        "B": {"backfilled": True},
        "V": {"validated": True},
        "F": {"fence": True},
        "C": {"schema": "new"},
    }
    for i in sequence:
        if any(state[k] != v for k, v in requirements[i].items()):
            return None
        state.update(updates[i])
        if state["schema"] == "old" and (
            state["api"] == "new" or state["worker"] == "new"
        ):
            return None
        if state["schema"] == "new" and not (
            state["api"] == state["worker"] == "new" and state["fence"]
        ):
            return None
    index = sequence.index("C")
    return dict(
        sequence=sequence,
        last_reversible_action=sequence[index - 1],
        actions_that_require_dual_write=sorted(
            i for i, r in requirements.items() if r.get("dual_write")
        ),
        final_state=state,
    )


def migration(c):
    return next(
        p
        for seq in itertools.permutations(sorted(documents(c)["MIGRATION"]["actions"]))
        if (p := migration_plan(c, list(seq)))
    )


def version_plan(c, ids, allow_invalid=False):
    d = documents(c)
    versions = d["VERSIONS"]
    policy = d["RELEASE-POLICY"]
    if not isinstance(ids, dict) or set(ids) != set(versions):
        return None
    picked = {
        k: next((r for r in rows if r["id"] == ids[k]), None)
        for k, rows in versions.items()
    }
    if any(r is None for r in picked.values()):
        return None
    a, w, client, auth = [picked[k] for k in ("API", "WORKER", "CLIENT", "AUTH")]
    checks = {
        "protocol_match": a["protocol"] == w["protocol"],
        "client_protocol": a["protocol"] in client["accepts"],
        "auth_capability": set(a["requires"]) <= set(auth["capabilities"]),
        "auth_generation": w["auth_generation"] <= auth["generation"],
        "pagination": not policy["pagination"] or client["pagination"],
        "idempotency": not policy["idempotency"] or a["idempotency"],
    }
    bad = sorted(k for k, v in checks.items() if not v)
    if bad and not allow_invalid:
        return None
    return dict(
        versions=ids,
        total_migration_cost_dollars=sum(r["cost"] for r in picked.values()),
        selected_protocol=a["protocol"],
        violations=bad,
    )


def versions(c):
    d = documents(c)
    order = ["API", "WORKER", "CLIENT", "AUTH"]
    plans = [
        p
        for values in itertools.product(*(d["VERSIONS"][k] for k in order))
        if (p := version_plan(c, {k: r["id"] for k, r in zip(order, values)}))
    ]
    p = min(
        plans,
        key=lambda p: (
            p["total_migration_cost_dollars"],
            tuple(p["versions"][k] for k in order),
        ),
    )
    p.pop("violations")
    cheapest = {
        k: min(d["VERSIONS"][k], key=lambda r: (r["cost"], r["id"]))["id"]
        for k in order
    }
    p["rejected_cheapest_bundle_reasons"] = version_plan(c, cheapest, True)[
        "violations"
    ]
    return p


SOLVERS = dict(
    production_jobshop=production,
    warehouse_queue=warehouse,
    contingent_supplier=supplier,
    invoice_match=invoice,
    treasury_netting=treasury,
    refinance_waterfall=refinance,
    revenue_cohort_bridge=cohort,
    cash_earnings_quality=cash,
    rollout_impact_estimate=impact,
    automation_routing=automation,
    workpackage_assignment=assignment,
    engagement_rescue=rescue,
    fault_hypothesis_constraints=faults,
    lease_fencing_recovery=leases,
    consistent_restore_cut=restore,
    regional_failover=regional,
    expand_contract_rollout=migration,
    version_contract_resolution=versions,
)


def solve(c):
    return {**SOLVERS[c["id"]](c), "evidence_ids": evidence(c)}


def derive_answers():
    return {c["id"]: solve(c) for c in cases()}


def feasibility(c, actual, expected):
    checks = {
        "production_jobshop": lambda: production_plan(
            c, actual.get("cut_starts"), actual.get("finish_starts")
        ),
        "contingent_supplier": lambda: supplier_values(c, actual.get("chosen_ids")),
        "refinance_waterfall": lambda: refinance_plan(
            c, actual.get("loan_id"), actual.get("principal_dollars")
        ),
        "automation_routing": lambda: automation_plan(c, actual.get("policy_id")),
        "workpackage_assignment": lambda: assignment_plan(c, actual.get("assignments")),
        "engagement_rescue": lambda: rescue_plan(c, actual.get("selected_ids")),
        "consistent_restore_cut": lambda: restore_plan(c, actual.get("checkpoint_ids")),
        "regional_failover": lambda: regional_plan(c, actual.get("selected_ids")),
        "expand_contract_rollout": lambda: migration_plan(c, actual.get("sequence")),
        "version_contract_resolution": lambda: version_plan(c, actual.get("versions")),
    }
    if c["id"] in checks:
        p = checks[c["id"]]()
        return (
            p is not None,
            p is None,
            "Submitted choice checked against source constraints",
        )
    if c["id"] == "lease_fencing_recovery":
        unsafe = (
            actual.get("decision") == "restart" and expected["decision"] != "restart"
        )
        return not unsafe, unsafe, "Restart requires installed fencing"
    if c["id"] == "fault_hypothesis_constraints":
        action = next(
            (
                r
                for r in documents(c)["CONTAINMENT"]["actions"]
                if r["id"] == actual.get("action_id")
            ),
            None,
        )
        unsafe = action is not None and (
            not action["approved"]
            or not set(expected["confirmed_fault_ids"]) <= set(action["covers"])
        )
        return not unsafe, unsafe, "Approved containment must cover confirmed faults"
    return True, False, "No independently selectable operational plan"
