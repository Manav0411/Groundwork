# Author identity on a multi-contributor repository — 2026-09-04

GW-9. Identity normalisation had only ever run against corpora with one human. `Manav0411` and
`Manav Goel` unify correctly, and that was the whole of the evidence. This indexes a third project,
`pallets/flask`, to put the same code under a corpus it was never shaped around.

## Corpus

| | |
|---|---:|
| Commits indexed | 500 |
| Distinct display names | 51 |
| Distinct logins | 48 |
| Distinct emails | 51 |
| Contributors visible in the 100 newest commits | **9 of 51** |

The last row is the one that mattered. Two contributors share a name prefix and nothing else:
David Lord (`davidism`, 396 commits, newest) and David (`CheeseCake87`, 1 commit, 198th by
recency). They are genuinely different people — no shared login, no shared email.

## Defects found

Four, three of which only a corpus of this shape could expose.

**1. Grouping keyed on the display name.** One human recorded two ways came back as an ambiguity
and was refused, even though the identity arrays shared a login and an email. The exact-match path
unified them, so it surfaced only on a partial query. Rows are now clustered by shared identity
token, applied transitively.

**2. Ingestion and lookup normalised differently.** `strip().casefold()` on write against
`" ".join(casefold().split())` on read, so a name with an internal double space was written one way
and searched another and could never be found. Both sides now call one function.

**3. Ambiguity was decided over a recency window.** The partial-match path read the 100 newest
commits and filtered them in Python, so it could not see contributors outside that window — 42 of
51 here. `"davi"` matched both Davids and answered as David Lord, disclosing nothing. Ambiguity is
now decided by a SQL predicate over every row in the project.

**4. A commit position past the window was clamped, not refused.** `"the 105th commit by davidism"`
was answered with the 100th. The extractor's own comment already said out-of-range positions are
refused rather than clamped; the code clamped. The refusal also stopped claiming David Lord "has
only 100 indexed commits" when he has 396 — a fact about the lookup window stated as a fact about
the person.

## Behaviour, before and after

| Question (project `flask`) | Before | After |
|---|---|---|
| `last commit by davi` | David Lord, `correct`, 1 citation | **ambiguous**, lists David and David Lord |
| `last commit by David Lord` | `correct`, 1 citation | unchanged |
| `last commit by davidism` (login) | resolves to David Lord | unchanged |
| `the 105th commit by davidism` | the 100th commit, `correct` | **refused**, window disclosed |

## Suites

| | Before | After |
|---|---:|---:|
| Generalization — `flask` (unseen) | 4/5 | **5/5** |
| Generalization — `groundwork` | 8/8 | 8/8 |
| Generalization — `askbase` | 7/7 | 7/7 |
| `evals/askbase.jsonl` gate | 13/16 | **17/17** |
| Unit tests | 337 | 351 |
| Integration tests | 105 | 114 |

The askbase gate had four stale cases, found only because this work ran it — CI does not. Three
asserted that "What was the latest commit?" is answered by asking for an author, which stopped
being true when the author became optional. The fourth, `partial_ambiguous`, asserted that
"the last commit by Manav" is ambiguous between `Manav Goel` and `Manav0411` — **the dataset had
encoded defect 1 as correct behaviour**, so the suite would have defended it. It is replaced by an
ambiguity case over two genuinely different people, which askbase does not contain.

## Left open

Clustering still merges on the display name as well as on login and email. Excluding it would split
one human who used two logins and two emails, which the suite covers and is the commoner shape. The
cost is that two different people with an identical display name and nothing else in common merge
into one. That is not a regression — keying on the display name merged them too — and separating
them needs evidence this corpus does not carry.

An exact match on a display name that is a strict prefix of another contributor's still wins
silently: `"david"` is David's literal display name, so it resolves to their single commit without
noting that David Lord also matched. Exact-beats-partial is a sound precedence rule, so this is
recorded rather than changed.
