import type { TraceStep } from "@/lib/api";
import { traceBars } from "./provenance";

type TraceStripProps = {
  trace: TraceStep[];
  className?: string;
};

/**
 * The agent run, drawn to scale.
 *
 * Bar widths are the real proportions of the measured durations, so the node
 * that dominates a turn looks like it does. Repeat entries — the corrective
 * loop re-entering `grade` — are drawn in a lighter tone and marked, because
 * "this node ran twice" is the single most interesting thing a trace can say.
 *
 * Nothing here is coloured by source. A pipeline node is not evidence, and
 * giving it an ink would break the rule that colour means provenance.
 */
export function TraceStrip({ trace, className }: TraceStripProps) {
  if (trace.length === 0) return null;

  const bars = traceBars(trace);
  const total = bars.reduce((sum, bar) => sum + Math.max(bar.durationMs, 0), 0);
  const loops = bars.filter((bar) => bar.repeated).length;

  return (
    <div className={`bg-boardDeep px-3.5 pb-4 pt-3.5 ${className ?? ""}`}>
      <div className="mb-3 flex flex-wrap justify-between gap-3 font-mono text-[10px] font-semibold uppercase tracking-[0.14em] text-ink2">
        <span>
          Trace · {bars.length} {bars.length === 1 ? "node" : "nodes"} entered
        </span>
        <span>
          {loops > 0 ? `${loops} corrective ${loops === 1 ? "cycle" : "cycles"}` : "no corrections"}
          {" · "}
          {total} ms
        </span>
      </div>

      <ol className="m-0 flex list-none flex-col gap-1.5 p-0">
        {bars.map((bar, index) => (
          <li
            key={`${bar.name}-${index}`}
            className="grid grid-cols-[15ch_minmax(0,1fr)_7ch] items-center gap-3 font-mono text-[10.5px] text-ink2"
            title={bar.summary || undefined}
          >
            <span className="break-identifier">
              {bar.name}
              {bar.repeated ? " ↺" : ""}
            </span>

            <span className="relative h-1.5 bg-ruleSoft">
              <span
                className={`evidence-draw absolute inset-y-0 ${
                  bar.status === "failed" ? "bg-bad" : bar.repeated ? "bg-rule" : "bg-ink"
                }`}
                style={{
                  left: `${bar.offsetPct}%`,
                  width: `${bar.widthPct}%`,
                  animationDelay: `${index * 90}ms`
                }}
              />
            </span>

            <span className="text-right font-semibold tabular-nums">{bar.durationMs} ms</span>
          </li>
        ))}
      </ol>
    </div>
  );
}
