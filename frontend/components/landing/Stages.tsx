import { Section } from "./Section";

/** A real sequence, so it is numbered. */
const STAGES = [
  {
    n: "Stage 01 — Ingest",
    title: "Read your sources, write to none",
    body: "Polling connectors with a ten-minute overlap cursor. Documents are upserted by content hash, so unchanged content keeps its chunks and its embeddings. Groundwork never writes back and takes no actions on your behalf.",
    data: [
      ["github", "commits + prs"],
      ["jira", "12 issues"],
      ["slack", "thread-per-doc"]
    ]
  },
  {
    n: "Stage 02 — Fuse",
    title: "Two rankings, one ordering",
    body: "Lexical search catches the identifier that embeddings blur. Vector search catches the paraphrase that keywords miss. Reciprocal rank fusion merges them without either needing to be trusted alone.",
    data: [
      ["full-text", "tsvector · gin"],
      ["vector", "pgvector · hnsw"],
      ["dimensions", "768"]
    ]
  },
  {
    n: "Stage 03 — Ground",
    title: "Grade the evidence before writing a word",
    body: "A grader decides whether what came back is sufficient, and a bounded corrective loop retries when it is not. Every marker is checked against the sources actually emitted; one that does not resolve is stripped and the grade downgraded.",
    data: [
      ["graph", "14 nodes · 17 edges"],
      ["cycles", "1"],
      ["unresolved", "stripped"]
    ]
  }
];

export function Stages() {
  return (
    <Section
      heading="Three stages, and the evidence survives all of them."
      id="pipeline"
      label="How it works"
      mark="§03"
    >
      <div className="mt-[clamp(28px,4vw,44px)] grid gap-[clamp(14px,2vw,24px)] md:grid-cols-3">
        {STAGES.map((stage) => (
          <article
            className="flex flex-col gap-3 border-2 border-ink bg-card px-4 pb-5 pt-4.5"
            key={stage.n}
          >
            <span className="font-mono text-[10.5px] font-semibold uppercase tracking-[0.18em] text-ink3">
              {stage.n}
            </span>
            <h3 className="m-0 font-sans text-lg font-bold leading-tight tracking-[-0.015em]">
              {stage.title}
            </h3>
            <p className="m-0 font-serif text-[15px] leading-normal text-ink2">{stage.body}</p>
            <dl className="mt-auto border-t border-ruleSoft pt-3 font-mono text-[10.5px] leading-[1.95] text-ink3">
              {stage.data.map(([label, value]) => (
                <div className="flex justify-between gap-3" key={label}>
                  <dt className="m-0">{label}</dt>
                  <dd className="break-identifier m-0 font-semibold tabular-nums text-ink">
                    {value}
                  </dd>
                </div>
              ))}
            </dl>
          </article>
        ))}
      </div>
    </Section>
  );
}
