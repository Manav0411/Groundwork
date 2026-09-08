# GW-7 verified against the live deployment

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

(filled in below by the live push that carried this file)
