import { CitationMarker } from "@/components/evidence";
import Link from "next/link";

/**
 * The hero is the mechanism, drawn.
 *
 * Three fragments from three systems converge into one sentence that cites
 * them — which explains the product to someone who has never heard of it in
 * about three seconds, and does it without a slogan or a stock illustration.
 *
 * The fragments are ordered so the markers ascend in reading order: the source
 * cited first is listed first. Citations that count down are a small tell that
 * nobody checked.
 */
const FRAGMENTS = [
  {
    n: 1,
    source: "slack" as const,
    meta: "Slack · #groundwork-eng",
    text: "“Recall@8 dropped to 0.717 from 1.000…”",
    border: "border-l-slack",
    ink: "text-slack"
  },
  {
    n: 2,
    source: "jira" as const,
    meta: "Jira · GW-7 · done",
    text: "Evaluate qwen3:4b as grader replacement",
    border: "border-l-jira",
    ink: "text-jira"
  },
  {
    n: 3,
    source: "github" as const,
    meta: "GitHub · commit 4f1c9ab",
    text: "Move grader model to settings; record baseline",
    border: "border-l-gh",
    ink: "text-gh"
  }
];

export function Hero() {
  return (
    <header className="pt-[clamp(96px,13vh,136px)]">
      <div className="grid items-center gap-[clamp(26px,4vw,56px)] lg:grid-cols-[minmax(0,1fr)_minmax(0,1.05fr)]">
        <div>
          <p className="m-0 mb-4 flex items-baseline gap-3 font-mono text-[11px] font-semibold uppercase tracking-[0.2em] text-ink3">
            <span className="text-ink">§00</span> Engineering project intelligence
          </p>
          <h1 className="m-0 max-w-[15ch] text-balance font-sans text-[clamp(36px,5.2vw,72px)] font-bold leading-[0.98] tracking-[-0.035em]">
            Every sentence, traceable to its source.
          </h1>
          <p className="m-0 mt-5 max-w-[52ch] font-serif text-[clamp(16.5px,1.4vw,19px)] leading-normal text-ink2">
            A commit records what changed. A ticket records what is left. Only a thread records{" "}
            <b className="font-medium text-ink">why</b>. Groundwork reads all three and will not
            write a claim it cannot attribute.
          </p>
          <div className="mt-6 flex flex-wrap gap-2.5">
            <Link
              className="border-2 border-ink bg-ink px-5 py-3 font-mono text-[11.5px] font-semibold uppercase tracking-[0.13em] text-card no-underline transition hover:bg-card hover:text-ink"
              href="/app"
            >
              Open the app
            </Link>
            <a
              className="border-2 border-ink bg-transparent px-5 py-3 font-mono text-[11.5px] font-semibold uppercase tracking-[0.13em] text-ink no-underline transition hover:bg-ink hover:text-card"
              href="#measured"
            >
              See what is measured
            </a>
          </div>
        </div>

        <div className="grid items-center gap-4 lg:grid-cols-[minmax(0,1fr)_56px_minmax(0,1.05fr)] lg:gap-0">
          <div className="flex flex-col gap-2.5">
            {FRAGMENTS.map((fragment, index) => (
              <article
                className={`evidence-rise border-2 border-l-[6px] border-ink bg-card px-3 py-2.5 ${fragment.border}`}
                key={fragment.n}
                style={{ animationDelay: `${220 + index * 170}ms` }}
              >
                <div
                  className={`break-identifier mb-1 font-mono text-[9.5px] font-semibold uppercase tracking-[0.12em] ${fragment.ink}`}
                >
                  {fragment.n}&nbsp;&nbsp;{fragment.meta}
                </div>
                <p className="m-0 font-serif text-[13.5px] leading-snug text-ink2">
                  {fragment.text}
                </p>
              </article>
            ))}
          </div>

          {/* Drawn with stroke-dashoffset so the joins appear after the
              fragments they connect. Hidden when the columns stack, where
              they would point nowhere. */}
          <svg
            aria-hidden
            className="hidden h-full w-14 lg:block"
            preserveAspectRatio="none"
            viewBox="0 0 56 220"
          >
            {[
              { d: "M0 36 C 30 36, 26 110, 56 110", stroke: "#66185F" },
              { d: "M0 110 C 24 110, 32 110, 56 110", stroke: "#0B4FC4" },
              { d: "M0 184 C 30 184, 26 110, 56 110", stroke: "#24292F" }
            ].map((path) => (
              <path
                className="evidence-join"
                d={path.d}
                fill="none"
                key={path.d}
                stroke={path.stroke}
                strokeWidth={1.5}
                vectorEffect="non-scaling-stroke"
              />
            ))}
          </svg>

          <div
            className="evidence-rise border-2 border-ink bg-card px-4 pb-4 pt-4 shadow-mount"
            style={{ animationDelay: "1040ms" }}
          >
            <div className="mb-2.5 flex flex-wrap justify-between gap-2.5 font-mono text-[9.5px] font-semibold uppercase tracking-[0.14em] text-ink3">
              <span>Groundwork answers</span>
              <span>1.6 s · grade correct</span>
            </div>
            <p className="m-0 font-serif text-[clamp(16px,1.6vw,19px)] leading-snug text-ink">
              The 3B grader was kept because the larger candidate dropped recall to 71.7%
              <CitationMarker id={1} sourceType="slack" />, the decision was recorded as done
              <CitationMarker id={2} sourceType="jira" />, and it lives in configuration rather than
              code
              <CitationMarker id={3} sourceType="github" />.
            </p>
          </div>
        </div>
      </div>

      <dl className="mt-[clamp(34px,5vw,56px)] grid grid-cols-2 border-y-2 border-ink sm:grid-cols-4">
        {[
          { label: "Exact path", value: "20–40 ms", note: "0 model calls" },
          { label: "Cited path", value: "1.6 s", note: "up to 8 model calls" },
          { label: "Recall @ 8", value: "80.7%", note: "MRR 0.941" },
          { label: "New project", value: "100%", note: "8 of 8, suite unchanged" }
        ].map((stat, index) => (
          <div
            className={`px-4 pb-4 pt-3.5 ${index > 0 ? "border-l border-rule" : ""}`}
            key={stat.label}
          >
            <dt className="m-0 mb-1.5 font-mono text-[10px] font-semibold uppercase tracking-[0.14em] text-ink3">
              {stat.label}
            </dt>
            <dd className="m-0 font-mono text-lg font-semibold tabular-nums">
              {stat.value}
              {/* The qualifier travels with the number: "0 model calls" is
                  true of the exact path and false of the cited one. */}
              <small className="mt-1.5 block font-mono text-[9.5px] font-normal uppercase tracking-[0.09em] text-ink3">
                {stat.note}
              </small>
            </dd>
          </div>
        ))}
      </dl>
    </header>
  );
}
