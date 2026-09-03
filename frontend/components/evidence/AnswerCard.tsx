import type { QueryResponse } from "@/lib/api";
import { AnswerBody } from "./AnswerBody";
import { CitationRail } from "./CitationRail";
import { Refusal } from "./Refusal";
import { TraceStrip } from "./TraceStrip";
import { gradeStyle, isRefusal } from "./provenance";

type AnswerCardProps = {
  question: string;
  answer: QueryResponse;
  /** Wall time for the whole turn, when the caller measured it. */
  elapsedMs?: number;
  /** Shown when the run came from a fixture rather than the live backend. */
  footnote?: React.ReactNode;
  className?: string;
};

/**
 * One answer, mounted.
 *
 * This is the component the landing page and the app both render. They differ
 * only in where the `QueryResponse` came from — a recorded fixture or a live
 * call — which is the whole point: if the two ever drift visually, it is
 * because one of them is lying about what the system does.
 */
export function AnswerCard({
  question,
  answer,
  elapsedMs,
  footnote,
  className
}: AnswerCardProps) {
  if (isRefusal(answer)) {
    return <Refusal question={question} answer={answer} className={className} />;
  }

  const grade = gradeStyle(answer.retrieval_grade);
  // `tools_used[0]` is "planner" on every structured route, so the chip read
  // "route · planner" for exactly the answers whose route is the interesting
  // part. `query_type` is the backend's own name for the path taken, and the
  // schema exposes it because `tools_used` cannot tell `latest_commit` from
  // `commit_detail` — both reach the same tool.
  const route = answer.query_type ?? answer.tools_used[0] ?? "hybrid_rag";

  return (
    <article className={`border-2 border-ink bg-card shadow-mount ${className ?? ""}`}>
      <header className="flex flex-wrap items-center gap-x-3.5 gap-y-2 border-b border-ruleSoft px-3.5 py-2.5 font-mono text-[10.5px] font-semibold uppercase tracking-[0.12em] text-ink3">
        <span className="break-identifier border border-rule px-2 py-0.5 text-ink2">
          route · {route}
        </span>
        <span className={`border px-2 py-0.5 ${grade.border} ${grade.text}`}>
          grade · {grade.label}
        </span>
        {typeof elapsedMs === "number" ? (
          <span className="ml-auto text-ink2">answered in {(elapsedMs / 1000).toFixed(1)} s</span>
        ) : null}
      </header>

      <div className="px-3.5 pb-1.5 pt-5">
        <p className="m-0 max-w-[44ch] font-serif text-[clamp(19px,2.1vw,25px)] leading-tight text-ink">
          {question}
        </p>
        {/* A follow-up is answered as a standalone question. Showing the
            rewrite is the difference between a system that resolved the
            reference and one that guessed. */}
        {answer.resolved_query && answer.resolved_query !== question ? (
          <p className="break-identifier mt-2 font-mono text-[10.5px] uppercase tracking-[0.1em] text-ink3">
            resolved as · {answer.resolved_query}
          </p>
        ) : null}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_286px]">
        <div className="border-b border-ruleSoft px-3.5 pb-6 pt-4 lg:border-b-0 lg:border-r">
          <AnswerBody answer={answer.answer} citations={answer.citations} />
        </div>
        <CitationRail
          citations={answer.citations}
          evidence={answer.evidence}
          className="px-3.5 pb-6 pt-4"
        />
      </div>

      {/* An answer can be graded down without being refused — most often
          because the index is older than its staleness threshold. The backend
          says why in `unresolved_gaps`; dropping that leaves a reader with a
          coloured chip and no reason for it. */}
      {answer.unresolved_gaps.length > 0 ? (
        <div className="border-t border-ruleSoft px-3.5 py-3.5">
          <p className="m-0 mb-2 font-mono text-[10px] font-semibold uppercase tracking-[0.16em] text-warn">
            {answer.unresolved_gaps.length === 1 ? "Caveat" : "Caveats"}
          </p>
          <ul className="m-0 list-none p-0">
            {answer.unresolved_gaps.map((gap) => (
              <li
                className="break-identifier relative py-1 pl-5 font-mono text-[11.5px] leading-relaxed text-ink2"
                key={gap}
              >
                <span className="absolute left-0.5 top-[13px] h-[1.5px] w-2.5 bg-warn" aria-hidden />
                {gap}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <TraceStrip trace={answer.trace} className="border-t border-ruleSoft" />

      {footnote ? (
        <p className="break-identifier m-0 border-t border-ruleSoft px-3.5 py-2.5 font-mono text-[10.5px] leading-relaxed text-ink3">
          {footnote}
        </p>
      ) : null}
    </article>
  );
}
