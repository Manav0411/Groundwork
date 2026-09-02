import type { Citation } from "@/lib/api";
import { citationsById, sourceStyle } from "./provenance";

type AnswerBodyProps = {
  answer: string;
  citations: Citation[];
  className?: string;
};

const MARKER = /\[(\d+)\]/g;

/**
 * Renders answer prose with its `[n]` markers turned into provenance-coloured
 * chips.
 *
 * One deliberate behaviour: a marker whose number has no matching citation is
 * rendered as plain text, not as a chip. The backend already strips unresolved
 * markers before returning, so this should never fire — but if it ever does,
 * the honest failure is a marker that looks unremarkable, not one that looks
 * verified. A coloured chip is a claim that the citation resolved.
 */
export function AnswerBody({ answer, citations, className }: AnswerBodyProps) {
  const byId = citationsById(citations);
  const paragraphs = answer.split(/\n{2,}/).filter((block) => block.trim().length > 0);

  return (
    <div className={className}>
      {paragraphs.map((paragraph, index) => (
        <p
          key={index}
          className="mb-4 max-w-[54ch] font-serif text-[16.5px] leading-relaxed text-ink last:mb-0"
        >
          {renderWithMarkers(paragraph, byId)}
        </p>
      ))}
    </div>
  );
}

function renderWithMarkers(text: string, byId: Map<number, Citation>) {
  const nodes: React.ReactNode[] = [];
  let cursor = 0;
  let match: RegExpExecArray | null;

  MARKER.lastIndex = 0;
  while ((match = MARKER.exec(text)) !== null) {
    if (match.index > cursor) nodes.push(text.slice(cursor, match.index));

    const id = Number(match[1]);
    const citation = byId.get(id);
    nodes.push(
      citation ? (
        <CitationMarker key={`${id}-${match.index}`} id={id} sourceType={citation.source_type} />
      ) : (
        match[0]
      )
    );
    cursor = match.index + match[0].length;
  }

  if (cursor < text.length) nodes.push(text.slice(cursor));
  return nodes;
}

type CitationMarkerProps = {
  id: number;
  sourceType: string;
};

/** The marker is the smallest place the colour system does real work. */
export function CitationMarker({ id, sourceType }: CitationMarkerProps) {
  const style = sourceStyle(sourceType);
  return (
    <sup
      className={`${style.bg} ml-[3px] rounded-none px-[5px] py-[2px] align-[2px] font-mono text-[0.66em] font-semibold text-card`}
    >
      {id}
    </sup>
  );
}
