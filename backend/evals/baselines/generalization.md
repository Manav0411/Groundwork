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
| `groundwork` | 34 commits, 12 issues | **8/8** | 0 |

The one unplanned case is `askbase`'s assignee lookup: not a single one of its 8 Jira issues has an
assignee, so the ground truth does not exist. Recorded rather than silently omitted — "7/7 passed"
means something different when a case never ran.

That gap is also why `issues_by_assignee` had never executed at all until `groundwork` was given a
Jira project. Three of the eight cases — `issue_by_key`, `issues_by_assignee`, `issue_counts` — had
only ever been tested against the corpus the system was built on. All three passed first try
against a corpus written days later, and the citation counts are the tell: the assignee lookup
returned exactly the 8 assigned issues, and the count returned exactly the 9 that are not done.

### The second project is the one that matters

`askbase` is the corpus this system was built against; passing there was expected and proves
little. `Manav0411/Groundwork` was synced and answered **without a single line of the suite
changing** — no new dataset, no new expectations, no code touched. The suite read 34 commits and 12
Jira issues it had never seen, derived the newest and second-newest for the most prolific identity,
sampled a hash from the middle of history, picked a real issue key and a real assignee, and checked
all eight answers against what SQL said. All eight passed, and the SHAs are independently
verifiable against `git log`.

Answers were also read by hand rather than only diffed. *"Are all the tasks complete?"* returned
"No — 3 of 12 indexed Jira issues are done, and 9 are not", enumerating and citing each; the count
matches the Jira API exactly. *"What blockers are open?"* found the single issue carrying both
`Highest` priority and the `blocked` label.

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

## What connecting the third source found

Slack was added to `groundwork` after Jira. It moved no counter — the suite asserts deterministic
paths and Slack is retrieval-only by design — but it surfaced a routing gap that only a second
project could have exposed.

`BLOCKER_PATTERN` matched `blocker`, `blockers` and `blocked`, but not `blocking` or `blocks`. So
*"what is blocking the EC2 deployment?"* fell through to retrieval and answered *"blocked due to
the lack of a hosted API"* — confidently, graded `correct`, with no gap disclosed — while the Jira
issue carrying the `blocked` label and a comment naming the real reason sat one deterministic lookup
away. The verb form is at least as natural as the noun.

Fixed, with the over-correction guarded: the pattern now requires a suffix, so "which code block
changed?" is still not a blocker question.

Worth noting *why* it survived until now. AskBase's eval and conversation cases all phrase this as
"what blockers are open?", because they were written by the same person who wrote the regex. A
second corpus prompted a different phrasing, and the phrasing is what broke.

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
  break. Tracked as `GW-9`.
- **`groundwork`'s Jira project was written by hand for this test**, so it is weaker evidence than
  a backlog that grew on its own. It was populated from the project's genuine open work — background
  sync, `/metrics`, rate limiting, the EC2 deploy — rather than filler, which keeps the text real,
  but the corpus did not arrive by accident and that is worth knowing when reading the result.
- Slack is connected on `groundwork` (6 threads, one document each). Cross-source retrieval works
  mechanically — one answer drew 3 Slack citations and 1 GitHub — but see below.
- **Cross-source answers are not asserted by any suite.** The generalization suite covers
  deterministic paths only, and what the second project exposed on the retrieval path is recorded
  in `inference.md`: synthesis reproduces the *direction* of retrieved evidence reliably and its
  *figures* unreliably.
- Deterministic-path questions only. Retrieval and synthesis are measured by the conversation suite
  and `retrieval_runner`; asserting a 3B model's phrasing against unseen data would measure
  variance.
- Cases are skipped when the corpus cannot supply their ground truth, so a thin corpus yields a
  smaller suite. The count of skipped cases is reported for that reason.
