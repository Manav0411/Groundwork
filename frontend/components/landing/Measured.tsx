import { Section } from "./Section";

/**
 * Percentages where a percentage is the honest unit. The test count stays a
 * count: "100% of tests passed" is meaningless when it is always 100.
 *
 * `src` is not rendered and is not the React key either. It records which
 * harness produced each figure so whoever edits this file can go and re-run it,
 * which is the section's whole claim — but the path on screen was detail the
 * reader had no use for. Keying on it would have serialised every path into the
 * server-component payload, so removing it from the markup alone would have
 * left them all readable in view-source.
 */
const MEASURES = [
  {
    value: "100%",
    text: "Of checks passed on a project the suite had never seen — 8 of 8, with every expectation derived from the database at run time, so the suite carries no knowledge of any corpus.",
    src: "evals/generalization_runner.py"
  },
  {
    value: "80.7%",
    text: "Recall@8 across the three-source corpus, at MRR 0.941.",
    src: "evals/baselines/retrieval.md"
  },
  {
    value: "95.0%",
    text: "Grader sufficiency accuracy. It refuses two of three deliberately unanswerable questions; the third is recorded as a known limitation rather than hidden.",
    src: "evals/baselines/grading.md"
  },
  {
    value: "97.6%",
    text: "Cut from a cited turn by moving generation off the instance — 67.9 s on cloud CPU down to 1.6 s, which is faster than the 8.1 s laptop it was built on. Measurement identified which part was slow.",
    src: "evals/baselines/deployment_inference.md"
  },
  {
    value: "71.7%",
    text: "Where a larger model landed. qwen3:4b dropped recall from 100% and was rejected. Model choice went against intuition twice, and both times the measurement won.",
    src: "evals/baselines/inference.md"
  },
  {
    value: "364",
    text: "Tests across two tiers. The default opens no socket: the model, the database and the rate limiter are all forced offline, because a tier whose result depends on what the developer happens to be running is not a tier.",
    src: "backend/tests"
  }
];

export function Measured() {
  return (
    <Section
      aside={
        <div className="border-t-2 border-ink">
          {MEASURES.map((measure) => (
            <div
              className="grid items-baseline gap-x-[clamp(16px,3vw,40px)] gap-y-1.5 border-b border-rule py-4.5 sm:grid-cols-[11ch_minmax(0,1fr)]"
              key={measure.value}
            >
              <span className="font-mono text-[clamp(21px,2.4vw,27px)] font-semibold tabular-nums tracking-[-0.02em]">
                {measure.value}
              </span>
              <span className="max-w-[54ch] font-serif text-[15.5px] leading-normal text-ink2">
                {measure.text}
              </span>
            </div>
          ))}
        </div>
      }
      heading="Every number here has a file you can run."
      id="measured"
      label="What is measured"
      lede={
        <>
          Nothing on this page is an estimate. Each figure is the output of a harness in{" "}
          <span className="font-mono text-[0.88em]">backend/evals/</span>, with the raw run committed
          beside it.
        </>
      }
      mark="§04"
    />
  );
}
