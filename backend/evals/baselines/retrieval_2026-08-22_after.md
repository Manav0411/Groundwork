# Retrieval report — retrieval_dataset.jsonl

- Completed: 2026-08-22T11:40:57.516940+00:00
- Cases: 16
- Embeddings: enabled

| Metric | k=3 | k=5 | k=8 |
|---|---:|---:|---:|
| Recall@k | 0.678 | 0.771 | 0.792 |
| Precision@k | 0.583 | 0.425 | 0.281 |

- MRR (excludes negative cases): 1.000
- Lexical hit rate: 0.422 — share of returned chunks matching the query lexically at all
- Negative cases returning nothing: 0/3

| Case | Category | R@8 | P@8 | RR | Returned | Lex |
|---|---|---:|---:|---:|---:|---:|
| `email_validator` | single_document | 1.00 | 0.12 | 1.00 | 8 | 7 |
| `python_version` | single_document | 1.00 | 0.12 | 1.00 | 8 | 1 |
| `readme_updates` | lexical_multi | 1.00 | 0.50 | 1.00 | 8 | 6 |
| `vercel_routing` | multi_document | 1.00 | 0.38 | 1.00 | 8 | 7 |
| `startup_blocking` | multi_document | 1.00 | 0.75 | 1.00 | 8 | 7 |
| `free_tier` | multi_document | 1.00 | 0.62 | 1.00 | 8 | 5 |
| `embeddings_provider` | multi_document | 0.67 | 0.25 | 1.00 | 8 | 0 |
| `permissions` | multi_document | 1.00 | 0.38 | 1.00 | 8 | 3 |
| `credentials_paraphrase` | paraphrase | 1.00 | 0.25 | 1.00 | 8 | 1 |
| `slow_boot_paraphrase` | paraphrase | 1.00 | 0.38 | 1.00 | 8 | 0 |
| `ec2_plans` | cross_source | 1.00 | 0.25 | 1.00 | 8 | 4 |
| `connector_work` | cross_source | 1.00 | 0.38 | 1.00 | 8 | 2 |
| `evaluation_gate` | single_document | 1.00 | 0.12 | 1.00 | 8 | 1 |
| `sprint_velocity_negative` | negative | 0.00 | 0.00 | 0.00 | 8 | 0 |
| `payment_gateway_negative` | negative | 0.00 | 0.00 | 0.00 | 8 | 7 |
| `kubernetes_negative` | negative | 0.00 | 0.00 | 0.00 | 8 | 3 |

## Incomplete recall

- `embeddings_provider` missed 1: 90b826602cc7

## Negative cases that returned evidence anyway

- `sprint_velocity_negative` returned 8 chunk(s) for: "What is the Sprint 24 delivery velocity?"
- `payment_gateway_negative` returned 8 chunk(s) for: "What payment gateway integration issues are open?"
- `kubernetes_negative` returned 8 chunk(s) for: "How is the Kubernetes cluster configured?"
