"""Verify exactly the 27 tasks used in the post; no API calls."""

import hashlib, importlib, json
from pathlib import Path
from evals.grade import get_assert

ROOT = Path(__file__).resolve().parents[1]


def verify_all():
    manifest = json.loads((ROOT / "evals/main-matrix.json").read_text())
    catalog = json.loads((ROOT / "evals/benchmark-catalog.json").read_text())["cases"]
    expected_ids = {c["case_id"] for c in manifest["cases"]}
    seen = set()
    for suite in ("decision", "complex", "reasoning"):
        directory = ROOT / "benchmarks" / suite
        derived = importlib.import_module(
            f"benchmarks.{suite}.verify_answer_key"
        ).verify_answer_key()
        prompts = json.loads((directory / "prompts.json").read_text())["scenarios"]
        assert {p["id"] for p in prompts} == set(derived)
        for p in prompts:
            key = f"{suite}/{p['id']}"
            seen.add(key)
            assert (
                hashlib.sha256(p["prompt"].encode()).hexdigest()
                == catalog[key]["prompt_sha256"]
            )
            assert get_assert(
                json.dumps(derived[p["id"]]),
                {"vars": {"suite": suite, "scenario": p["id"]}},
            )["pass"]
    assert seen == expected_ids == set(catalog) and len(seen) == 27
    from scripts.build_main_dataset import rendered

    assert (ROOT / "benchmarks/main/dataset.jsonl").read_text() == rendered()
    return len(seen)


if __name__ == "__main__":
    print(f"Verified {verify_all()} main benchmark tasks.")
