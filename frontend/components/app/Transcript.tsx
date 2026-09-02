"use client";

import { RotateCcw } from "lucide-react";
import type { QueryResponse } from "@/lib/api";
import { AnswerCard } from "@/components/evidence";

export type TranscriptTurn = {
  question: string;
  response: QueryResponse;
  elapsedMs: number;
};

type TranscriptProps = {
  turns: TranscriptTurn[];
  onReset: () => void;
  /** Rendered in place of an empty transcript — usually the recorded run. */
  placeholder?: React.ReactNode;
};

/**
 * The conversation, newest last.
 *
 * Every turn is an `<AnswerCard>` — the same component the landing page
 * renders. The old dashboard had its own answer markup here: citation chips,
 * a grade pill, a separate trace panel in a third column. All of it is gone,
 * because two renderings of one thing is how the two drift apart until one is
 * lying about what the system does.
 */
export function Transcript({ turns, onReset, placeholder }: TranscriptProps) {
  if (turns.length === 0) {
    return <>{placeholder}</>;
  }

  return (
    <section aria-label="Conversation" className="flex flex-col gap-5">
      <div className="flex items-center justify-between gap-3">
        <span className="font-mono text-[10px] font-semibold uppercase tracking-[0.18em] text-ink3">
          Conversation · {turns.length} {turns.length === 1 ? "turn" : "turns"}
        </span>
        <button
          className="flex items-center gap-1.5 border border-rule px-2.5 py-1.5 font-mono text-[10px] font-semibold uppercase tracking-[0.12em] text-ink2 transition hover:border-ink hover:text-ink"
          onClick={onReset}
          type="button"
        >
          <RotateCcw className="h-3 w-3" aria-hidden /> New conversation
        </button>
      </div>

      {turns.map((turn, index) => (
        <AnswerCard
          answer={turn.response}
          elapsedMs={turn.elapsedMs}
          key={`${turn.response.conversation_id}-${index}`}
          question={turn.question}
        />
      ))}
    </section>
  );
}
