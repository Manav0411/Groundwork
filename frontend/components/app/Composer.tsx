"use client";

import { useEffect, useRef } from "react";
import { Loader2, Send } from "lucide-react";

type ComposerProps = {
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
  pending: boolean;
  disabled: boolean;
  /** Why the composer is unavailable, when it is. */
  disabledReason?: string;
  projectName?: string;
};

/**
 * The one place on the site where a question can actually be typed.
 *
 * A textarea rather than an input, because the questions this system answers
 * best are long ones — "why did we choose X instead of Y" runs past the width
 * of a single-line field, and a field you cannot see the end of discourages
 * exactly the questions worth asking. Enter submits, Shift+Enter breaks a line.
 */
export function Composer({
  value,
  onChange,
  onSubmit,
  pending,
  disabled,
  disabledReason,
  projectName
}: ComposerProps) {
  const ref = useRef<HTMLTextAreaElement>(null);

  // Grow with the content instead of scrolling inside a fixed box. Reset to
  // auto first or the height only ever ratchets upward.
  useEffect(() => {
    const node = ref.current;
    if (!node) return;
    node.style.height = "auto";
    node.style.height = `${Math.min(node.scrollHeight, 220)}px`;
  }, [value]);

  return (
    <div className="border-2 border-ink bg-card">
      <label
        className="block border-b border-ruleSoft px-3.5 py-2.5 font-mono text-[10px] font-semibold uppercase tracking-[0.14em] text-ink3"
        htmlFor="agent-query"
      >
        Ask about {projectName ?? "your project"}
      </label>

      <div className="flex items-end gap-2 p-3">
        <textarea
          className="min-h-[52px] w-full flex-1 resize-none border-0 bg-transparent px-1 py-1.5 font-serif text-[17px] leading-snug text-ink outline-none placeholder:text-ink3 disabled:opacity-60"
          disabled={disabled}
          id="agent-query"
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              onSubmit();
            }
          }}
          placeholder="Why did we choose the grader model?"
          ref={ref}
          rows={1}
          value={value}
        />
        <button
          aria-label="Ask"
          className="flex h-11 w-11 shrink-0 items-center justify-center border-2 border-ink bg-ink text-card transition hover:bg-card hover:text-ink disabled:cursor-not-allowed disabled:opacity-50"
          disabled={disabled || pending || !value.trim()}
          onClick={onSubmit}
          type="button"
        >
          {pending ? (
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
          ) : (
            <Send className="h-4 w-4" aria-hidden />
          )}
        </button>
      </div>

      <p className="m-0 border-t border-ruleSoft px-3.5 py-2 font-mono text-[10px] uppercase tracking-[0.1em] text-ink3">
        {disabled && disabledReason ? disabledReason : "Enter to ask · Shift + Enter for a new line"}
      </p>
    </div>
  );
}
