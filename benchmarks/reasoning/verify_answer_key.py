"""Check the post's reasoning source packs against independently derived answers."""

import json
from .reference import DIRECTORY, derive_answers, build_prompts


def verify_answer_key():
    expected = json.loads((DIRECTORY / "expected_answers.json").read_text())["answers"]
    derived = derive_answers()
    assert derived == expected, "Reference answer mismatch"
    assert (
        json.loads((DIRECTORY / "prompts.json").read_text()) == build_prompts()
    ), "Prompt/source drift"
    return derived


if __name__ == "__main__":
    print(f"Verified {len(verify_answer_key())} reasoning tasks.")
