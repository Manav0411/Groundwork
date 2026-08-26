# Golden conversation baseline — 2026-08-26

35 conversations, 62 turns, 3 trials each, against the live AskBase corpus with Ollama on the host.

```bash
.venv/bin/python -m evals.conversation_runner --trials 3 --sync-before --fail-under 1.0
```

## Why this suite exists

Twelve defects were found in five rounds of manual testing, and **not one was found by the test
suite**. The deterministic eval gate caught exactly one. The reason was structural: every existing
dataset is single-turn, and the bugs lived in turn two and three. The harnesses that did find them
were throwaway scripts in `/tmp`.

## Result

| | |
|---|---:|
| Gated conversations passing every hard check | **34/34** |
| Hard pass rate | **1.000** |
| Known limitations, excluded from the gate | 1 |

Hard checks — route, grade, citation presence, `[n]` marker validity, forbidden text — gate at
1.000. Measured checks — whether a follow-up resolved and to what — are reported as a rate over the
trials, because asserting a 3B model's output once turns variance into a red build.

### Measured checks below 100%

| Check | Rate | Reading |
|---|---:|---|
| `followup_asking_for_source` turn2 resolved | 67% | *"which channel was that discussed in?"* — resolution is model-dependent; the answer is still correct when it does not resolve |
| `followup_asking_for_source` turn2 answer contains "decisions" | 67% | Same turn; phrasing varies even when the retrieved thread does not |

Everything else measured 100% across three trials.

## What the first run found

The suite was written from what `docs/STATUS.md` claims, not from behaviour known to pass. The first
21 conversations all passed — which meant the dataset was encoding what had already been fixed. A
second wave of 11 **exploratory** conversations, written for territory nobody had tested, found
three real defects:

**1. A commit question describing content had no path.** *"Which commit dropped the HuggingFace
dependency?"* names no author, no hash, and no position, so it reached the structured GitHub tool —
which answers exactly one question, *which commit is Nth-newest for an author* — and was told an
author was required. This was a **first-turn** failure, so no amount of follow-up resolution could
have helped, and five rounds of manual multi-turn testing could never have surfaced it. Content
commit questions now route to retrieval; the three live eval cases that expect the author prompt all
carry a superlative ("latest", "most recent") and are unaffected.

**2. A demonstrative mid-sentence was not a back-reference.** *"which channel was that discussed
in?"* never resolved: `that` was neither at the end of the clause nor followed by a known noun. A
demonstrative followed by a verb is a pronoun, not a determiner, and now counts.

**3. Long conversations anchored on the wrong turn.** After six Jira questions, *"who is it assigned
to?"* resolved against **ASK-5** — the oldest turn still inside the five-turn window — instead of
ASK-4, the one immediately before. The history prompt was an unlabelled transcript, giving the model
no reason to prefer the end. Marking the newest exchange took it from 0/2 to 2/2, then 3/3.

A fourth finding was in the harness rather than the product: a known limitation was reported as
"now passing" because its *hard* checks passed while its measured expectation still failed. Resolved
now means every check, not just the gated ones — otherwise a real limitation retires on a
technicality.

## Known limitations, reported and not gated

Each carries a written reason so the bucket cannot become a place to hide inconvenient results.
Deleting a marker is how a fix gets recorded.

| Conversation | Why |
|---|---|
| `refusal_unanswerable` | *"What is the Sprint 24 delivery velocity?"* is accepted because the corpus grew Slack timing metrics that superficially resemble a velocity figure. Recorded in `slack.md`; the question is still unanswerable |

## Two limitations closed — 2026-08-26

Both markers were deleted rather than reworded, which is how a fix gets recorded here.

**`aggregate_completion` is now gated and passing.** *"Are all the tasks complete?"* was refused
because the grader judges whether a **passage** supports the answer and a quantifier is answered by
the **set** — every chunk was correctly rejected. The earlier note said loosening the grader risked
the property it exists for, and that was right; what was wrong was assuming the grader had to be
involved. `structured_jira.py` had no project-wide tool, so the question had nowhere else to go.
`jira_project_status` counts issues by status category and returns the outstanding ones so the
count can be cited. No model participates. `counting_question` moved with it.

**`commit_feature_detail` is now gated and passing**, on a narrower claim. The corpus limitation is
real and unchanged: commit messages record what changed, not which product feature it belonged to.
What changed is that the answer used to present commit metadata as though it had answered the
question. It now returns the commit *and* discloses the half it cannot answer. The case does not
assert a route — resolution may rewrite the follow-up to name the hash (`commit_detail`) or leave
it positional (`latest_commit`), both defensible, and the disclosure is emitted either way.

## `--fast`

`--fast` runs one trial over the categories whose expectations the code decides: **90 seconds
against ~50 minutes**. It skips `exploratory`, `cross_source`, `decision`, and `corpus_limit`,
which is where the runtime goes — those run the corrective loop and several model calls per turn.
The full suite remains the release gate.

## Limitations of this baseline

- One corpus, one project. Directional, not precise.
- Three trials is enough to separate 100% from 67%, not enough to distinguish 95% from 90%.
- The dataset still encodes today's behaviour as correct wherever a claim was ambiguous. Two
  expectations were wrong rather than the system: `ambiguous_referent` asserted one of two
  defensible readings of "it", and `slack_answer_asked_as_github` demanded a specific commit when
  another was equally defensible. Both were corrected, and that correction is itself a reminder
  that a golden dataset is an opinion until something disagrees with it.
