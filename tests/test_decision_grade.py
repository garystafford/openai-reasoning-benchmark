import copy
import json
import unittest
from benchmarks.decision.reference import derive_answers
from evals.decision_grade import grade_decision


class DecisionGradingTests(unittest.TestCase):
    def grade(self, name, answer, fenced=False):
        output = json.dumps(answer)
        if fenced:
            output = "```json\n" + output + "\n```"
        return grade_decision(output, {"vars": {"scenario": name}})

    def test_reference_answers_and_format_are_separate(self):
        for name, answer in derive_answers().items():
            result = self.grade(name, answer)
            self.assertTrue(result["pass"], result)
            fenced = self.grade(name, answer, True)
            self.assertFalse(fenced["pass"])
            self.assertEqual(fenced["namedScores"]["task_success"], 1)
            self.assertEqual(fenced["namedScores"]["format_compliance"], 0)

    def test_suboptimal_feasible_plan_is_not_a_constraint_violation(self):
        a = copy.deepcopy(derive_answers()["disruption_response"])
        a["selected_orders"].remove("O8")
        result = self.grade("disruption_response", a)["namedScores"]
        self.assertEqual(result["constraint_compliance"], 1)
        self.assertEqual(result["critical_violation"], 0)
        self.assertEqual(result["task_success"], 0)

    def test_infeasible_plan_is_critical(self):
        a = copy.deepcopy(derive_answers()["disruption_response"])
        a["selected_options"] = ["S1", "S2"]
        result = self.grade("disruption_response", a)["namedScores"]
        self.assertEqual(result["constraint_compliance"], 0)
        self.assertEqual(result["critical_violation"], 1)

    def test_correct_numbers_do_not_excuse_unsupported_approval(self):
        a = copy.deepcopy(derive_answers()["diligence_reconciliation"])
        a["recommendation"] = "approve"
        result = self.grade("diligence_reconciliation", a)["namedScores"]
        self.assertEqual(result["business_correctness"], 1)
        self.assertEqual(result["appropriate_uncertainty"], 0)
        self.assertEqual(result["critical_violation"], 1)
        self.assertEqual(result["task_success"], 0)

    def test_correct_numbers_do_not_excuse_wrong_evidence(self):
        a = copy.deepcopy(derive_answers()["diligence_reconciliation"])
        a["evidence_ids"].append("LOAN-DRAFT")
        result = self.grade("diligence_reconciliation", a)["namedScores"]
        self.assertEqual(result["business_correctness"], 1)
        self.assertEqual(result["evidence_support"], 0)
        self.assertEqual(result["task_success"], 0)

    def test_unknown_cause_and_safe_retry_are_critical(self):
        a = copy.deepcopy(derive_answers()["incident_handoff"])
        a["confirmed_root_cause"] = "H2"
        result = self.grade("incident_handoff", a)["namedScores"]
        self.assertEqual(result["business_correctness"], 1)
        self.assertEqual(result["critical_violation"], 1)

    def test_bad_shapes_are_failures_not_grader_crashes(self):
        for value in [
            None,
            [],
            {},
            {"selected_orders": [{}], "selected_options": [None]},
            {"evidence_ids": [dict()]},
        ]:
            self.assertFalse(self.grade("disruption_response", value)["pass"])

    def test_unordered_fulfillment_ids_are_format_only_not_semantic_failures(self):
        a = copy.deepcopy(derive_answers()["disruption_response"])
        for key in (
            "selected_orders",
            "selected_options",
            "unfilled_optional_orders",
            "evidence_ids",
        ):
            a[key].reverse()
        result = self.grade("disruption_response", a)["namedScores"]
        self.assertEqual(result["task_success"], 1)
        self.assertEqual(result["format_compliance"], 0)
        self.assertEqual(result["format_only"], 1)
