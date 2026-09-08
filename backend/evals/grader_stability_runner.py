"""How often does the grader change its mind on identical input?

Every release gate in this repo runs once and trusts the answer. That is only sound if the grader
is deterministic. On 2026-09-07 two questions were answered with citations at 05:20 and refused
three times each at 12:00, with the same corpus, the same code and no 429s in the logs. Sampling
was ruled out: the hosted grader runs at temperature 0.

This measures the flip rate directly. Retrieval runs once per question and the resulting chunk set
is frozen, then the grader is called N times on that identical set. Anything that varies is the
grader, not the retrieval that feeds it, and not the corpus.

Run it on the instance, inside the backend container, so it uses the deployed configuration:

    docker cp evals/grader_stability_runner.py groundwork-backend:/tmp/
    docker exec groundwork-backend python /tmp/grader_stability_runner.py --replays 10
"""

import argparse
import asyncio
import json
from collections import Counter

from app.db.session import SessionFactory
from app.services.grading import grade_retrieval
from app.services.llm import chat_client, embedding_client
from app.services.retrieval import hybrid_retrieve

QUESTIONS = [
    # The two that flipped between morning and midday on 2026-09-07.
    "How does retrieval work?",
    "Why is the backend deployed on EC2?",
    # A control that has graded `correct` on every run so far, including the recorded fixture.
    "Why did we delete the synthetic demo evidence?",
]


async def measure(project_id: str, replays: int, retrieval_checks: int) -> dict:
    embedder = embedding_client()
    grader = chat_client("grader")
    out: dict = {"project_id": project_id, "replays": replays, "questions": []}

    async with SessionFactory() as session:
        for question in QUESTIONS:
            # Retrieval first, repeated, to establish it is not the variable.
            id_sets = []
            for _ in range(retrieval_checks):
                records = await hybrid_retrieve(session, project_id, question, ollama=embedder)
                id_sets.append([r.chunk_id for r in records])
            retrieval_stable = all(s == id_sets[0] for s in id_sets)

            frozen = await hybrid_retrieve(session, project_id, question, ollama=embedder)
            verdicts = []
            for _ in range(replays):
                result = await grade_retrieval(question, frozen, ollama=grader)
                verdict = result.verdict
                verdicts.append(
                    {
                        "grade": result.grade,
                        "answerable": None if verdict is None else verdict.answerable,
                        "needed": "" if verdict is None else verdict.needed[:120],
                        "evidence": "" if verdict is None else verdict.evidence[:120],
                        "summary": result.summary[:160],
                    }
                )

            grades = Counter(v["grade"] for v in verdicts)
            answerable = Counter(str(v["answerable"]) for v in verdicts)
            out["questions"].append(
                {
                    "question": question,
                    "chunks": len(frozen),
                    "retrieval_stable": retrieval_stable,
                    "retrieved_ids": id_sets[0],
                    "grades": dict(grades),
                    "answerable": dict(answerable),
                    "flipped": len(answerable) > 1,
                    "verdicts": verdicts,
                }
            )
            print(
                f"{question[:44]:46} retrieval_stable={retrieval_stable} "
                f"grades={dict(grades)} answerable={dict(answerable)}",
                flush=True,
            )
    return out


async def main() -> int:
    parser = argparse.ArgumentParser(description="Measure grader verdict stability.")
    parser.add_argument("--project-id", default="groundwork")
    parser.add_argument("--replays", type=int, default=10)
    parser.add_argument("--retrieval-checks", type=int, default=3)
    parser.add_argument("--json-report")
    args = parser.parse_args()

    summary = await measure(args.project_id, args.replays, args.retrieval_checks)
    flipped = [q["question"] for q in summary["questions"] if q["flipped"]]
    print(f"\nquestions whose verdict flipped: {len(flipped)} of {len(summary['questions'])}")
    for q in flipped:
        print(f"  - {q}")
    if args.json_report:
        with open(args.json_report, "w") as handle:
            json.dump(summary, handle, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
