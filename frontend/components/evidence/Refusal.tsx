import type { QueryResponse } from "@/lib/api";

type RefusalProps = {
  question: string;
  gaps: QueryResponse["unresolved_gaps"];
  className?: string;
};

/**
 * The state that makes every other answer worth trusting.
 *
 * When retrieval returns nothing that supports an answer, the run stops and
 * reports what is missing. This is composed as a considered state, not an
 * error banner: no apology, no "something went wrong", no retry prompt — the
 * system did exactly what it is supposed to do.
 */
export function Refusal({ question, gaps, className }: RefusalProps) {
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
          Nothing on record
        </h3>
        <p className="m-0 mb-4 max-w-[56ch] font-serif text-base leading-relaxed text-ink2">
          Retrieval returned no passage that supports an answer. Rather than assemble a plausible
          one, the run stops here and reports what is missing.
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
        <span className="border border-bad px-2 py-0.5 text-bad">grade · incorrect</span>
        <span className="border border-rule px-2 py-0.5 text-ink2">citations · 0</span>
        <span className="ml-auto">answer withheld</span>
      </div>
    </section>
  );
}
