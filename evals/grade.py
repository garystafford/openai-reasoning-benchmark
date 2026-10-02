"""Promptfoo assertion for the 27 tasks described in the post."""

from evals.decision_grade import grade_decision


def get_assert(output, context):
    if context["vars"]["suite"] not in ("decision", "complex", "reasoning"):
        raise ValueError("Unknown benchmark suite")
    return grade_decision(output, context)
