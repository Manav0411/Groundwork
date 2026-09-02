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
 */
export function CitationRail({ citations, evidence = [], className }: CitationRailProps) {
  if (citations.length === 0) return null;

  const snippetFor = new Map(evidence.map((item) => [item.citation_id, item.snippet]));

  return (
    <aside className={`flex flex-col gap-4 ${className ?? ""}`} aria-label="Sources">
      {citations.map((citation, index) => {
        const style = sourceStyle(citation.source_type);
        const snippet = snippetFor.get(citation.id);

        return (
          <div
            key={citation.id}
            className={`evidence-rise border-l-4 pl-3 ${style.border}`}
            style={{ animationDelay: `${index * 110}ms` }}
          >
            <div
              className={`break-identifier mb-1.5 flex gap-2 font-mono text-[10px] font-semibold uppercase tracking-[0.1em] ${style.text}`}
            >
              <span>{citation.id}</span>
              <span>
                {style.label}
                {citation.title ? ` · ${citation.title}` : ""}
              </span>
            </div>

            {snippet ? (
              <p className="break-identifier m-0 font-serif text-[13.5px] leading-snug text-ink2">
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
          </div>
        );
      })}
    </aside>
  );
}
