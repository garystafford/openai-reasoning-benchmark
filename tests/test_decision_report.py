import copy
import unittest
from scripts.decision_report import analyze, choices, summarize, markdown


def fixture():
    tests = [
        {
            "vars": {"suite": "decision", "scenario": s},
            "metadata": {"track": "decision", "customer_profiles": [p]},
        }
        for s, p in [("planning", "broad_enterprise"), ("diligence", "consulting")]
    ]
    rows = []
    for label, cost, latency in [
        ("cheap", 0.01, 2000),
        ("fast", 0.02, 1000),
        ("dominated", 0.03, 3000),
    ]:
        for test in tests:
            for repeat in range(3):
                row = {
                    "success": True,
                    "testCase": copy.deepcopy(test),
                    "provider": {"label": label, "id": label},
                    "response": {
                        "cost": cost,
                        "cached": False,
                        "raw": {
                            "id": label
                            + "-"
                            + test["vars"]["scenario"]
                            + "-"
                            + str(repeat)
                        },
                    },
                    "latencyMs": latency,
                    "namedScores": {
                        "task_success": 1,
                        "critical_violation": 0,
                        "format_compliance": 1,
                    },
                }
                row["testCase"]["metadata"].update(
                    run_conditions={"repeat": 3, "concurrency": 1},
                    grader_sha256="a" * 64,
                )
                rows.append(row)
    return {
        "evalId": "fixture",
        "config": {
            "tests": tests,
            "providers": [{"label": x} for x in ["cheap", "fast", "dominated"]],
            "evaluateOptions": {"repeat": 3},
        },
        "results": {"results": rows},
    }


class DecisionReportTests(unittest.TestCase):
    def test_one_attempt_26_of_27_distinguishes_overall_and_per_case_gates(self):
        template = fixture()["results"]["results"][0]
        rows = []
        for i in range(27):
            row = copy.deepcopy(template)
            row["testCase"]["vars"]["scenario"] = f"task{i}"
            row["response"]["raw"]["id"] = f"resp_{i}"
            row["namedScores"]["task_success"] = int(i != 0)
            row["success"] = i != 0
            rows.append(row)
        expected = {("decision", f"task{i}") for i in range(27)}
        conservative = summarize(rows, expected, 1, 0.95)
        overall = summarize(rows, expected, 1, 0.95, accuracy_gate="overall")
        self.assertEqual(conservative["accuracy"], 26 / 27)
        self.assertEqual(overall["accuracy"], 26 / 27)
        self.assertFalse(conservative["qualified_observed"])
        self.assertTrue(overall["qualified_observed"])
        rows[1]["namedScores"]["critical_violation"] = 1
        self.assertFalse(
            summarize(rows, expected, 1, 0.95, accuracy_gate="overall")[
                "qualified_observed"
            ]
        )

    def test_gate_is_recorded_in_report_and_applied_to_profile_and_domain_views(self):
        d = fixture()
        d["results"]["results"][0]["namedScores"]["task_success"] = 0
        per_case = analyze(d, 0.8)
        overall = analyze(d, 0.8, accuracy_gate="overall")
        self.assertFalse(per_case["configurations"]["cheap"]["qualified_observed"])
        self.assertTrue(overall["configurations"]["cheap"]["qualified_observed"])
        self.assertEqual(overall["accuracy_gate"], "overall")
        self.assertEqual(
            overall["profiles"]["consulting"]["configurations"]["cheap"][
                "accuracy_gate"
            ],
            "overall",
        )
        self.assertEqual(
            overall["domains"]["unknown"]["configurations"]["cheap"]["accuracy_gate"],
            "overall",
        )
        self.assertIn("no per-case accuracy threshold", markdown(overall))
        self.assertRaises(ValueError, analyze, d, 0.95, "unknown")

    def test_frontier_respects_cost_and_latency_under_quality_gate(self):
        r = analyze(fixture())
        self.assertEqual(
            r["choices"],
            {
                "least_cost": "cheap",
                "fastest": "fast",
                "pareto_frontier": ["cheap", "fast"],
            },
        )
        self.assertEqual(r["distinct_cases"], 2)

    def test_domain_comparisons_show_scores_cost_latency_and_do_not_pool_collections(
        self,
    ):
        d = fixture()
        for t in d["config"]["tests"] + [
            r["testCase"] for r in d["results"]["results"]
        ]:
            t["metadata"].update(
                domain=t["vars"]["scenario"], evaluation_collection="main"
            )
        r = analyze(d)
        self.assertEqual(set(r["domains"]), {"planning", "diligence"})
        self.assertEqual(r["domains"]["planning"]["cases"], 1)
        self.assertEqual(
            r["domains"]["planning"]["configurations"]["cheap"]["accuracy"], 1
        )
        self.assertEqual(
            r["domains"]["planning"]["configurations"]["cheap"][
                "mean_cost_per_attempt_usd"
            ],
            0.01,
        )
        self.assertEqual(
            r["domains"]["planning"]["configurations"]["cheap"][
                "median_latency_seconds"
            ],
            2,
        )
        self.assertIn("Domain comparisons", markdown(r))
        d["config"]["tests"][0]["metadata"]["evaluation_collection"] = "robustness"
        r = analyze(d)
        self.assertEqual(r["evaluation_collections"], ["main", "robustness"])
        self.assertFalse(r["conditions_match"])
        self.assertIsNone(r["choices"]["least_cost"])

    def test_missing_attempts_do_not_imply_perfect_accuracy(self):
        d = fixture()
        d["results"]["results"].pop(0)
        r = analyze(d)
        self.assertFalse(r["configurations"]["cheap"]["complete"])
        self.assertFalse(r["configurations"]["cheap"]["qualified_observed"])
        self.assertEqual(r["choices"]["least_cost"], "fast")

    def test_critical_violation_excludes_even_nominally_correct_answers(self):
        d = fixture()
        d["results"]["results"][0]["namedScores"]["critical_violation"] = 1
        self.assertFalse(analyze(d)["configurations"]["cheap"]["qualified_observed"])

    def test_unknown_cost_and_latency_are_not_zero(self):
        d = fixture()
        d["results"]["results"][0]["response"].pop("cost")
        self.assertIsNone(
            analyze(d)["configurations"]["cheap"]["mean_cost_per_attempt_usd"]
        )
        self.assertEqual(analyze(d)["choices"]["least_cost"], "fast")
        d["results"]["results"][6].pop("latencyMs")
        self.assertEqual(analyze(d)["choices"]["least_cost"], "dominated")

    def test_format_is_separate_and_sanity_rows_are_excluded(self):
        d = fixture()
        d["results"]["results"][0]["namedScores"]["format_compliance"] = 0
        d["results"]["results"].append({"testCase": {"metadata": {"track": "sanity"}}})
        r = analyze(d)
        self.assertEqual(r["configurations"]["cheap"]["accuracy"], 1)
        self.assertEqual(r["configurations"]["cheap"]["format_passes"], 5)
        self.assertEqual(r["excluded_nondecision_rows"], 1)

    def test_strict_counts_use_native_pass_flag_despite_metric_name_collision(self):
        d = fixture()
        row = d["results"]["results"][0]
        row.update(success=False)
        row["namedScores"].update(
            strict_correctness=1, format_compliance=0, format_only=1
        )
        report = analyze(d)
        group = report["configurations"]["cheap"]
        self.assertEqual(group["accuracy"], 1)
        self.assertEqual(group["strict_passes"], 5)
        self.assertEqual(group["strict_accuracy"], 5 / 6)
        self.assertEqual(group["format_only_failures"], 1)
        self.assertEqual(report["strict_metric_collision_rows"], 1)
        self.assertIn("Strict counts below use native pass/fail", markdown(report))
        row.pop("success")
        self.assertIsNone(analyze(d)["configurations"]["cheap"]["strict_passes"])

    def test_mismatched_conditions_block_comparison(self):
        d = fixture()
        d["results"]["results"][0]["testCase"]["metadata"]["run_conditions"][
            "concurrency"
        ] = 4
        self.assertIsNone(analyze(d)["choices"]["least_cost"])

    def test_structured_and_prompt_only_outputs_cannot_be_pooled(self):
        d = fixture()
        d["results"]["results"][0]["testCase"]["metadata"]["run_conditions"][
            "output_mode"
        ] = "structured_outputs"
        report = analyze(d)
        self.assertFalse(report["conditions_match"])
        self.assertIsNone(report["choices"]["least_cost"])
        self.assertEqual(
            report["output_modes"], ["prompt_requested_json", "structured_outputs"]
        )
        self.assertIn("Output mode:", markdown(report))

    def test_redacted_fingerprints_are_unknown_not_verified_matches(self):
        d = fixture()
        for r in d["results"]["results"]:
            r["testCase"]["metadata"]["grader_sha256"] = "[REDACTED]"
        report = analyze(d)
        self.assertEqual(report["grader_fingerprint_status"], "unavailable")
        self.assertTrue(report["conditions_match"])
        self.assertEqual(report["choices"]["least_cost"], "cheap")

    def test_cached_or_duplicate_responses_cannot_win(self):
        d = fixture()
        d["results"]["results"][0]["response"]["cached"] = True
        r = analyze(d)
        self.assertFalse(r["configurations"]["cheap"]["fresh_calls_verified"])
        self.assertIsNone(r["configurations"]["cheap"]["median_latency_seconds"])
        self.assertEqual(r["choices"]["least_cost"], "fast")
        d = fixture()
        d["results"]["results"][1]["response"]["raw"]["id"] = d["results"]["results"][
            0
        ]["response"]["raw"]["id"]
        self.assertFalse(analyze(d)["configurations"]["cheap"]["qualified_observed"])
