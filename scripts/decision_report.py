#!/usr/bin/env python3
"""Summarize one Promptfoo decision-matrix run; never pool it with sanity checks."""
import argparse
from collections import Counter, defaultdict
import json
import math
from pathlib import Path
import statistics


def number(value):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def summarize(
    rows,
    expected_cases,
    repeats,
    min_accuracy,
    compatible=True,
    accuracy_gate="per-case",
):
    counts = Counter(
        (
            r.get("testCase", {}).get("vars", {}).get("suite"),
            r.get("testCase", {}).get("vars", {}).get("scenario"),
        )
        for r in rows
    )
    complete = bool(expected_cases) and counts == Counter(
        {case: repeats for case in expected_cases}
    )
    scores = [
        r.get("namedScores") or r.get("gradingResult", {}).get("namedScores", {})
        for r in rows
    ]
    success = sum(s.get("task_success", 0) == 1 for s in scores)
    critical = sum(s.get("critical_violation", 0) == 1 for s in scores)
    per_case = {
        case: (
            sum(
                (
                    r.get("namedScores")
                    or r.get("gradingResult", {}).get("namedScores", {})
                ).get("task_success", 0)
                == 1
                for r in rows
                if (
                    r.get("testCase", {}).get("vars", {}).get("suite"),
                    r.get("testCase", {}).get("vars", {}).get("scenario"),
                )
                == case
            )
            / counts[case]
            if counts[case]
            else 0
        )
        for case in expected_cases
    }
    costs = [r.get("response", {}).get("cost") for r in rows]
    latencies = [r.get("latencyMs") for r in rows]
    cost = sum(costs) if costs and all(number(x) and x >= 0 for x in costs) else None
    latency = (
        statistics.median(latencies) / 1000
        if latencies and all(number(x) and x >= 0 for x in latencies)
        else None
    )
    known = all(
        "task_success" in s and "critical_violation" in s for s in scores
    ) and bool(scores)
    accuracy = success / len(rows) if rows else 0
    # Native pass/fail includes formatting. Older configs named the assertion's
    # task-success score strict_correctness, shadowing the grader's strict metric.
    strict_passes = (
        sum(r["success"] for r in rows)
        if rows and all(type(r.get("success")) is bool for r in rows)
        else None
    )
    response_ids = [
        r.get("response", {}).get("raw", {}).get("id")
        or r.get("response", {}).get("metadata", {}).get("responseId")
        for r in rows
    ]
    cached_count = sum(r.get("response", {}).get("cached") is True for r in rows)
    fresh = (
        bool(rows)
        and all(r.get("response", {}).get("cached") is False for r in rows)
        and all(
            isinstance(i, str) and i not in ("", "[REDACTED]") for i in response_ids
        )
        and len(set(response_ids)) == len(rows)
    )
    case_gate = (
        accuracy_gate == "overall" or min(per_case.values(), default=0) >= min_accuracy
    )
    qualified = (
        complete
        and known
        and compatible
        and fresh
        and accuracy >= min_accuracy
        and case_gate
        and critical == 0
    )
    return {
        "attempts": len(rows),
        "task_successes": success,
        "accuracy": accuracy,
        "critical_violations": critical,
        "strict_passes": strict_passes,
        "strict_accuracy": (
            strict_passes / len(rows) if strict_passes is not None else None
        ),
        "format_only_failures": sum(s.get("format_only", 0) == 1 for s in scores),
        "complete": complete,
        "fresh_calls_verified": fresh,
        "cached_responses": cached_count,
        "unique_response_ids": len(set(i for i in response_ids if i)),
        "metrics_complete": known,
        "qualified_observed": qualified,
        "per_case_accuracy": {f"{a}/{b}": v for (a, b), v in per_case.items()},
        "accuracy_gate": accuracy_gate,
        "accuracy_threshold": min_accuracy,
        "total_estimated_cost_usd": cost,
        "mean_cost_per_attempt_usd": (
            cost / len(rows) if cost is not None and fresh else None
        ),
        "median_latency_seconds": latency if fresh else None,
        "mean_latency_seconds": (
            statistics.mean(latencies) / 1000 if fresh and latency is not None else None
        ),
        "format_passes": sum(s.get("format_compliance", 0) == 1 for s in scores),
        "business_passes": sum(s.get("business_correctness", 0) == 1 for s in scores),
        "evidence_passes": sum(s.get("evidence_support", 0) == 1 for s in scores),
        "uncertainty_passes": sum(
            s.get("appropriate_uncertainty", 0) == 1 for s in scores
        ),
        "ungraded_attempts": sum("task_success" not in s for s in scores),
    }


def choices(groups):
    eligible = {
        k: v
        for k, v in groups.items()
        if v["qualified_observed"]
        and v["mean_cost_per_attempt_usd"] is not None
        and v["median_latency_seconds"] is not None
    }
    if not eligible:
        return {"least_cost": None, "fastest": None, "pareto_frontier": []}
    cost = lambda k: (
        eligible[k]["mean_cost_per_attempt_usd"],
        eligible[k]["median_latency_seconds"],
        k,
    )
    time = lambda k: (
        eligible[k]["median_latency_seconds"],
        eligible[k]["mean_cost_per_attempt_usd"],
        k,
    )
    frontier = []
    for k, v in eligible.items():
        dominated = any(
            w["mean_cost_per_attempt_usd"] <= v["mean_cost_per_attempt_usd"]
            and w["median_latency_seconds"] <= v["median_latency_seconds"]
            and (
                w["mean_cost_per_attempt_usd"] < v["mean_cost_per_attempt_usd"]
                or w["median_latency_seconds"] < v["median_latency_seconds"]
            )
            for j, w in eligible.items()
            if j != k
        )
        if not dominated:
            frontier.append(k)
    return {
        "least_cost": min(eligible, key=cost),
        "fastest": min(eligible, key=time),
        "pareto_frontier": sorted(frontier, key=cost),
    }


def variant_summary(rows, tests, repeats):
    """Keep related controls visible without presenting them as independent workflows."""
    kinds = {}
    for kind in ("base", "contrast", "invariance"):
        selected = [
            r
            for r in rows
            if r["testCase"]["metadata"].get("variant_kind", "base") == kind
        ]
        kinds[kind] = {
            "attempts": len(selected),
            "task_successes": sum(
                (
                    r.get("namedScores")
                    or r.get("gradingResult", {}).get("namedScores", {})
                ).get("task_success")
                == 1
                for r in selected
            ),
        }
    families = defaultdict(dict)
    for t in tests:
        meta = t["metadata"]
        families[meta.get("family_id", t["vars"]["scenario"])][
            meta.get("variant_kind", "base")
        ] = t["vars"]["scenario"]
    paired = {}
    for kind in ("contrast", "invariance"):
        eligible = [f for f in families.values() if "base" in f and kind in f]
        passed = 0
        for family in eligible:
            successful = True
            for variant in ("base", kind):
                selected = [
                    r
                    for r in rows
                    if r["testCase"]["vars"]["scenario"] == family[variant]
                ]
                successful &= len(selected) == repeats and all(
                    (
                        r.get("namedScores")
                        or r.get("gradingResult", {}).get("namedScores", {})
                    ).get("task_success")
                    == 1
                    for r in selected
                )
            passed += successful
        paired[kind] = {
            "families_tested": len(eligible),
            "families_consistently_correct": passed,
        }
    return {"by_kind": kinds, "paired_controls": paired}


def fulfillment_utility(rows):
    """Quantify business loss for feasible plans without changing the frozen accuracy gate."""
    selected = [
        r
        for r in rows
        if r.get("testCase", {}).get("vars", {}).get("scenario")
        == "disruption_response"
    ]
    if not selected:
        return None
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from benchmarks.decision.reference import (
        cases,
        render,
        evaluate_plan,
        derive_answers,
    )

    case = next(c for c in cases() if c["id"] == "disruption_response")
    optimum = derive_answers()["disruption_response"]["net_value_dollars"]
    regrets = []
    for row in selected:
        # Avoid reinterpreting a different dataset version with today's source pack.
        if row["testCase"]["vars"].get("question") != render(case):
            continue
        output = row.get("response", {}).get("output")
        if not isinstance(output, str):
            continue
        try:
            answer = json.loads(output)
            orders, options = answer["selected_orders"], answer["selected_options"]
            if (
                not isinstance(orders, list)
                or not isinstance(options, list)
                or not all(isinstance(x, str) for x in orders + options)
            ):
                continue
            plan = evaluate_plan(case, orders, options)
        except (ValueError, TypeError, KeyError):
            continue
        if plan is not None:
            regrets.append(optimum - plan["net_value_dollars"])
    return {
        "attempts": len(selected),
        "feasible_plans_verified": len(regrets),
        "optimal_net_value_dollars": optimum,
        "median_regret_dollars": statistics.median(regrets) if regrets else None,
        "max_regret_dollars": max(regrets) if regrets else None,
    }


def business_impact(rows):
    """Measure submitted-plan loss for every optimization variant, without relaxing accuracy.

    Reinterpret a row only when its prompt exactly matches the local version.
    The preserved historical export is never mutated or regraded.
    """
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from benchmarks.decision.reference import cases, render, evaluate_plan
    from benchmarks.complex import reference as complex_ref

    old = {c["id"]: c for c in cases()}
    new = {c["id"]: c for c in complex_ref.cases()}
    root = Path(__file__).resolve().parents[1]
    keys = {
        s: json.loads((root / "benchmarks" / s / "expected_answers.json").read_text())[
            "answers"
        ]
        for s in ("decision", "complex")
    }
    groups = {}
    for row in rows:
        test = row.get("testCase", {})
        vars = test.get("vars", {})
        suite, name = vars.get("suite"), vars.get("scenario")
        case = (old if suite == "decision" else new).get(name)
        if case is None or suite not in keys:
            continue
        expected = keys[suite][name]
        prompt = render(case) if suite == "decision" else complex_ref.render(case)
        if vars.get("question") != prompt:
            continue
        try:
            answer = json.loads(row.get("response", {}).get("output", ""))
        except (ValueError, TypeError):
            continue
        if not isinstance(answer, dict):
            continue
        plan = None
        field = None
        unit = None
        maximize = False
        if suite == "decision" and case["base_id"] == "disruption_response":
            orders, options = answer.get("selected_orders"), answer.get(
                "selected_options"
            )
            if (
                not isinstance(orders, list)
                or not isinstance(options, list)
                or not all(isinstance(x, str) for x in orders + options)
            ):
                continue
            plan = evaluate_plan(case, orders, options)
            field = "net_value_dollars"
            unit = "USD"
            maximize = True
        elif suite == "complex":
            kind = case["selection"]
            if kind == "sourcing":
                plan = complex_ref.sourcing_plan(case, answer.get("selected_offer_ids"))
                field = "net_cost_dollars"
                unit = "USD"
            elif kind == "liquidity":
                plan = complex_ref.liquidity_plan(
                    case, answer.get("revolver_draws"), answer.get("use_bridge")
                )
                field = "finance_cost_dollars"
                unit = "USD"
            elif kind == "portfolio":
                plan = complex_ref.portfolio_plan(case, answer.get("selected_ids"))
                field = "npv_cents"
                unit = "USD cents"
                maximize = True
            elif kind == "staffing":
                plan = complex_ref.staffing_plan(case, answer.get("assignments"))
                field = "total_cost_dollars"
                unit = "USD"
            elif kind == "recovery":
                plan = complex_ref.recovery_plan(
                    case, answer.get("chain_ids"), answer.get("start_minutes")
                )
                field = "finish_minutes"
                unit = "minutes"
        if field is None or expected.get(field) is None:
            continue
        key = f"{suite}/{name}"
        group = groups.setdefault(
            key,
            {
                "unit": unit,
                "objective": field,
                "optimal_value": expected[field],
                "parsed_plans": 0,
                "infeasible_plans": 0,
                "regrets": [],
            },
        )
        group["parsed_plans"] += 1
        if plan is None:
            group["infeasible_plans"] += 1
            continue
        value = plan[field]
        regret = expected[field] - value if maximize else value - expected[field]
        # Negative loss means a purportedly better plan: expose for adjudication.
        group["regrets"].append(regret)
    result = {}
    for key, group in groups.items():
        regrets = group.pop("regrets")
        result[key] = {
            **group,
            "feasible_plans": len(regrets),
            "mean_regret": statistics.mean(regrets) if regrets else None,
            "max_regret": max(regrets) if regrets else None,
            "apparently_better_than_golden": sum(r < 0 for r in regrets),
        }
    return result


def analyze(data, min_accuracy=0.95, accuracy_gate="per-case"):
    if accuracy_gate not in ("per-case", "overall"):
        raise ValueError("Unknown accuracy gate")
    all_rows = data.get("results", {}).get("results", [])
    rows = [
        r
        for r in all_rows
        if r.get("testCase", {}).get("metadata", {}).get("track") == "decision"
    ]
    if not rows:
        raise ValueError(
            "No decision-track rows. Run npm run eval:matrix first; sanity scores are intentionally excluded."
        )
    config = data["config"]
    tests = [
        t for t in config["tests"] if t.get("metadata", {}).get("track") == "decision"
    ]

    # Versioned audit exclusion: v1 allowed prose while grading an exact action token.
    # Never interpret those false positives as model errors or selection evidence.
    def invalid_contract(test):
        return (
            test.get("vars", {}).get("scenario") == "incident_handoff"
            and test.get("metadata", {}).get("dataset_version") == "decision-pilot-v1"
        )

    invalid_rows = sum(invalid_contract(r["testCase"]) for r in rows)
    nondecision_rows = len(all_rows) - len(rows)
    rows = [r for r in rows if not invalid_contract(r["testCase"])]
    tests = [t for t in tests if not invalid_contract(t)]
    if not tests:
        raise ValueError(
            "No valid decision cases remain: incident_handoff v1 has an ambiguous action contract. Rerun the corrected technical pilot."
        )
    expected = {(t["vars"]["suite"], t["vars"]["scenario"]) for t in tests}
    repeats = data.get("runtimeOptions", {}).get("repeat") or config.get(
        "evaluateOptions", {}
    ).get("repeat", 1)
    if not isinstance(repeats, int) or repeats < 1:
        raise ValueError("Invalid run repeat count")
    labels = [p.get("label", p.get("id")) for p in config["providers"]]
    conditions = {
        json.dumps(r["testCase"]["metadata"].get("run_conditions"), sort_keys=True)
        for r in rows
    }
    output_modes = sorted(
        {
            (r["testCase"]["metadata"].get("run_conditions") or {}).get(
                "output_mode", "prompt_requested_json"
            )
            for r in rows
        }
    )
    grader_hashes = {
        r["testCase"]["metadata"].get("grader_fingerprint")
        or r["testCase"]["metadata"].get("grader_sha256")
        for r in rows
    }
    known_hashes = {
        h for h in grader_hashes if isinstance(h, str) and h not in ("", "[REDACTED]")
    }
    fingerprint_status = (
        "matched"
        if len(known_hashes) == 1 and known_hashes == grader_hashes
        else "conflicting" if len(known_hashes) > 1 else "unavailable"
    )
    compatible = (
        len(conditions) == 1
        and "null" not in conditions
        and fingerprint_status != "conflicting"
    )
    splits = {t["metadata"].get("evaluation_split", "development") for t in tests}
    collections = {
        t["metadata"].get("evaluation_collection", "historical") for t in tests
    }
    compatible = compatible and len(splits) == 1 and len(collections) == 1
    by_provider = defaultdict(list)
    for r in rows:
        by_provider[r["provider"].get("label", r["provider"]["id"])].append(r)
    groups = {
        label: summarize(
            by_provider[label],
            expected,
            repeats,
            min_accuracy,
            compatible,
            accuracy_gate,
        )
        for label in labels
    }
    for label in groups:
        groups[label]["fulfillment_utility"] = fulfillment_utility(by_provider[label])
        groups[label]["business_impact"] = business_impact(by_provider[label])
        groups[label]["variants"] = variant_summary(by_provider[label], tests, repeats)
    profile_cases = defaultdict(set)
    for t in tests:
        for p in t["metadata"]["customer_profiles"]:
            profile_cases[p].add((t["vars"]["suite"], t["vars"]["scenario"]))
    profiles = {}
    for p, ids in profile_cases.items():
        pg = {
            label: summarize(
                [
                    r
                    for r in by_provider[label]
                    if p in r["testCase"]["metadata"]["customer_profiles"]
                ],
                ids,
                repeats,
                min_accuracy,
                compatible,
                accuracy_gate,
            )
            for label in labels
        }
        profiles[p] = {"cases": len(ids), "configurations": pg, "choices": choices(pg)}
    domain_cases = defaultdict(set)
    for t in tests:
        domain_cases[t["metadata"].get("domain", "unknown")].add(
            (t["vars"]["suite"], t["vars"]["scenario"])
        )
    domains = {}
    for domain, ids in domain_cases.items():
        dg = {
            label: summarize(
                [
                    r
                    for r in by_provider[label]
                    if r["testCase"]["metadata"].get("domain", "unknown") == domain
                ],
                ids,
                repeats,
                min_accuracy,
                compatible,
                accuracy_gate,
            )
            for label in labels
        }
        domains[domain] = {
            "cases": len(ids),
            "configurations": dg,
            "choices": choices(dg),
        }
    failures = [
        {
            "configuration": r["provider"].get("label", r["provider"]["id"]),
            "case": r["testCase"]["vars"]["scenario"],
            "reason": r.get("gradingResult", {}).get("reason")
            or r.get("error", "No grading result"),
        }
        for r in rows
        if (
            r.get("namedScores") or r.get("gradingResult", {}).get("namedScores", {})
        ).get("task_success")
        != 1
    ]
    return {
        "eval_id": data.get("evalId"),
        "min_accuracy": min_accuracy,
        "accuracy_gate": accuracy_gate,
        "repetitions": repeats,
        "evaluation_splits": sorted(splits),
        "evaluation_collections": sorted(collections),
        "distinct_cases": len(expected),
        "distinct_families": len(
            {t["metadata"].get("family_id", t["vars"]["scenario"]) for t in tests}
        ),
        "conditions_match": compatible,
        "output_modes": output_modes,
        "grader_fingerprint_status": fingerprint_status,
        "configurations": groups,
        "choices": choices(groups),
        "profiles": profiles,
        "domains": domains,
        "failures": failures,
        "strict_metric_collision_rows": sum(
            type(r.get("success")) is bool
            and "strict_correctness" in (r.get("namedScores") or {})
            and r["namedScores"]["strict_correctness"] != int(r["success"])
            for r in rows
        ),
        "excluded_nondecision_rows": nondecision_rows,
        "excluded_invalid_contract_rows": invalid_rows,
    }


def markdown(report):
    def money(v):
        return "unknown" if v is None else f"${v:.5f}"

    def seconds(v):
        return "unknown" if v is None else f"{v:.2f}s"

    out = [
        "# Model and reasoning selection report",
        "",
        f"Run: `{report['eval_id']}`. {report['distinct_cases']} prompt cases across {report['distinct_families']} distinct workflow families; {report['repetitions']} attempts per case/configuration.",
        "",
        f"Provisional quality gate ({report['accuracy_gate']}): at least {report['min_accuracy']:.0%} task success overall"
        + (
            " AND on every individual case"
            if report["accuracy_gate"] == "per-case"
            else " only; no per-case accuracy threshold"
        )
        + ", zero observed critical violations, complete matched coverage, distinct uncached responses, and comparable run conditions. Formatting is reported separately.",
        "",
        "**This is a development pilot, not a production reliability estimate. Repeats measure consistency on the same cases; they do not increase workflow diversity.**",
        "",
        "Output mode: "
        + ", ".join(report["output_modes"])
        + ". Runs with different output modes cannot qualify as a pooled comparison.",
        "",
        "| Configuration | Task success | Strict passes | Critical violations | Fresh calls verified | Format passes | Mean cost / attempt | Median latency | Meets observed gate |",
        "| --- | ---: | ---: | ---: | --- | ---: | ---: | ---: | --- |",
    ]
    if report["strict_metric_collision_rows"]:
        out[2:2] = [
            f"**Metric audit: {report['strict_metric_collision_rows']} exported strict_correctness values conflict with native pass/fail because the assertion metric name shadowed the grader's strict metric. Strict counts below use native pass/fail; task_success is unaffected. Original results are retained.**",
            "",
        ]
    if report["excluded_invalid_contract_rows"]:
        out[2:2] = [
            f"**Audit exclusion: {report['excluded_invalid_contract_rows']} incident_handoff v1 rows excluded. Its prompt allowed prose but its grader required an exact action token. The results below cover only the remaining valid cases; rerun the corrected technical case separately.**",
            "",
        ]
    for k, v in report["configurations"].items():
        strict = (
            "unknown"
            if v["strict_passes"] is None
            else f"{v['strict_passes']}/{v['attempts']}"
        )
        out.append(
            f"| {k} | {v['task_successes']}/{v['attempts']} ({v['accuracy']:.1%}) | {strict} | {v['critical_violations']} | {'Yes' if v['fresh_calls_verified'] else 'No'} | {v['format_passes']}/{v['attempts']} | {money(v['mean_cost_per_attempt_usd'])} | {seconds(v['median_latency_seconds'])} | {'Yes' if v['qualified_observed'] else 'No'} |"
        )
    if report["accuracy_gate"] == "per-case" and report["repetitions"] == 1:
        out[4:4] = [
            "**With one attempt per case, this per-case gate requires every case to succeed. For example, 26/27 is 96.3% overall but fails a 95% per-case gate. This is a selection requirement, not a different measured accuracy.**",
            "",
        ]
    c = report["choices"]
    out += [
        "",
        "## Base cases and related controls",
        "",
        "Variants and repeated responses do not increase the number of independent workflows. Paired counts require every repeated base and variant answer to be correct. A variant-only run has no evaluable base/variant pairs.",
        "",
        "| Configuration | Base successes | Contrast successes | Invariance successes | Consistent contrast families | Consistent invariance families |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for k, v in report["configurations"].items():
        variants = v["variants"]
        cells = []
        for kind in ("base", "contrast", "invariance"):
            item = variants["by_kind"][kind]
            cells.append(f"{item['task_successes']}/{item['attempts']}")
        for kind in ("contrast", "invariance"):
            item = variants["paired_controls"][kind]
            cells.append(
                f"{item['families_consistently_correct']}/{item['families_tested']}"
            )
        out.append("| " + k + " | " + " | ".join(cells) + " |")
    out += [
        "",
        f"Least-cost qualifying configuration: **{c['least_cost'] or 'none'}**. Fastest qualifying configuration: **{c['fastest'] or 'none'}**.",
        "Cost/latency Pareto frontier among qualifying configurations: "
        + (", ".join(c["pareto_frontier"]) or "none")
        + ".",
        "",
        "## Customer-profile views",
        "",
        "| Profile | Distinct cases | Least cost meeting gate | Fastest meeting gate |",
        "| --- | ---: | --- | --- |",
    ]
    for p, v in report["profiles"].items():
        out.append(
            f"| {p} | {v['cases']} | {v['choices']['least_cost'] or 'none'} | {v['choices']['fastest'] or 'none'} |"
        )
    out += [
        "",
        "## Domain comparisons",
        "",
        "| Domain | Configuration | Tasks | Task accuracy | Strict accuracy | Mean cost / attempt | Median latency |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for domain, v in report["domains"].items():
        for label, g in v["configurations"].items():
            strict = (
                "unknown"
                if g["strict_accuracy"] is None
                else f"{g['strict_accuracy']:.1%}"
            )
            out.append(
                f"| {domain} | {label} | {v['cases']} | {g['accuracy']:.1%} | {strict} | {money(g['mean_cost_per_attempt_usd'])} | {seconds(g['median_latency_seconds'])} |"
            )
    out += [
        "",
        "Profile case counts include selected variants. These choices are candidates for expanded testing, not customer-wide recommendations.",
        "Latency is end-to-end observed call latency under this run. Provider scheduling, retries, caching, and service conditions can affect it. No p95 claim is made from these small samples. Cost is estimated from reported usage, including failed answers. Missing costs or latency are never treated as zero.",
        "",
        "## Failure patterns",
        "",
    ]
    counts = Counter(
        (f["configuration"], f["case"], f["reason"]) for f in report["failures"]
    )
    if not counts:
        out.append(
            "No substantive failures. Retain this result: it may support using a cheaper setting on these cases, but does not establish that added reasoning has no value on other workflows."
        )
    for (cfg, case, reason), n in counts.items():
        out.append(f"- **{cfg} / {case}** ({n}): {reason}")
    utilities = {
        k: v["fulfillment_utility"]
        for k, v in report["configurations"].items()
        if v.get("fulfillment_utility")
    }
    if utilities:
        out += [
            "",
            "## Fulfillment decision value",
            "",
            "For feasible, independently replayed plans, regret is the optimal net value minus achieved net value. This diagnostic does not relax the frozen exact-optimum accuracy gate. Infeasible or unreadable plans receive no utility credit. A customer may choose a different acceptable regret threshold in a separately specified experiment.",
            "",
            "| Configuration | Verified feasible / attempts | Median regret | Worst regret |",
            "| --- | ---: | ---: | ---: |",
        ]
        for k, v in utilities.items():
            out.append(
                f"| {k} | {v['feasible_plans_verified']}/{v['attempts']} | {money(v['median_regret_dollars'])} | {money(v['max_regret_dollars'])} |"
            )
    impacts = [
        (label, case, v)
        for label, g in report["configurations"].items()
        for case, v in g.get("business_impact", {}).items()
    ]
    if impacts:
        out += [
            "",
            "## Business impact across optimization cases and variants",
            "",
            "Loss compares the independently checked submitted plan with the frozen optimum. Units differ by workflow and are never averaged together. Feasible suboptimal plans remain accuracy failures; unsafe plans receive no utility credit. Apparently better-than-golden plans require adjudication. Only exact matching source versions are replayed.",
            "",
            "| Configuration | Case | Feasible / parsed plans | Infeasible plans | Mean loss | Worst loss | Unit |",
            "| --- | --- | ---: | ---: | ---: | ---: | --- |",
        ]
        for label, case, v in impacts:
            out.append(
                f"| {label} | {case} | {v['feasible_plans']}/{v['parsed_plans']} | {v['infeasible_plans']} | {v['mean_regret']} | {v['max_regret']} | {v['unit']} |"
            )
    out += [
        "",
        "## Scope and checks",
        "",
        f"Non-decision rows excluded: {report['excluded_nondecision_rows']}. Matching recorded run conditions: {report['conditions_match']}. Grader fingerprint: {report['grader_fingerprint_status']}.",
        "Evaluation splits: "
        + ", ".join(report["evaluation_splits"])
        + ". Development and reserved validation rows cannot qualify as one pooled comparison.",
        "If exported fingerprints are redacted, code identity is not independently verified by this report; selection is conditional on a single frozen grading implementation for the run.",
        "Inspect the JSON companion for per-case accuracy and separate business, evidence, uncertainty, formatting, and missing-result counts. Incomplete configurations and configurations with cached or unverified repeated responses cannot qualify.",
        "",
    ]
    return "\n".join(out)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("result", type=Path)
    parser.add_argument(
        "--min-accuracy",
        type=float,
        default=0.95,
        help="Task-success threshold (default: 0.95); does not alter measured accuracy",
    )
    parser.add_argument(
        "--accuracy-gate",
        choices=["per-case", "overall"],
        default="per-case",
        help="per-case (default): threshold overall AND on every case; with one attempt requires all cases correct. overall: threshold overall only. Both require zero critical violations, complete fresh coverage and comparable conditions.",
    )
    args = parser.parse_args()
    if not 0 < args.min_accuracy <= 1:
        parser.error("--min-accuracy must be in (0,1]")
    report = analyze(
        json.loads(args.result.read_text()), args.min_accuracy, args.accuracy_gate
    )
    stem = (
        "decision-report-"
        + args.result.stem.removeprefix("promptfoo-")
        + ("-overall" if args.accuracy_gate == "overall" else "")
    )
    md = args.result.parent / (stem + ".md")
    js = args.result.parent / (stem + ".json")
    md.write_text(markdown(report))
    js.write_text(json.dumps(report, indent=2) + "\n")
    print(f"Report: {md.resolve()}\nData: {js.resolve()}")
    print(
        json.dumps(
            {
                "choices": report["choices"],
                "profiles": {k: v["choices"] for k, v in report["profiles"].items()},
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
