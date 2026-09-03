import type { Citation, EvidenceItem } from "@/lib/api";
import { sourceStyle } from "./provenance";

type CitationRailProps = {
  citations: Citation[];
  /** Snippets, keyed to citations by `citation_id`. Optional: a citation
   *  without retrieved evidence still lists, just without a quotation. */
  evidence?: EvidenceItem[];
  className?: string;
};

/**
 * The sources, in a margin beside the prose that cites them.
 *
 * Each entry carries its source's ink on the left rule, matching the marker in
 * the answer, so the eye can pair a claim with its evidence without reading
 * either. Entries stagger in rather than appearing at once — a pure-CSS delay,
 * so this stays a server component.
 *
 * Snippets are whole indexed chunks, and eight of them ran to several screens —
 * the rail was taller than the answer it supported. Each entry now collapses to
 * its headline and opens on click, which keeps the rail scannable: the reader
 * sees which sources back the answer first, and reads one only when they want
 * to check a specific claim.
 *
 * Native `<details>` rather than React state, for three reasons: it keeps this a
 * server component with no client boundary, it is keyboard-operable and
 * screen-reader-announced without any work, and it survives with JavaScript
 * still loading. An entry with nothing to reveal renders as a plain row instead,
 * so no one clicks a disclosure that opens onto nothing.
 */
export function CitationRail({ citations, evidence = [], className }: CitationRailProps) {
  if (citations.length === 0) return null;

  const snippetFor = new Map(evidence.map((item) => [item.citation_id, item.snippet]));

  return (
    <aside className={`flex flex-col gap-4 ${className ?? ""}`} aria-label="Sources">
      {citations.map((citation, index) => {
        const style = sourceStyle(citation.source_type);
        const snippet = snippetFor.get(citation.id);
        const hasBody = Boolean(snippet) || Boolean(citation.url);

        const heading = (
          <>
            <span>{citation.id}</span>
            <span className="line-clamp-2 [[open]_&]:line-clamp-none">
              {style.label}
              {citation.title ? ` · ${citation.title}` : ""}
            </span>
          </>
        );

        const headingClass = `break-identifier flex gap-2 font-mono text-[10px] font-semibold uppercase tracking-[0.1em] ${style.text}`;

        const body = (
          <>
            {snippet ? (
              <p className="break-identifier m-0 mt-1.5 font-serif text-[13.5px] leading-snug text-ink2">
                {snippet}
              </p>
            ) : null}

            {citation.url ? (
              <a
                href={citation.url}
                target="_blank"
                rel="noreferrer noopener"
                className="mt-1.5 inline-block font-mono text-[10px] uppercase tracking-[0.1em] text-ink3 underline underline-offset-2 hover:text-ink"
              >
                Open source
              </a>
            ) : null}
          </>
        );

        return (
          <div
            key={citation.id}
            className={`evidence-rise border-l-4 pl-3 ${style.border}`}
            style={{ animationDelay: `${index * 110}ms` }}
          >
            {hasBody ? (
              <details className="group">
                <summary
                  className={`${headingClass} cursor-pointer list-none focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink [&::-webkit-details-marker]:hidden`}
                >
                  {heading}
                  {/* Mono glyph rather than a chevron: the rail is set in the
                      same face, and a rotating icon would be the only thing on
                      the surface that is not type. */}
                  <span aria-hidden className="ml-auto shrink-0 text-ink3">
                    <span className="group-open:hidden">+</span>
                    <span className="hidden group-open:inline">&minus;</span>
                  </span>
                </summary>
                {body}
              </details>
            ) : (
              <div className={headingClass}>{heading}</div>
            )}
          </div>
        );
      })}
    </aside>
  );
}
