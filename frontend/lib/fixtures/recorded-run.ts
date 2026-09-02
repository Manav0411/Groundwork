import type { QueryResponse } from "@/lib/api";

/**
 * A captured run, kept so the product has something honest to show when the
 * demo backend is asleep.
 *
 * The instance is stopped between demos to stay inside a free tier, which
 * means "the backend is down" is the *normal* state, not an edge case. Both
 * the landing page and the app fall back to this, labelled as recorded — a
 * real trace with real durations, not a mock-up of one.
 *
 * It is deliberately typed as `QueryResponse` rather than a looser shape: if
 * the API contract changes, this stops compiling, which is the only thing that
 * will remind anyone the fixture needs recapturing.
 *
 * Recapture with:
 *   curl -s -X POST "$API/query" -H 'Content-Type: application/json' \
 *     -H "X-API-Key: $KEY" \
 *     -d '{"project_id":"groundwork","query":"<question>"}' | jq
 */
export const RECORDED_QUESTION =
  "Why did we keep the 3B model for grading instead of moving to something larger?";

/** When the run below was captured, for the label on the card. */
export const RECORDED_AT = "30 Aug 2026";

export const RECORDED_ELAPSED_MS = 1572;

export const RECORDED_RUN: QueryResponse = {
  conversation_id: "recorded-2026-08-30",
  answer: [
    "Both larger candidates were measured and rejected. qwen3:4b scored worse on the same retrieval set — recall fell from 1.000 to 0.717, meaning it discarded evidence the corpus genuinely holds. [1]",
    "Grading is long-prompt classification returning a single bit, which is the shape where a small model wins. Residency mattered too: a 3B grader beside an 8B writer plus embeddings exceeded 16 GB. [2]",
    "The decision was carried into configuration rather than code, so the model is an environment variable and the finding is reproducible from the harness. [3]"
  ].join("\n\n"),
  retrieval_grade: "correct",
  tools_used: ["hybrid_rag"],
  citations: [
    {
      id: 1,
      source_type: "slack",
      title: "#groundwork-eng",
      url: null,
      timestamp: "2026-08-26T11:42:00Z"
    },
    {
      id: 2,
      source_type: "slack",
      title: "#groundwork-eng",
      url: null,
      timestamp: "2026-08-26T12:05:00Z"
    },
    {
      id: 3,
      source_type: "github",
      title: "commit 4f1c9ab",
      url: null,
      timestamp: "2026-08-26T15:20:00Z"
    }
  ],
  evidence: [
    {
      id: "slack-1",
      source_type: "slack",
      title: "#groundwork-eng",
      snippet:
        "“Re-ran the retrieval set on qwen3:4b. Recall@8 dropped to 0.717 from 1.000. It is dropping chunks that are actually relevant.”",
      citation_id: 1,
      authority: 0.9
    },
    {
      id: "slack-2",
      source_type: "slack",
      title: "#groundwork-eng",
      snippet:
        "“Grading is classification with a long prompt and a one-bit answer. Small models are fine at that. The writer is where size pays.”",
      citation_id: 2,
      authority: 0.85
    },
    {
      id: "gh-1",
      source_type: "github",
      title: "commit 4f1c9ab",
      snippet: "Move grader model to settings; record baseline in evals/baselines/inference.md",
      citation_id: 3,
      authority: 0.8
    }
  ],
  unresolved_gaps: [],
  trace: [
    { name: "guardrail", status: "completed", duration_ms: 2, summary: "Question accepted" },
    { name: "resolve", status: "completed", duration_ms: 1, summary: "Self-contained, no rewrite" },
    { name: "plan", status: "completed", duration_ms: 1, summary: "Routed to hybrid retrieval" },
    { name: "retrieve", status: "completed", duration_ms: 112, summary: "Lexical + vector, fused by RRF" },
    { name: "grade", status: "completed", duration_ms: 228, summary: "Evidence judged insufficient" },
    { name: "correct", status: "completed", duration_ms: 96, summary: "Query rewritten, retrieved again" },
    { name: "grade", status: "completed", duration_ms: 211, summary: "Evidence judged sufficient" },
    { name: "settle_evidence", status: "completed", duration_ms: 24, summary: "3 citations settled" },
    { name: "synthesize", status: "completed", duration_ms: 852, summary: "Answer written with markers" },
    { name: "validate", status: "completed", duration_ms: 44, summary: "3 of 3 markers resolved" }
  ],
  resolved_query: null
};

/**
 * A captured refusal. Kept separate because it is the state the product is
 * proudest of and the one a screenshot never shows.
 *
 * Note: unlike RECORDED_RUN this one is *composed*, not captured — the backend
 * was asleep when the page was built. Recapture it against a live instance
 * before treating it as evidence of anything.
 */
export const REFUSAL_QUESTION = "When did we last rotate the TLS certificate?";

export const RECORDED_REFUSAL: QueryResponse = {
  conversation_id: "recorded-refusal",
  answer: "",
  retrieval_grade: "incorrect",
  tools_used: ["hybrid_rag"],
  citations: [],
  evidence: [],
  unresolved_gaps: [
    "No commit message references certificate rotation",
    "No ticket in scope mentions TLS or renewal",
    "No thread on the subject after 12 Jun"
  ],
  trace: [
    { name: "guardrail", status: "completed", duration_ms: 2, summary: "Question accepted" },
    { name: "resolve", status: "completed", duration_ms: 1, summary: "Self-contained, no rewrite" },
    { name: "plan", status: "completed", duration_ms: 1, summary: "Routed to hybrid retrieval" },
    { name: "retrieve", status: "completed", duration_ms: 108, summary: "No passage above threshold" },
    { name: "grade", status: "completed", duration_ms: 219, summary: "Evidence judged insufficient" },
    { name: "correct", status: "completed", duration_ms: 94, summary: "Rewritten, still nothing" },
    { name: "grade", status: "completed", duration_ms: 204, summary: "Evidence judged insufficient" },
    { name: "validate", status: "completed", duration_ms: 12, summary: "Answer withheld" }
  ],
  resolved_query: null
};
