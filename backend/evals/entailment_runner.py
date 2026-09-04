"""Measure the entailment checker against hand-labelled claim/evidence pairs.

The point of this harness is that the two errors do not cost the same, so they are never reported
as one number:

**Recall on unsupported claims** is whether the checker catches a fabrication. Missing one is the
failure the checker exists to prevent.

**Precision on supported claims** is whether it leaves correct claims alone. Flagging a good
paraphrase downgrades a correct answer, and a grade that fires on correct answers teaches the
reader to ignore it -- which is worse than having no check.

An accuracy figure averages those into something that hides both. `grading_runner.py` reports
verdict precision and recall separately for the same reason.
"""

import argparse
import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

from app.services.citations import ClaimSpan
from app.services.entailment import check_entailment

DATASET = Path(__file__).parent / "entailment_dataset.jsonl"


@dataclass(frozen=True)
class Case:
    id: str
    label: str
    claim: str
    premise: str
    notes: str


def load_cases(path: Path) -> list[Case]:
    cases = [
        Case(**json.loads(line)) for line in path.read_text().splitlines() if line.strip()
    ]
    if not cases:
        raise ValueError(f"{path} contained no cases")
    if len({case.id for case in cases}) != len(cases):
        raise ValueError("duplicate case ids")
    return cases


async def run(cases: list[Case]) -> dict:
    results = []
    for case in cases:
        # Judged one at a time so a wrong verdict cannot be blamed on a neighbouring claim. The
        # product batches; this measures the judgement itself.
        premises = {index + 1: part.strip() for index, part in enumerate(case.premise.split("||"))}
        span = ClaimSpan(text=case.claim, ordinals=sorted(premises))
        outcome = await check_entailment([span], premises)
        if not outcome.used_model:
            raise SystemExit(
                f"The checker degraded instead of judging ({outcome.summary}). Every number below "
                "would measure an outage rather than the checker."
            )
        verdict = outcome.verdicts[0]
        predicted = "supported" if verdict.supported else "unsupported"
        results.append(
            {
                "id": case.id,
                "label": case.label,
                "predicted": predicted,
                "correct": predicted == case.label,
                "quote": verdict.quote,
                "notes": case.notes,
            }
        )

    unsupported = [r for r in results if r["label"] == "unsupported"]
    supported = [r for r in results if r["label"] == "supported"]
    caught = sum(1 for r in unsupported if r["predicted"] == "unsupported")
    kept = sum(1 for r in supported if r["predicted"] == "supported")
    flagged = sum(1 for r in results if r["predicted"] == "unsupported")
    return {
        "cases": len(results),
        "recall_on_unsupported": caught / len(unsupported) if unsupported else 0.0,
        "precision_on_flagged": caught / flagged if flagged else 0.0,
        "correct_on_supported": kept / len(supported) if supported else 0.0,
        "results": results,
    }


def render_markdown(summary: dict) -> str:
    lines = [
        "# Entailment checker",
        "",
        f"- Cases: {summary['cases']}",
        f"- **Recall on unsupported claims: {summary['recall_on_unsupported']:.3f}** "
        "— the fabrications it catches, which is what it exists for",
        f"- **Correct on supported claims: {summary['correct_on_supported']:.3f}** "
        "— correct claims left alone; a checker that fails here is worse than none",
        f"- Precision of a flag: {summary['precision_on_flagged']:.3f}",
        "",
        "| Case | Labelled | Predicted | | Quote |",
        "|---|---|---|---|---|",
    ]
    for item in summary["results"]:
        mark = "ok" if item["correct"] else "**MISS**"
        quote = (item["quote"] or "—").replace("|", "\\|")[:60]
        lines.append(
            f"| `{item['id']}` | {item['label']} | {item['predicted']} | {mark} | {quote} |"
        )
    misses = [item for item in summary["results"] if not item["correct"]]
    if misses:
        lines += ["", "## Misses", ""]
        for item in misses:
            lines.append(f"- `{item['id']}` ({item['label']}): {item['notes']}")
    return "\n".join(lines) + "\n"


async def main() -> int:
    parser = argparse.ArgumentParser(description="Measure the entailment checker.")
    parser.add_argument("--dataset", default=str(DATASET))
    parser.add_argument("--markdown-report")
    parser.add_argument("--json-report")
    args = parser.parse_args()

    summary = await run(load_cases(Path(args.dataset)))
    report = render_markdown(summary)
    print(report)
    if args.markdown_report:
        Path(args.markdown_report).write_text(report)
    if args.json_report:
        Path(args.json_report).write_text(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
