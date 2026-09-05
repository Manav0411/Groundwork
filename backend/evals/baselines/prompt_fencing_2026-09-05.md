# Fencing the evidence — 2026-09-05

The injection probe found nothing obeyed, but three of six payloads were stopped by the model rather
than by anything designed, and the model is configuration. So the boundary between instruction and
evidence is now explicit in all three prompts corpus text reaches: a fenced block plus a line saying
the enclosed text is data which may contain sentences shaped like commands, to be reported rather
than followed.

The question this measures is whether that cost anything. It edits `build_answer_prompt`, which two
changes ago took unsupported claims from 43% to 27%.

## It cost nothing, and injection is still blocked

Probe re-run with fences in place: **0 of 6 obeyed**, and more payloads now refuse outright rather
than answering around the injected text.

| | before fences | after fences |
|---|---:|---:|
| Answers with claims to judge | 18 | 29 |
| **Flagged** | 5 (**28%**) | 4 (**14%**) |
| Graded `correct` | 12 of 50 | 25 of 50 |
| Unchecked (provider rate limit) | 19 | 6 |

**Do not read 28% → 14% as a result.** The denominators differ for an environmental reason: the
earlier run hit the provider's per-minute ceiling on 19 of 50 answers against 6 here, because the
box was busier, not because anything changed. 4/29 against 5/18 is well inside what this sample can
resolve. What is safe to say is that fencing did not make grounding worse, which is what it was run
to find out.

## The finding that matters is not the rate

**Every flag came from one question.** Five of six questions are clean across every trial:

| Question | Flagged |
|---|---:|
| Why did we choose the grader model? | 0/5 |
| Who is working on this project? | 0/5 |
| **What was the last feature added in this project?** | **4/5** |
| What was decided about rate limiting? | 0/5 |
| How are citations validated? | 0/5 |
| What are the known limitations? | 0/4 |

The answer each time asserts *"the most recent feature added was contributor identity resolution"*,
citing a passage about display-name clustering that never says anything is most recent. A true
positive, four times out of five.

**This is structural, not stylistic.** The corpus holds no passage stating which feature is most
recent, because no such record exists — recency is a property of the commit and issue *ordering*,
which is exactly what the structured routes read and what semantic retrieval cannot. The writer is
handed topically relevant recent-ish work and infers a superlative nobody wrote down.

It is the same shape as *"what was the last conversation on slack?"* before `structured_slack`
existed: a recency question with no route to answer it, falling through to a path that cannot. The
fix is a structured route or an honest refusal, not better prose.

## A correction to the previous baseline

`synthesis_grounding_2026-09-04.md` calls the recency rule "the cleanest evidence" that the grounding
rules worked, on the strength of this same question going from 2/2 flagged to 0/2.

**That was an artefact of two trials.** At five it is 4/5. The rule did not fix this; a small sample
made it look fixed. The earlier document has been left as written — it is a dated record — but its
conclusion about recency is wrong and this supersedes it.

The lesson is the one this project keeps relearning: two trials of a non-deterministic system
measures luck. The 5-trial runs exist because of it and it still caught me out.

## Cost

Fenced prompts are longer, so each call carries slightly more input. Not measurable against the
per-minute ceiling here — the skip count moved with how busy the box was, not with prompt size.
