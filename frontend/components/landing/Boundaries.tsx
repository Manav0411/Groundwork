import { Refusal } from "@/components/evidence";
import { RECORDED_REFUSAL, REFUSAL_QUESTION } from "@/lib/fixtures/recorded-run";
import { Section } from "./Section";

const BOUNDS = [
  {
    tag: "By design",
    title: "No evidence, no answer",
    body: "The screen above is the whole policy. Zero citations, a grade of incorrect, and an explicit gap — never a plausible-sounding guess.",
    limit: false
  },
  {
    tag: "By design",
    title: "Read-only, permanently",
    body: "Groundwork never writes back to GitHub, Jira or Slack. Comparable products sync bidirectionally; this is a chosen boundary, not a missing feature.",
    limit: false
  },
  {
    tag: "Known limit",
    title: "A claim is a span, not a sentence",
    body: "Each cited claim is checked against the passage it cites, and one the evidence does not state downgrades the answer. The unit is the text a marker ends, so a paragraph-trailing marker claims the whole paragraph — generous, and honest about it.",
    limit: true
  },
  {
    tag: "Known limit",
    title: "Polling, not webhooks",
    body: "No public ingress and no secret rotation to manage. The cost is real: a freshly pushed commit with an unusually old author timestamp can fall outside the overlap window.",
    limit: true
  }
];

/**
 * The refusal is rendered by the same `Refusal` component the app uses, from
 * the same fixture. Describing this state in a paragraph was always weaker
 * than showing it — and showing it twice from two implementations would be
 * weaker still.
 */
export function Boundaries() {
  return (
    <Section
      heading="The most important screen is the one that says no."
      id="bounds"
      label="Boundaries"
      lede="Ask something the corpus does not cover and Groundwork does not improvise. This is a composed state, not an error banner — and it is the state that makes every other answer worth trusting."
      mark="§05"
    >
      <Refusal
        answer={RECORDED_REFUSAL}
        className="mt-[clamp(26px,4vw,42px)]"
        question={REFUSAL_QUESTION}
      />

      <div className="mt-[clamp(22px,3vw,34px)] grid gap-[clamp(14px,2vw,22px)] sm:grid-cols-2">
        {BOUNDS.map((bound) => (
          <article className="border-2 border-ink bg-card px-4 py-4.5" key={bound.title}>
            <span
              className={`mb-3 block font-mono text-[10px] font-semibold uppercase tracking-[0.16em] ${
                bound.limit ? "text-bad" : "text-ok"
              }`}
            >
              {bound.tag}
            </span>
            <h3 className="m-0 font-sans text-lg font-bold leading-tight tracking-[-0.015em]">
              {bound.title}
            </h3>
            <p className="m-0 mt-2 font-serif text-[14.5px] leading-normal text-ink2">
              {bound.body}
            </p>
          </article>
        ))}
      </div>
    </Section>
  );
}
