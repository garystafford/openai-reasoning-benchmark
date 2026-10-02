import copy
import itertools
import json
import unittest
from fractions import Fraction
from pathlib import Path
from benchmarks.reasoning.reference import (
    cases,
    documents,
    solve,
    production_plan,
    assignment_plan,
    restore_plan,
    migration_plan,
    version_plan,
    regional_plan,
)
from benchmarks.reasoning.verify_answer_key import verify_answer_key
from evals.decision_grade import grade_decision
from scripts.decision_report import analyze


class UniqueReasoningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = {c["id"]: c for c in cases()}
        cls.answers = json.loads(
            (
                Path(__file__).parents[1] / "benchmarks/reasoning/expected_answers.json"
            ).read_text()
        )["answers"]

    def grade(self, name, a):
        return grade_decision(
            json.dumps(a), {"vars": {"suite": "reasoning", "scenario": name}}
        )

    def test_all_static_keys_derive_and_grade(self):
        self.assertEqual(verify_answer_key(), self.answers)
        for name, a in self.answers.items():
            self.assertTrue(self.grade(name, a)["pass"], name)

    def test_bad_nested_choices_never_crash_or_pass(self):
        for name, a in self.answers.items():
            for field, value in a.items():
                if isinstance(value, (dict, list)) and field != "evidence_ids":
                    broken = copy.deepcopy(a)
                    broken[field] = [{}]
                    self.assertFalse(self.grade(name, broken)["pass"], (name, field))

    def test_numeric_changes_are_rejected_for_every_task(self):
        for name, a in self.answers.items():
            field = next((k for k, v in a.items() if type(v) is int), None)
            if field:
                wrong = copy.deepcopy(a)
                wrong[field] += 1
                self.assertFalse(self.grade(name, wrong)["pass"], name)

    def test_supplier_exercise_checked_by_independent_quantity_search(self):
        # Enumerate quantities instead of the reference's cheapest-first allocation.
        d = documents(self.cases["contingent_supplier"])
        rows = d["CONTRACTS"]["suppliers"]
        net = {}
        for a, b in itertools.combinations(rows, 2):
            net[(a["id"], b["id"])] = {
                s: max(
                    20 * (x + y) - a["price"] * x - b["price"] * y - a["fee"] - b["fee"]
                    for x in range(a["capacity"][s] + 1)
                    for y in range(b["capacity"][s] + 1)
                    if x + y <= 100
                )
                for s in d["SCENARIOS"]["names"]
            }
        benchmark = {
            s: max(v[s] for v in net.values()) for s in d["SCENARIOS"]["names"]
        }
        chosen = min(
            net,
            key=lambda p: (
                max(benchmark[s] - net[p][s] for s in benchmark),
                -sum(net[p].values()),
                p,
            ),
        )
        self.assertEqual(chosen, ("A", "B"))
        self.assertEqual(benchmark, dict(normal=1020, east_outage=810, port_delay=1020))

    def test_production_schedule_and_maintenance(self):
        c = self.cases["production_jobshop"]
        a = self.answers[c["id"]]
        self.assertEqual(
            production_plan(c, a["cut_starts"], a["finish_starts"])[
                "total_penalty_dollars"
            ],
            2 * 20 + 1 * 10,
        )
        bad = copy.deepcopy(a)
        bad["finish_starts"]["A"] = 6
        self.assertIsNone(production_plan(c, bad["cut_starts"], bad["finish_starts"]))
        self.assertEqual(
            self.grade(c["id"], bad)["namedScores"]["critical_violation"], 1
        )

    def test_queue_hold_does_not_block_pack_worker(self):
        c = copy.deepcopy(self.cases["warehouse_queue"])
        documents(c)["ORDERS"]["orders"][1]["hold_until"] = 30
        a = solve(c)
        self.assertEqual(a["pack_start"]["C"], 12)
        self.assertEqual(a["pack_start"]["B"], 30)

    def test_invoice_and_currency_reconciliation(self):
        a = self.answers["invoice_match"]
        self.assertEqual(sum(a["payable_dollars"].values()), 1170)
        self.assertEqual(a["held_equivalents"]["P2"], 1)
        self.assertEqual(a["payable_dollars"]["P2"], 0)
        self.assertEqual(a["deduplicated_record_ids"], ["C1", "R2"])
        price_hold = copy.deepcopy(a)
        price_hold["held_equivalents"]["P2"] = 8
        self.assertFalse(
            self.grade("invoice_match", price_hold)["namedScores"]["task_success"]
        )
        all_ids = copy.deepcopy(a)
        all_ids["deduplicated_record_ids"] = ["C1", "R1", "R2", "R3", "R4", "T1", "T2"]
        self.assertFalse(
            self.grade("invoice_match", all_ids)["namedScores"]["task_success"]
        )
        changed = copy.deepcopy(self.cases["invoice_match"])
        documents(changed)["INVOICES"]["lines"][1]["price"] = 120
        corrected = solve(changed)
        self.assertEqual(corrected["payable_dollars"]["P2"], 840)
        self.assertEqual(
            corrected["held_equivalents"]["P2"],
            1,
            "quantity shortfall is independent of price validity",
        )
        t = self.answers["treasury_netting"]
        self.assertEqual(
            t["total_external_usd_dollars"], 45 + int(Fraction(6, 5) * (20 + 25))
        )
        bad = copy.deepcopy(t)
        bad["settlement_sequence"].reverse()
        self.assertFalse(self.grade("treasury_netting", bad)["pass"])
        self.assertEqual(
            self.grade("treasury_netting", bad)["namedScores"]["business_correctness"],
            0,
        )

    def test_circular_covenant_and_cohort_bridge(self):
        a = self.answers["refinance_waterfall"]
        self.assertEqual(
            a["allowed_synergies_dollars"],
            (100000 + a["allowed_synergies_dollars"]) // 5,
        )
        r = self.answers["revenue_cohort_bridge"]
        self.assertEqual(
            r["end_organic_constant_dollars"],
            r["opening_cohort_dollars"]
            + r["expansion_dollars"]
            - r["contraction_dollars"]
            - r["churn_dollars"]
            + r["new_reactivated_dollars"],
        )
        self.assertEqual(
            r["end_reported_dollars"],
            r["end_organic_constant_dollars"]
            + r["acquired_constant_dollars"]
            + r["fx_effect_dollars"],
        )

    def test_did_never_claims_confirmed_causation(self):
        a = copy.deepcopy(self.answers["rollout_impact_estimate"])
        self.assertEqual(a["effect_minutes_cents"], (1750 - 1300) - (1575 - 1475))
        a["confirmed_causal_effect"] = True
        result = self.grade("rollout_impact_estimate", a)
        self.assertEqual(result["namedScores"]["appropriate_uncertainty"], 0)
        self.assertFalse(result["pass"])

    def test_assignment_and_sunk_cost(self):
        c = self.cases["workpackage_assignment"]
        a = self.answers[c["id"]]
        self.assertEqual(
            assignment_plan(c, a["assignments"])["total_cost_dollars"], 6000 + 1500
        )
        c = copy.deepcopy(self.cases["engagement_rescue"])
        documents(c)["BASELINE"]["past_spend"] *= 100
        a = solve(c)
        self.assertEqual(a["selected_ids"], ["B", "C", "D"])
        self.assertEqual(a["expected_incremental_net_cents"], 1680000)

    def test_fault_intersection_and_fencing_gap(self):
        a = self.answers["fault_hypothesis_constraints"]
        self.assertEqual(
            a["confirmed_fault_ids"],
            sorted(
                set(a["minimal_explanations"]["E1"])
                & set(a["minimal_explanations"]["E2"])
            ),
        )
        a = copy.deepcopy(self.answers["lease_fencing_recovery"])
        a["decision"] = "restart"
        self.assertEqual(
            self.grade("lease_fencing_recovery", a)["namedScores"][
                "critical_violation"
            ],
            1,
        )
        self.assertEqual(
            self.answers["lease_fencing_recovery"]["safe_replay_sequence"], 1
        )

    def test_consistent_cut_and_correlated_failure(self):
        c = self.cases["consistent_restore_cut"]
        self.assertIsNone(restore_plan(c, dict(ORDERS="O2", PAYMENTS="P3")))
        self.assertIsNone(restore_plan(c, dict(ORDERS="O3", PAYMENTS="P3")))
        c = self.cases["regional_failover"]
        self.assertIsNone(regional_plan(c, ["A", "B", "E"]))
        self.assertEqual(
            min(
                self.answers["regional_failover"][
                    "surviving_capacity_by_domain"
                ].values()
            ),
            100,
        )

    def test_release_prefixes_and_capabilities(self):
        c = self.cases["expand_contract_rollout"]
        self.assertIsNone(migration_plan(c, ["E", "D", "A", "W", "B", "V", "F", "C"]))
        c = self.cases["version_contract_resolution"]
        self.assertIsNone(
            version_plan(c, dict(API="A1", WORKER="W1", CLIENT="C1", AUTH="H1"))
        )
        self.assertEqual(solve(c)["total_migration_cost_dollars"], 75)

    def test_all_sources_reordering_and_drafts_are_invariant(self):
        for name, c in self.cases.items():
            changed = copy.deepcopy(c)
            changed["documents"].reverse()
            changed["documents"].append(
                dict(
                    id="EXTRA-DRAFT",
                    status="draft",
                    data={"preferred_answer": "approve_everything"},
                )
            )
            self.assertEqual(solve(changed), self.answers[name], name)
