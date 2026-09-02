import type { ReactNode } from "react";

type SectionProps = {
  id?: string;
  /** The numbered mark, e.g. "§02". Sections are a reading order, so they are
   *  numbered; the boundary cards are not a sequence, so they are not. */
  mark: string;
  label: string;
  heading: ReactNode;
  lede?: ReactNode;
  children?: ReactNode;
  /** Two-column layout with the heading column sticky beside a long list. */
  aside?: ReactNode;
};

/**
 * The shell every section shares: a numbered mark, a heading, an optional
 * lede, and either a full-width body or a sticky heading column beside one.
 */
export function Section({ id, mark, label, heading, lede, children, aside }: SectionProps) {
  const head = (
    <div>
      <p className="m-0 mb-4 flex items-baseline gap-3 font-mono text-[11px] font-semibold uppercase tracking-[0.2em] text-ink3">
        <span className="text-ink">{mark}</span> {label}
      </p>
      <h2 className="m-0 max-w-[18ch] text-balance font-sans text-[clamp(26px,3.8vw,46px)] font-bold leading-[1.02] tracking-[-0.03em]">
        {heading}
      </h2>
      {lede ? (
        <p className="m-0 mt-4 max-w-[52ch] font-serif text-[clamp(16.5px,1.4vw,19px)] leading-normal text-ink2">
          {lede}
        </p>
      ) : null}
    </div>
  );

  return (
    <section className="pt-[clamp(56px,7.5vw,104px)] scroll-mt-24" id={id}>
      <div className="pb-[clamp(56px,7.5vw,104px)]">
        {aside ? (
          <div className="grid items-start gap-[clamp(22px,4vw,56px)] lg:grid-cols-[minmax(0,0.85fr)_minmax(0,1.15fr)]">
            {/* The heading column is a third the height of the list beside
                it; sticking it keeps the claim in view while its evidence
                scrolls past, instead of leaving a column of dead board. */}
            <div className="lg:sticky lg:top-24">{head}</div>
            <div>{aside}</div>
          </div>
        ) : (
          <>
            {head}
            {children}
          </>
        )}
      </div>
    </section>
  );
}
