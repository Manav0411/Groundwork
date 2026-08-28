# Generalization baseline — 2026-08-26

```bash
python -m evals.generalization_runner --project-id askbase
```

## Why this suite exists

Every other eval dataset in this repo hardcodes its expectations. `askbase.jsonl` names the SHA it
expects; `jira_askbase.jsonl` names the issue key. That guards known behaviour well, and it cannot
demonstrate the claim the project is actually making — that this answers *any* engineering project,
not the one it was built against. **A dataset whose answers were typed by hand from the data it
tests proves nothing about generalization.**

This suite asks the database what is there, builds questions from those values, and checks the
answers against them. It takes `--project-id` and carries no knowledge of any corpus.

## Result

| Project | Corpus | Passed | Not planned |
|---|---|---:|---:|
| `askbase` | 36 commits, 8 issues | **7/7** | 1 |
| `groundwork` | 34 commits | **5/5** | 3 |

Unplanned cases are those whose ground truth the corpus cannot supply: no `askbase` Jira issue has
an assignee, and `groundwork` had no Jira connector configured at the time of this run. Recorded
rather than silently omitted — "7/7 passed" means something different when a case never ran.

### The second project is the one that matters

`askbase` is the corpus this system was built against; passing there was expected and proves
little. `Manav0411/Groundwork` was synced and answered **without a single line of the suite
changing** — no new dataset, no new expectations, no code touched. The suite read 34 commits it had
never seen, derived the newest and second-newest for the most prolific identity, sampled a hash from
the middle of history, and checked all five answers against what SQL said. All five passed, and the
SHAs are independently verifiable against `git log`.

The hybrid path was checked by hand on the same corpus, since the suite deliberately asserts only
deterministic questions. *"What decisions were made about the retrieval approach?"* graded `correct`
with 4 citations and no gaps; *"Summarize recent engineering activity"* graded `correct` with 8. Both
answers were grounded in the new project's real commits, and neither borrowed anything from
`askbase`.

## What it found on the first run: the harness, not the product

**1/7 passed.** All six failures were mine.

**The probe disagreed with the product about who wrote a commit.** It grouped by
`SourceDocument.author`, which holds whatever GitHub reported per commit — `Manav0411` on 31 and
`Manav Goel` on 5. `author_identities` correctly unifies both into one person with 36. So the
expectation said the newest commit was `4121d76` while the system correctly answered `f4a941f`, and
the "36th commit" case asserted an out-of-range refusal for a position that exists.

The plan for this phase said to derive ground truth from the tools the answer path uses rather than
from parallel SQL. Writing parallel SQL anyway produced a wrong expectation on the very first case.
The probe now calls `latest_commit_by_author` and `jira_issues_by_assignee` directly.

**Every grade came back `ambiguous`.** Also correct: the last sync was hours old and the freshness
policy downgrades stale answers. Asserting a bare `correct` would mean the suite only passes in the
minutes after a sync, so it accepts a downgrade when the staleness is disclosed — the contract,
rather than the convenient half of it.

## Is the suite capable of failing?

A suite that passes on day one has proved nothing until it can be shown to fail. Mutating
`latest_commit_by_author` to ignore its `offset` argument dropped it to **5/7**, failing exactly
the two ordinal cases.

Worth noting *how* it caught that. Because the probe uses the same tool, the mutation moved the
expectation and the answer together — `second_newest` was derived as `f4a941f` and answered
`f4a941f`, which matches. The `answer_excludes` cross-check is what failed it: the second-newest
commit must not be the newest, and that statement is true independently of the tool. **A probe that
shares code with the thing it checks needs at least one assertion that does not.**

## Limitations

- Two projects, one owner, one GitHub account. Author-identity normalization has not been tested
  against a repository with several distinct contributors, which is where it is most likely to
  break.
- Deterministic-path questions only. Retrieval and synthesis are measured by the conversation suite
  and `retrieval_runner`; asserting a 3B model's phrasing against unseen data would measure
  variance.
- Cases are skipped when the corpus cannot supply their ground truth, so a thin corpus yields a
  smaller suite. The count of skipped cases is reported for that reason.
