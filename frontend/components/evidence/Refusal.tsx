import type { QueryResponse } from "@/lib/api";
import { gradeStyle, refusalKind } from "./provenance";

type RefusalProps = {
  question: string;
  answer: QueryResponse;
  className?: string;
};

const COPY = {
  "no-evidence": {
    heading: "Nothing on record",
    body: "Retrieval returned no passage that supports an answer. Rather than assemble a plausible one, the run stops here and reports what is missing."
  },
  untraceable: {
    heading: "Answer withheld",
    body: "Retrieval found supporting passages and an answer was written against them, but it cited none. An answer whose claims cannot be traced to a source is not shown, however plausible it reads."
  }
} as const;

/**
 * The state that makes every other answer worth trusting.
 *
 * This is composed as a considered state, not an error banner: no apology, no
 * "something went wrong", no retry prompt — the system did exactly what it is
 * supposed to do.
 *
 * It takes the whole response rather than a question and a list of gaps. The
 * earlier shape let the card contradict the run it was describing: it hardcoded
 * "nothing on record" over a caveat that said an answer *had* been written, and
 * hardcoded a grade of incorrect over runs the backend graded ambiguous.
 */
export function Refusal({ question, answer, className }: RefusalProps) {
  const kind = refusalKind(answer) ?? "no-evidence";
  const copy = COPY[kind];
  const grade = gradeStyle(answer.retrieval_grade);
  const gaps = answer.unresolved_gaps;

  return (
    <section className={`border-2 border-ink bg-card shadow-mountBad ${className ?? ""}`}>
      <div className="border-b border-ruleSoft px-4 pb-3.5 pt-4">
        <p className="mb-2 font-mono text-[9.5px] font-semibold uppercase tracking-[0.16em] text-ink3">
          Question
        </p>
        <p className="m-0 max-w-[44ch] font-serif text-[clamp(18px,2vw,23px)] leading-tight text-ink">
          {question}
        </p>
      </div>

      <div className="px-4 pb-5 pt-4">
        <h3 className="m-0 mb-2.5 font-mono text-xs font-semibold uppercase tracking-[0.2em] text-bad">
          {copy.heading}
        </h3>
        <p className="m-0 mb-4 max-w-[56ch] font-serif text-base leading-relaxed text-ink2">
          {copy.body}
        </p>

        {gaps.length > 0 ? (
          <ul className="m-0 list-none border-t border-ruleSoft p-0">
            {gaps.map((gap, index) => (
              <li
                key={index}
                className="break-identifier relative border-b border-ruleSoft py-2.5 pl-5 font-mono text-xs text-ink2"
              >
                <span className="absolute left-0.5 top-[15px] h-[1.5px] w-2.5 bg-bad" aria-hidden />
                {gap}
              </li>
            ))}
          </ul>
        ) : null}
      </div>

      <div className="flex flex-wrap items-center gap-x-3.5 gap-y-2 border-t border-ruleSoft px-3.5 py-2.5 font-mono text-[10.5px] font-semibold uppercase tracking-[0.12em] text-ink3">
        <span className={`border px-2 py-0.5 ${grade.border} ${grade.text}`}>
          grade · {grade.label}
        </span>
        <span className="border border-rule px-2 py-0.5 text-ink2">citations · 0</span>
        {/* Only meaningful when something was retrieved. On a no-evidence run
            there was nothing to retain, so the count would read as a bug. */}
        {kind === "untraceable" ? (
          <span className="break-identifier border border-rule px-2 py-0.5 text-ink2">
            evidence retrieved · {answer.evidence.length}
          </span>
        ) : null}
        <span className="ml-auto">answer withheld</span>
      </div>
    </section>
  );
}
