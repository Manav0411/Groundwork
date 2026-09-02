import { AnswerCard } from "@/components/evidence";
import Link from "next/link";
import {
  RECORDED_AT,
  RECORDED_ELAPSED_MS,
  RECORDED_QUESTION,
  RECORDED_RUN
} from "@/lib/fixtures/recorded-run";
import { Section } from "./Section";

/**
 * A presented run, not a simulated interface.
 *
 * This section used to carry a question field that could not answer anything —
 * a small lie sitting in the middle of a page whose whole argument is that the
 * product does not assert what it cannot back. There is now exactly one place
 * on the site where a question can be typed, and it is the one that answers.
 */
export function WatchItWork() {
  return (
    <Section
      heading="Not a mockup. The system, answering."
      id="live"
      label="Watch it work"
      lede={
        <>
          The trace is the pipeline reporting node by node: ten of fourteen entered, one corrective
          cycle, every duration <b className="font-medium text-ink">measured rather than estimated</b>.
        </>
      }
      mark="§01"
    >
      <div className="mt-[clamp(26px,3.5vw,42px)]">
        <AnswerCard
          answer={RECORDED_RUN}
          elapsedMs={RECORDED_ELAPSED_MS}
          footnote={
            <>
              <strong className="font-semibold text-ink">Recorded run.</strong> The demo backend
              sleeps between sessions to stay inside a free tier. Captured {RECORDED_AT} — a real
              trace, not a mock-up of one.
            </>
          }
          question={RECORDED_QUESTION}
        />
        <Link
          className="mt-4 inline-block border-2 border-ink bg-transparent px-5 py-3 font-mono text-[11.5px] font-semibold uppercase tracking-[0.13em] text-ink no-underline transition hover:bg-ink hover:text-card"
          href="/app"
        >
          Ask your own question
        </Link>
      </div>
    </Section>
  );
}
