"""What entailment actually does to real answers.

The checker was justified on twenty hand-curated claim/evidence pairs: short, single-proposition
claims, recall 0.909, precision 1.000. Real answers are not that shape. A paragraph-trailing marker
takes the whole paragraph as its claim, and a larger span holds more propositions, every one of
which has to be entailed for the span to pass. So the curated numbers should be optimistic and the
dataset cannot see by how much.

That matters because the failure mode is quiet. If most RAG answers now grade `ambiguous`, `correct`
becomes unreachable on that path and the grade stops discriminating -- the exact thing the checker
was supposed to protect against.

This measures the rate and prints every flagged claim beside the evidence it cites, because the rate
alone does not say whether the flags are right. That judgement is made by reading them.

Trials, not single runs: synthesis is non-deterministic. The citation work measured the same
question producing markers on one attempt and none on the next, so one run per question would
measure luck.
"""

import argparse
import asyncio
import json
from pathlib import Path

import httpx

from app.core.config import settings

QUESTIONS = [
    "Why did we choose the grader model?",
    "What is this project about?",
    "Who is working on this project?",
    "What was the last feature added in this project?",
    "How does retrieval work?",
    "Why is the backend deployed on EC2?",
    "What was decided about rate limiting?",
    "How are citations validated?",
    "What are the known limitations?",
    "Why does the project use Ollama?",
]


async def run(
    base_url: str, api_key: str, project_id: str, trials: int, delay: float = 0.0
) -> dict:
    answers = []
    async with httpx.AsyncClient(base_url=base_url, timeout=httpx.Timeout(180)) as client:
        for question in QUESTIONS:
            for trial in range(trials):
                response = await client.post(
                    "/query",
                    headers={"X-API-Key": api_key},
                    json={"query": question, "project_id": project_id, "include_trace": True},
                )
                response.raise_for_status()
                body = response.json()
                step = next(
                    (s for s in body["trace"] if s["name"] == "Entailment Check"), None
                )
                if step is None:
                    # A structured route, or a refusal. Neither is what this measures.
                    continue
                snippets = {item["citation_id"]: item["snippet"] for item in body["evidence"]}
                flagged = [
                    {
                        "claim": gap,
                        "evidence": snippets,
                    }
                    for gap in body["unresolved_gaps"]
                    if "does not state this claim" in gap
                ]
                if delay:
                    # Paced so this can run against the deployed backend without disabling its rate
                    # limiter. The limiter is production behaviour; turning it off to measure would
                    # be measuring a configuration nobody runs.
                    await asyncio.sleep(delay)
                answers.append(
                    {
                        "question": question,
                        "trial": trial + 1,
                        "grade": body["retrieval_grade"],
                        "citations": len(body["citations"]),
                        "summary": step["summary"],
                        "flagged": flagged,
                        "checked": "not checked" not in step["summary"],
                    }
                )

    checked = [a for a in answers if a["checked"]]
    with_flag = [a for a in checked if a["flagged"]]
    return {
        "answers": len(answers),
        "checked": len(checked),
        "answers_with_a_flag": len(with_flag),
        "flag_rate": len(with_flag) / len(checked) if checked else 0.0,
        "graded_correct": sum(1 for a in checked if a["grade"] == "correct") ,
        "results": answers,
    }


def render_markdown(summary: dict) -> str:
    rate = summary["flag_rate"]
    lines = [
        "# Entailment on real answers",
        "",
        f"- Answers checked: {summary['checked']} of {summary['answers']}",
        f"- **Answers with at least one flagged claim: {summary['answers_with_a_flag']} "
        f"({rate:.0%})**",
        f"- Still graded `correct`: {summary['graded_correct']}",
        "",
        "| Question | Trial | Grade | Cites | Entailment |",
        "|---|---:|---|---:|---|",
    ]
    for item in summary["results"]:
        lines.append(
            f"| {item['question'][:44]} | {item['trial']} | `{item['grade']}` | "
            f"{item['citations']} | {item['summary'][:70]} |"
        )

    flagged = [item for item in summary["results"] if item["flagged"]]
    if flagged:
        lines += ["", "## Every flagged claim, for adjudication", ""]
        for item in flagged:
            lines.append(f"**{item['question']}** (trial {item['trial']})")
            for flag in item["flagged"]:
                lines.append(f"- {flag['claim']}")
            lines.append("")
    return "\n".join(lines) + "\n"


async def main() -> int:
    parser = argparse.ArgumentParser(description="Measure entailment on real answers.")
    parser.add_argument("--base-url", default="http://localhost:8000")
    # Defaults to the configured key so it never has to be typed. A key passed on the command
    # line is visible in the process table to anyone with a shell on the host, and in shell
    # history -- which is how APP_API_KEY ended up in a transcript on 2026-09-04.
    parser.add_argument("--api-key", default=settings.app_api_key)
    parser.add_argument("--project-id", default="groundwork")
    parser.add_argument("--trials", type=int, default=2)
    parser.add_argument("--delay", type=float, default=0.0)
    parser.add_argument("--markdown-report")
    parser.add_argument("--json-report")
    args = parser.parse_args()

    summary = await run(
        args.base_url, args.api_key, args.project_id, args.trials, args.delay
    )
    report = render_markdown(summary)
    print(report)
    if args.markdown_report:
        Path(args.markdown_report).write_text(report)
    if args.json_report:
        Path(args.json_report).write_text(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
