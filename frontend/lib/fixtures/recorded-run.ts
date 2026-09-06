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
 * will remind anyone the fixture needs recapturing. That guard catches a
 * changed *shape*; it cannot catch a changed *pipeline*, which is how the
 * previous capture went stale — it predated entailment checking and the node
 * renaming, so §01 showed a trace the running system no longer produced.
 * Recapture whenever a node is added, removed or renamed.
 *
 * Recapture with:
 *   curl -s -X POST "$API/query" -H 'Content-Type: application/json' \
 *     -H "X-API-Key: $KEY" \
 *     -d '{"project_id":"groundwork","query":"<question>"}' | jq
 *
 * Sync all three connectors first. An index older than its staleness threshold
 * adds a caveat and downgrades the grade, which would be baked into the fixture.
 */
export const RECORDED_QUESTION = "Why did we delete the synthetic demo evidence?";

/** When the run below was captured, for the label on the card. */
export const RECORDED_AT = "6 Sep 2026";

/** Median of three warm runs measured on the instance, so it excludes the public chain. */
export const RECORDED_ELAPSED_MS = 1828;

export const RECORDED_RUN: QueryResponse = {
  conversation_id: "recorded-2026-09-06",
  answer: [
    "The synthetic demo evidence was removed because it consisted of invented blockers and sprint‑plan documents that had been ingested as real evidence and returned to users with citations, creating a leak of fabricated information [1][3].",
    "Deleting it also emptied the sample projects so they now follow the normal “no‑evidence” path, preventing the mechanism from unintentionally resurfacing fake data in future runs [1][3]."
  ].join("\n\n"),
  retrieval_grade: "correct",
  query_type: "weekly_project_brief",
  tools_used: ["planner", "postgres_fts", "pgvector", "retrieval_grader"],
  // Markers run 1 and 3, not 1 and 2: the writer cited two of the eight retrieved
  // chunks and the validator dropped the rest. Renumbering them would be tidier
  // and would misrepresent what the run did.
  citations: [
    {
      id: 1,
      source_type: "slack",
      title: "#all-groundwork — Why did we delete the synthetic demo evidence?",
      url: "https://groundwork-0fg6997.slack.com/archives/C0BRTQ9BZ7H/p1787940734896229",
      timestamp: "2026-08-28T18:12:45.116299+00:00"
    },
    {
      id: 3,
      source_type: "github",
      title: "remove the last hardcoded answers and the demo-project default",
      url: "https://github.com/Manav0411/Groundwork/commit/94010749eabab16dd8316d779b885ae8a9806b93",
      timestamp: "2026-08-26T15:54:54+00:00"
    }
  ],
  evidence: [
    {
      id: "chunk-118",
      source_type: "slack",
      title: "#all-groundwork — Why did we delete the synthetic demo evidence?",
      snippet:
        "Slack thread in #all-groundwork started by Manav Goel with 3 message(s). Manav Goel: Why did we delete the synthetic demo evidence? Manav Goel: It was invented blockers and sprint plans, ingested as real documents and returned to users with citations. Scoping it to the two sample projects contained the leak but kept the mechanism, so a later change could reopen it. Manav Goel: Deleted outright. The sample projects are empty shells now and take the ordinary no-evidence path: grade incorrect, zero",
      citation_id: 1,
      authority: 0.8
    },
    {
      id: "chunk-66",
      source_type: "github",
      title: "remove the last hardcoded answers and the demo-project default",
      snippet:
        "Git commit 94010749eabab16dd8316d779b885ae8a9806b93 by Manav0411. Commit message: remove the last hardcoded answers and the demo-project default The sample projects carried invented evidence: fixture blockers and sprint plans, ingested as real documents and returned with citations. Scoping them to project-atlas/project-orion contained the leak but kept the mechanism. They are empty projects now and take the ordinary no-evidence path. Also drops the canned weekly brief in llm.py, which carried [1",
      citation_id: 3,
      authority: 0.8
    }
  ],
  unresolved_gaps: [],
  trace: [
    {
      name: "Input Guardrail",
      status: "completed",
      duration_ms: 0,
      summary: "Input reads as a question; admitted to the pipeline."
    },
    {
      name: "Follow-up Resolution",
      status: "completed",
      duration_ms: 0,
      summary: "First turn in the conversation; nothing to resolve against."
    },
    {
      name: "Planner",
      status: "completed",
      duration_ms: 0,
      summary: "Classified as weekly_project_brief; selected hybrid full-text/vector retrieval."
    },
    {
      name: "Hybrid Retriever",
      status: "completed",
      duration_ms: 92,
      summary: "Retrieved 8 persisted chunk(s) with hybrid full-text/vector search."
    },
    {
      name: "Retrieval Grader",
      status: "completed",
      duration_ms: 557,
      summary: "Graded 8 retrieved chunk(s) sufficient; supporting passage: 'Deleted outright.'."
    },
    {
      name: "Answer Generator",
      status: "completed",
      duration_ms: 548,
      summary: "Generated answer with openai_compat model openai/gpt-oss-120b."
    },
    {
      name: "Entailment Check",
      status: "completed",
      duration_ms: 597,
      summary: "Checked 2 claim(s) against cited evidence; all supported."
    },
    {
      name: "Citation Validator",
      status: "completed",
      duration_ms: 0,
      summary:
        "Validated 2 citation marker(s) against emitted evidence. Dropped 6 retrieved citation(s) the answer did not reference."
    }
  ],
  resolved_query: null
};

/**
 * A captured refusal, from the live backend on 6 Sep 2026, immediately after a
 * successful sync of all three connectors.
 *
 * This is the state the product is proudest of and the one a screenshot never
 * shows. It is worth reading the trace rather than just the verdict: retrieval
 * ran, the grader rejected it, the corrective loop rewrote the question, ran
 * again, was rejected again, widened the pool to sixteen chunks, and was
 * rejected a third time. The system did not decline because it was lazy.
 *
 * The question matters as much as the trace. An earlier capture used "What was
 * the last conversation on slack?", which refused only because no route existed
 * for it — `structured_slack` now answers that in 3 ms, which made the old
 * fixture a demonstration of a gap that had been closed. Pricing was never in
 * any of the three connectors and never will be, so this refuses for want of
 * evidence rather than for want of a route. That is the policy §05 claims.
 *
 * Note the entailment step still runs and reports honestly that there was
 * nothing to check: a refusal makes no claim, so there is no claim to verify.
 */
export const REFUSAL_QUESTION = "What did we decide about pricing?";

export const RECORDED_REFUSAL: QueryResponse = {
  conversation_id: "recorded-refusal-2026-09-06",
  answer:
    "I could not find any indexed evidence for this question in groundwork. Sync the project's connected sources, or rephrase the question, and ask again.",
  retrieval_grade: "incorrect",
  query_type: "weekly_project_brief",
  tools_used: ["planner", "postgres_fts", "pgvector", "retrieval_grader", "corrective_retrieval"],
  citations: [],
  evidence: [],
  unresolved_gaps: [
    "No indexed evidence matched this question, so no part of an answer could be supported."
  ],
  trace: [
    {
      name: "Input Guardrail",
      status: "completed",
      duration_ms: 0,
      summary: "Input reads as a question; admitted to the pipeline."
    },
    {
      name: "Follow-up Resolution",
      status: "completed",
      duration_ms: 0,
      summary: "First turn in the conversation; nothing to resolve against."
    },
    {
      name: "Planner",
      status: "completed",
      duration_ms: 0,
      summary: "Classified as weekly_project_brief; selected hybrid full-text/vector retrieval."
    },
    {
      name: "Hybrid Retriever",
      status: "completed",
      duration_ms: 87,
      summary: "Retrieved 8 persisted chunk(s) with hybrid full-text/vector search."
    },
    {
      name: "Retrieval Grader",
      status: "completed",
      duration_ms: 469,
      summary:
        "Graded the 8 retrieved chunk(s) insufficient: no passage states What did we decide about pricing?."
    },
    {
      name: "Corrective Retrieval 1",
      status: "completed",
      duration_ms: 561,
      summary:
        "Attempt 1: rewrote the question as 'What decision was made regarding pricing?'. Re-retrieved 8 chunk(s)."
    },
    {
      name: "Retrieval Grader",
      status: "completed",
      duration_ms: 473,
      summary: "Graded the 8 retrieved chunk(s) insufficient: no passage states pricing decision."
    },
    {
      name: "Corrective Retrieval 2",
      status: "completed",
      duration_ms: 87,
      summary: "Attempt 2: widened the candidate pool. Re-retrieved 16 chunk(s)."
    },
    {
      name: "Retrieval Grader",
      status: "completed",
      duration_ms: 548,
      summary:
        "Graded the 16 retrieved chunk(s) insufficient: no passage states What did we decide about pricing?."
    },
    {
      name: "Entailment Check",
      status: "completed",
      duration_ms: 0,
      summary: "No cited claim to check."
    },
    {
      name: "Citation Validator",
      status: "completed",
      duration_ms: 0,
      summary: "No citation emitted and none claimed."
    }
  ],
  resolved_query: null
};
