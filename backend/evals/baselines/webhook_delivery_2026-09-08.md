# GW-7 verified live, and the defect turned out to be worse than recorded

Recorded because the defect this closes is invisible by inspection: a commit polling cannot see
looks exactly like a commit that does not exist.

## The gate

| Check | Result |
|---|---:|
| Endpoint before the secret was set | 503 |
| Unsigned delivery | 401 |
| Wrong signature | 401 |
| GitHub `ping` from 140.82.115.250 (`GitHub-Hookshot`) | 202 |

The signature is the only authentication this endpoint has, so refusal is most of what matters.

## Delivery

Pushed `b117325` and asked the deployed app for the latest commit **without running a sync**:

    The latest indexed commit in groundwork is `b117325`, by Manav0411, "Record the webhook
    verification", committed at 2026-09-08T11:20:10+00:00 [1].

Seconds, not the up-to-an-hour a poll would have taken.

## The blind spot, demonstrated

Pushed an empty commit `c55cc74` with author **and** committer dates set to 2020-01-01, then asked
for it by sha:

    route commit_detail | grade correct
    Commit `c55cc74` by Manav0411, "Backdated empty commit...", committed at
    2020-01-01T00:00:00+00:00 [1].

Indexed, because the webhook addresses commits by sha and never consults the time cursor.

Then the same question put to GitHub the way the poller puts it:

| Poll window | Commits returned |
|---|---:|
| `since` = now - 10 minutes | **0** |
| `since` = now - 60 minutes | **0** |
| `since` = now - 24 hours | **0** |
| no `since` at all | `c55cc74`, `b117325`, `be22696`, ... |

## The part that was not previously understood

`README` and `TRADEOFFS` said a backdated commit "can fall outside the overlap window", implying
one commit goes missing. The real behaviour is worse, and this isolates it:

    since = now - 60m, starting from the default tip (c55cc74, dated 2020) -> []
    since = now - 60m, starting from b117325 (dated today)                 -> ['b117325']

Same window, same repository, different starting point. **GitHub walks history from the tip and
stops at the first commit older than `since`.** So one backdated commit at the tip does not hide
itself, it hides *everything behind it*: `b117325` was committed twenty minutes earlier and became
invisible to every poll the moment a 2020-dated commit landed on top of it.

A poller in that state does not fail. It returns 0 commits, records a successful sync, advances its
cursor, and reports a healthy connector while the index silently stops tracking the repository.
That is the exact failure class this project is built to refuse, sitting in its own ingestion path.

The webhook is immune by construction: it is handed the shas and fetches each directly.

## Housekeeping

The backdated commit is left in history deliberately, as the artifact this measurement refers to.
The next ordinary commit restores traversal for future polls, since it becomes a tip newer than the
cursor; everything below the 2020 commit was already indexed.
