"""Export the selected main benchmark; does not generate or modify answer keys."""

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def rows():
    manifest = json.loads((ROOT / "evals/main-matrix.json").read_text())
    catalog = json.loads((ROOT / "evals/benchmark-catalog.json").read_text())["cases"]
    suites = json.loads((ROOT / "evals/suites.json").read_text())
    result = []
    for entry in manifest["cases"]:
        suite, scenario = entry["case_id"].split("/")
        directory = ROOT / "benchmarks" / suite
        prompt = next(
            s
            for s in json.loads((directory / "prompts.json").read_text())["scenarios"]
            if s["id"] == scenario
        )
        answer = json.loads((directory / "expected_answers.json").read_text())[
            "answers"
        ][scenario]
        result.append(
            {
                "id": entry["case_id"],
                "benchmark_version": manifest["version"],
                "domain": entry["domain"],
                "reasoning_mechanism": entry["reasoning_mechanism"],
                "difficulty": "complex",
                "messages": [
                    {"role": "system", "content": suites[suite]["systemPrompt"]},
                    {"role": "user", "content": prompt["prompt"]},
                ],
                "expected_answer": answer,
                "metadata": catalog[entry["case_id"]],
            }
        )
    # Export the same model-facing schemas as Promptfoo, rather than leaving
    # portable consumers to reconstruct the output contract from prose.
    compiler = """
const fs = require('node:fs');
const { outputFormat, schemaHash } = require('./evals/output-schema.cjs');
const { POLICY } = require('./evals/final-answer.cjs');
const rows = JSON.parse(fs.readFileSync(0, 'utf8'));
console.log(JSON.stringify(rows.map(row => {
  const [suite, scenario] = row.id.split('/');
  const format = outputFormat(suite, scenario, row.messages[1].content);
  return { format, fingerprint: schemaHash(format), outputExtraction: POLICY };
})));
"""
    contracts = json.loads(
        subprocess.run(
            ["node", "-e", compiler],
            cwd=ROOT,
            input=json.dumps(result),
            text=True,
            capture_output=True,
            check=True,
        ).stdout
    )
    for row, contract in zip(result, contracts):
        row["text"] = {"format": contract["format"]}
        row["metadata"] = {
            **row["metadata"],
            "output_mode": "structured_outputs",
            "output_schema_sha256": contract["fingerprint"],
            "output_extraction": contract["outputExtraction"],
        }
    return result


def rendered():
    return "".join(
        json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
        for row in rows()
    )


if __name__ == "__main__":
    path = ROOT / "benchmarks/main/dataset.jsonl"
    path.write_text(rendered())
    print(f"Exported {len(rows())} distinct main tasks to {path}")
