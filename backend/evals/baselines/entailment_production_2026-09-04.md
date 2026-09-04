# Entailment on real answers — 2026-09-04

The checker shipped on **20 hand-curated claim/evidence pairs**: short, single-proposition claims,
recall 0.909, precision 1.000. Real answers are not that shape. A paragraph-trailing marker takes
the whole paragraph as its claim, and a larger span holds more propositions, every one of which must
be entailed for the span to pass. The curated numbers should have been optimistic and the dataset
could not see by how much.

Ten questions, two trials each, against the deployed corpus.

## The rate

| | |
|---|---:|
| Answers with an entailment check | 17 of 20 |
| **Answers with at least one flagged claim** | **6 (35%)** |
| Answers still graded `correct` | 6 |

The number that mattered was whether `correct` survives on the RAG path. It does. A checker that
fired on everything would have made the grade stop discriminating, which is the failure this was
supposed to prevent, not cause.

## Every flag adjudicated by hand — 7 of 7 are real

Read against the passage each claim cites. Nothing automated could do this: a second checker
agreeing with the first is not evidence either is right.

| Claim | What the cited passage says | |
|---|---|---|
| "single model on limited hardware graded only 8 of 20 and silently degraded the rest" | one model served both roles on 16GB | conflates local residency with Groq's TPM exhaustion |
| "…as shown by the Jira assignee entries **and the commit author name**" | two Jira issues; no commit authors | half the claim cites evidence that does not contain it |
| "most recent feature was structured logging and /metrics" (both trials) | metrics histogram bucket choices | recency asserted, never evidenced — and false; entailment and startup sync are newer |
| "EC2 makes it possible to compare RAG latency against local metal" | commit *"measure cloud CPU inference, **and rule it out**"* | inverts the finding into a benefit |
| "the grading model has an 8,000 tokens/min ceiling" | commit adding *the application's own* rate limiter | conflates the app's limiter with the provider's |
| "documentation notes a claim is a span rather than a sentence" | commit lowering the daily cap to 300 | the claim is true; the cited passage does not state it |

**Zero false positives.** Precision held on real prose, and the generous claim span did not cause
the over-flagging it was expected to. The last row is the sharpest illustration of what this checks:
a true statement is still unsupported if the passage it points at does not make it.

## The finding that changed the code

Three of twenty answers had the check skipped — Groq's per-minute ceiling, hit by running twenty
questions back to back. The permissive degradation worked: the answer stood and the trace said
"Entailment not checked".

**But one of those still graded `correct`.** An unverified answer was presented exactly like a
verified one, with only the trace distinguishing them. "Could not verify" is not "verified", and the
grader had already settled this for its own outage — `_derived_grade` downgrades and says relevance
was not checked. An unchecked answer now does the same: `ambiguous`, with a gap saying support is
unverified rather than confirmed.

That is the same class of defect this whole check exists to remove, one level up: a surface
asserting something it does not know.

## Also observed

- **Synthesis is non-deterministic and so is the verdict.** *"Why did we choose the grader model?"*
  flagged on trial 1 and came back clean on trial 2. One trial per question would have measured
  luck, not the rate.
- **Not every downgrade is entailment's.** *"How are citations validated?"* graded `ambiguous` with
  every claim supported — the grade came from elsewhere. Entailment is one input, not the only one.
- **Rate limiting is the real cost ceiling**, not the extra call. Twenty back-to-back questions hit
  the provider's per-minute limit three times; spaced normal use did not.

## Method note

The conversation suite had never been run since entailment landed. Run here: **20/20 gated
conversations, hard pass rate 1.000**, unchanged. Its eight turns expecting `correct` all route to
structured tools, which never reach the entailment node — the placement on the `synthesize` edge
protects the gate by construction rather than by luck.

An earlier run of that suite reported 55%, which was not a regression: every failure was an HTTP 429
from the deployed rate limiter, which is why limits are disabled locally for eval runs.
