# Grading report — retrieval_dataset.jsonl

- Completed: 2026-08-24T13:09:23.762155+00:00
- Grader model: `llama3.2:3b`
- Cases graded by the model: 20/20

- **Sufficiency accuracy: 0.950**
- Unanswerable questions correctly refused: 2/3
- Paraphrase questions preserved: 2/2
- Verdict precision: 0.319
- Verdict recall: 1.000
- Latency: 7931 ms mean, 14480 ms max

| Case | Category | Retrieved | Kept | Grade | OK | ms |
|---|---|---:|---:|---|:--:|---:|
| `email_validator` | single_document | 8 | 8 | correct | yes | 8582 |
| `python_version` | single_document | 8 | 8 | correct | yes | 8841 |
| `readme_updates` | lexical_multi | 8 | 8 | correct | yes | 14480 |
| `vercel_routing` | multi_document | 8 | 8 | correct | yes | 8728 |
| `startup_blocking` | multi_document | 8 | 8 | correct | yes | 7799 |
| `free_tier` | multi_document | 8 | 8 | correct | yes | 10257 |
| `embeddings_provider` | multi_document | 8 | 8 | correct | yes | 8218 |
| `permissions` | multi_document | 8 | 8 | correct | yes | 6838 |
| `credentials_paraphrase` | paraphrase | 8 | 8 | correct | yes | 7062 |
| `slow_boot_paraphrase` | paraphrase | 8 | 8 | correct | yes | 8801 |
| `ec2_plans` | cross_source | 8 | 8 | correct | yes | 6383 |
| `connector_work` | cross_source | 8 | 8 | correct | yes | 5022 |
| `evaluation_gate` | single_document | 8 | 8 | correct | yes | 7046 |
| `sprint_velocity_negative` | negative | 8 | 8 | correct | NO | 7858 |
| `payment_gateway_negative` | negative | 8 | 0 | incorrect | yes | 5469 |
| `kubernetes_negative` | negative | 8 | 0 | incorrect | yes | 7169 |
| `bcrypt_rationale` | decision_rationale | 8 | 8 | correct | yes | 8651 |
| `embeddings_rationale` | decision_rationale | 8 | 8 | correct | yes | 9524 |
| `ec2_blocker_cross` | cross_source | 8 | 8 | correct | yes | 2863 |
| `boot_fix_cross` | cross_source | 8 | 8 | correct | yes | 9023 |

## Sufficiency failures

- `sprint_velocity_negative` (expected to keep nothing): Graded 8 retrieved chunk(s) sufficient; supporting passage: 'Deploy times went from ~90s to under 20s.'.
