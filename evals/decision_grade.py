"""Separate business correctness, feasibility, evidence, uncertainty, and formatting."""

import json
import re
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from benchmarks.decision.reference import cases
from evals.decision_constraints import UNCERTAINTY_FIELDS, constraints

ROOT = Path(__file__).resolve().parents[1]


@lru_cache(maxsize=None)
def fixtures(suite="decision"):
    if suite == "complex":
        from benchmarks.complex.reference import cases as complex_cases

        case_list = complex_cases()
    elif suite == "reasoning":
        from benchmarks.reasoning.reference import cases as reasoning_cases

        case_list = reasoning_cases()
    else:
        case_list = cases()
    return (
        {c["id"]: c for c in case_list},
        json.loads((ROOT / f"benchmarks/{suite}/expected_answers.json").read_text())[
            "answers"
        ],
    )


def same(actual, expected, field=None):
    if isinstance(expected, bool) or expected is None:
        return type(actual) is type(expected) and actual == expected
    if isinstance(expected, (int, float, Decimal)):
        return type(actual) in (int, float, Decimal) and actual == expected
    if isinstance(expected, dict):
        return (
            isinstance(actual, dict)
            and actual.keys() == expected.keys()
            and all(same(actual[k], v, k) for k, v in expected.items())
        )
    if isinstance(expected, list):
        if not isinstance(actual, list) or len(actual) != len(expected):
            return False
        if field not in ("sequence", "chain_ids", "settlement_sequence") and all(
            isinstance(x, str) for x in actual + expected
        ):
            return len(set(actual)) == len(actual) and set(actual) == set(expected)
        return all(same(a, b) for a, b in zip(actual, expected))
    return type(actual) is type(expected) and actual == expected


def shape(actual, expected, field=None):
    if isinstance(expected, dict):
        return (
            isinstance(actual, dict)
            and actual.keys() == expected.keys()
            and all(shape(actual[k], v, k) for k, v in expected.items())
        )
    if isinstance(expected, list):
        return (
            isinstance(actual, list)
            and all(isinstance(x, str) for x in actual)
            and (
                len(actual) == len(set(actual))
                if field in ("sequence", "chain_ids", "settlement_sequence")
                else actual == sorted(set(actual))
            )
        )
    return type(actual) is type(expected)


def grade_decision(output, context):
    suite = context["vars"].get("suite", "decision")
    name = context["vars"]["scenario"]
    case_map, keys = fixtures(suite)
    case = case_map[name]
    expected = keys[name]
    raw = (context.get("providerResponse") or {}).get("raw") or {}
    if not isinstance(raw, dict):
        raw = {}
    refused = raw.get("status") == "refused" or any(
        p.get("type") == "refusal"
        for i in raw.get("output", [])
        if i.get("type") == "message"
        for p in i.get("content", [])
    )
    truncated = raw.get("status") == "incomplete" and (
        raw.get("incomplete_details") or {}
    ).get("reason") in ("max_output_tokens", "max_tokens")
    bare = True
    actual = None
    if isinstance(output, str):
        text = output.strip()
        fenced = re.fullmatch(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
        if fenced:
            bare = False
            text = fenced.group(1)
        try:

            def invalid_constant(value):
                raise ValueError(value)

            def unique_object(pairs):
                result = {}
                for k, v in pairs:
                    if k in result:
                        raise ValueError("Duplicate JSON key")
                    result[k] = v
                return result

            actual = json.loads(
                text,
                parse_float=Decimal,
                parse_constant=invalid_constant,
                object_pairs_hook=unique_object,
            )
        except (ValueError, TypeError):
            pass
    parsed = isinstance(actual, dict)
    actual = actual if parsed else {}
    blocked = refused or truncated or not parsed
    uncertainty_fields = case.get(
        "uncertainty_fields", UNCERTAINTY_FIELDS.get(case["base_id"], [])
    )
    substantive = [
        k for k in expected if k != "evidence_ids" and k not in uncertainty_fields
    ]
    correct_fields = [
        k for k in substantive if k in actual and same(actual[k], expected[k], k)
    ]
    business = not blocked and len(correct_fields) == len(substantive)
    uncertainty = not blocked and all(
        k in actual and same(actual[k], expected[k], k) for k in uncertainty_fields
    )
    evidence = actual.get("evidence_ids")
    valid_evidence = isinstance(evidence, list) and all(
        isinstance(x, str) for x in evidence
    )
    allowed = set(expected["evidence_ids"]) | (
        {"DEPLOYMENT"} if case["base_id"] == "incident_handoff" else set()
    )
    evidence_pass = (
        not blocked
        and valid_evidence
        and len(evidence) == len(set(evidence))
        and set(expected["evidence_ids"]) <= set(evidence) <= allowed
    )
    feasible = False
    violation = False
    detail = ""
    if parsed and not refused and not truncated:
        try:
            if suite == "complex":
                from benchmarks.complex.reference import feasibility

                feasible, violation, detail = feasibility(case, actual, expected)
            elif suite == "reasoning":
                from benchmarks.reasoning.reference import feasibility

                feasible, violation, detail = feasibility(case, actual, expected)
            else:
                feasible, violation, detail = constraints(case, actual, expected)
        except (TypeError, KeyError, ValueError, IndexError, AttributeError):
            feasible, violation, detail = False, False, "Malformed submitted choice"
    format_pass = not blocked and bare and shape(actual, expected)
    task = business and feasible and evidence_pass and uncertainty
    passed = task and format_pass
    failed = []
    for label, ok in [
        ("answer", business),
        ("constraints", feasible),
        ("evidence", evidence_pass),
        ("uncertainty", uncertainty),
        ("format", format_pass),
    ]:
        if not ok:
            failed.append(label)
    outcome = (
        "policy_refusal"
        if refused
        else (
            "truncated"
            if truncated
            else (
                "malformed"
                if not parsed
                else "strict" if passed else "format_only" if task else "semantic_error"
            )
        )
    )
    reason = f"{outcome}: " + (
        "All decision checks passed" if passed else "Failed " + ", ".join(failed)
    )
    wrong = [k for k in substantive if k not in correct_fields]
    if wrong:
        reason += "; mismatched fields: " + ", ".join(wrong)
    if detail:
        reason += "; " + detail
    return {
        "pass": passed,
        "score": int(task),
        "reason": reason,
        "namedScores": {
            "task_success": int(task),
            "business_correctness": int(business),
            "answer_field_accuracy": (
                len(correct_fields) / len(substantive) if not blocked else 0
            ),
            "constraint_compliance": int(feasible),
            "critical_violation": int(violation),
            "evidence_support": int(evidence_pass),
            "appropriate_uncertainty": int(uncertainty),
            "format_compliance": int(format_pass),
            "strict_correctness": int(passed),
            "recoverable_correctness": int(task),
            **{
                k: int(outcome == k)
                for k in (
                    "format_only",
                    "semantic_error",
                    "policy_refusal",
                    "truncated",
                    "malformed",
                )
            },
        },
    }
