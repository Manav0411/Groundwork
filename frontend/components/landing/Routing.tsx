import { Section } from "./Section";

/**
 * The colour system extends here, and this is where it earns its keep: a
 * GitHub dot on the commit question, Jira dots on the ticket questions. You can
 * read which source answers which kind of question without reading the labels.
 *
 * Slack appears twice, which is the point of the section. Colour marks where
 * evidence came from, not how it was fetched — the same source answers a
 * recency question with typed SQL and an interpretive one with a model.
 *
 * Every figure below is the sum of that run's own trace durations, measured
 * against the deployed backend on 3 Sep 2026 with the index freshly synced —
 * the same number the trace strip adds up to, so a reader can check it. Median
 * of three warm runs; the first call after the instance wakes is an order of
 * magnitude slower and is not what these claim to represent.
 */
const ROUTES = [
  {
    dot: "bg-gh",
    question: "What was the last commit by Manav0411?",
    how: "Typed SQL over normalised identities",
    time: "13 ms",
    calls: "0 model calls"
  },
  {
    dot: "bg-jira",
    question: "What is the status of GW-3?",
    how: "Typed SQL",
    time: "3 ms",
    calls: "0 model calls"
  },
  {
    dot: "bg-slack",
    question: "What was the last conversation on Slack?",
    how: "Typed SQL over thread recency",
    time: "3 ms",
    calls: "0 model calls"
  },
  {
    dot: "bg-jira",
    question: "Are all the tasks complete?",
    how: "SQL counting by status category",
    time: "5 ms",
    calls: "0 model calls"
  },
  {
    dot: "bg-slack",
    question: "Why did we choose the grader model?",
    how: "Hybrid retrieval → grade → cited synthesis",
    time: "1.6 s",
    calls: "8 model calls",
    rag: true
  }
];

export function Routing() {
  return (
    <Section
      aside={
        <div className="border-t-2 border-ink">
          {ROUTES.map((route) => (
            <div
              className={`grid grid-cols-[14px_minmax(0,1fr)_11ch] items-baseline gap-3.5 border-b border-rule py-4 ${
                route.rag ? "bg-gradient-to-r from-slack/[0.07] to-transparent" : ""
              }`}
              key={route.question}
            >
              <span className={`h-2.5 w-2.5 self-center rounded-full ${route.dot}`} />
              <span className="font-serif text-[17px] leading-snug">
                {route.question}
                <span className="break-identifier mt-1.5 block font-mono text-[10.5px] font-semibold uppercase tracking-[0.1em] text-ink3">
                  {route.how}
                </span>
              </span>
              <span className="text-right font-mono text-[15px] font-semibold tabular-nums">
                {route.time}
                <small className="mt-1 block text-[9.5px] font-semibold uppercase tracking-[0.1em] text-ink3">
                  {route.calls}
                </small>
              </span>
            </div>
          ))}
        </div>
      }
      heading="Some questions have exactly one right answer."
      id="routing"
      label="Two mechanisms"
      lede={
        <>
          Those never reach a model. Cosine similarity has no concept of{" "}
          <span className="font-mono text-[0.88em]">max(commit_time)</span> — routing an exact
          question through embeddings does not make the system more general, it makes it{" "}
          <b className="font-medium text-ink">confidently wrong</b>. A deterministic router sends
          them to typed SQL instead.
        </>
      }
      mark="§02"
    />
  );
}
