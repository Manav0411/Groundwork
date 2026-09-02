import type { Citation, QueryResponse } from "@/lib/api";

/**
 * Provenance is the colour system.
 *
 * A source's ink follows its evidence everywhere: the citation marker inside
 * the prose, the entry in the rail, the badge on a routing row. That is what
 * lets a reader see that an answer rests on two Slack threads and one commit
 * before reading a word of it.
 *
 * The backend sends `source_type` as a free-form string, so this normalises
 * rather than switching on exact values, and falls back to neutral ink for a
 * connector that does not exist yet. A new source showing up as grey is a
 * missing entry here; it is not a broken page.
 */
export type SourceKey = "github" | "jira" | "slack" | "other";

type SourceStyle = {
  /** Tailwind class painting the source's ink as a background (markers). */
  bg: string;
  /** Tailwind class painting it as text (rail headings). */
  text: string;
  /** Tailwind class painting it as a left border (rail entries, fragments). */
  border: string;
  /** Human label, already cased for display. */
  label: string;
};

const STYLES: Record<SourceKey, SourceStyle> = {
  github: { bg: "bg-gh", text: "text-gh", border: "border-l-gh", label: "GitHub" },
  jira: { bg: "bg-jira", text: "text-jira", border: "border-l-jira", label: "Jira" },
  slack: { bg: "bg-slack", text: "text-slack", border: "border-l-slack", label: "Slack" },
  other: { bg: "bg-ink3", text: "text-ink3", border: "border-l-rule", label: "Source" }
};

export function sourceKey(sourceType: string | null | undefined): SourceKey {
  const value = (sourceType ?? "").toLowerCase();
  if (value.includes("github") || value.includes("commit") || value.includes("pull")) return "github";
  if (value.includes("jira") || value.includes("issue") || value.includes("ticket")) return "jira";
  if (value.includes("slack") || value.includes("thread") || value.includes("message")) return "slack";
  return "other";
}

export function sourceStyle(sourceType: string | null | undefined): SourceStyle {
  return STYLES[sourceKey(sourceType)];
}

/** Index citations by their `id`, which is what `[n]` markers refer to. */
export function citationsById(citations: Citation[]): Map<number, Citation> {
  return new Map(citations.map((citation) => [citation.id, citation]));
}

/**
 * Grade is a separate three-value semantic scale, unrelated to provenance.
 * These classes are only legible on `card`; see the note in tailwind.config.ts.
 */
export function gradeStyle(grade: QueryResponse["retrieval_grade"]) {
  if (grade === "correct") return { text: "text-ok", border: "border-ok", label: "correct" };
  if (grade === "ambiguous") return { text: "text-warn", border: "border-warn", label: "ambiguous" };
  return { text: "text-bad", border: "border-bad", label: "incorrect" };
}

/**
 * An answer is a refusal when nothing was retrieved that supports it. The
 * backend expresses that as zero citations rather than a distinct status, so
 * the check lives here instead of being repeated at each call site.
 */
export function isRefusal(answer: QueryResponse): boolean {
  return answer.citations.length === 0;
}

/**
 * Trace steps arrive in execution order with a duration each. The waterfall
 * needs a start offset per step, which is the sum of everything before it.
 * Percentages are of the total, so a step that dominates a run looks like it.
 */
export type TraceBar = {
  name: string;
  durationMs: number;
  /** Percentage offset from the start of the run. */
  offsetPct: number;
  /** Percentage width, floored so a 1 ms node stays visible. */
  widthPct: number;
  /** A node entered more than once — the corrective loop. */
  repeated: boolean;
  status: string;
  summary: string;
};

export function traceBars(trace: QueryResponse["trace"]): TraceBar[] {
  const total = trace.reduce((sum, step) => sum + Math.max(step.duration_ms, 0), 0) || 1;
  const seen = new Set<string>();
  let elapsed = 0;

  return trace.map((step) => {
    const duration = Math.max(step.duration_ms, 0);
    const offsetPct = (elapsed / total) * 100;
    elapsed += duration;
    const repeated = seen.has(step.name);
    seen.add(step.name);

    return {
      name: step.name,
      durationMs: step.duration_ms,
      offsetPct,
      widthPct: Math.max((duration / total) * 100, 0.8),
      repeated,
      status: step.status,
      summary: step.summary
    };
  });
}
