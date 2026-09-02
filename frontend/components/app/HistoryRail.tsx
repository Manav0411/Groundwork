"use client";

import { MessageSquareText, Trash2 } from "lucide-react";
import type { StoredConversation } from "@/lib/conversationStore";

type HistoryRailProps = {
  conversations: StoredConversation[];
  activeId: string | null;
  onOpen: (conversation: StoredConversation) => void;
  onDelete: (id: string) => void;
};

function relative(timestamp: number): string {
  const seconds = Math.round((Date.now() - timestamp) / 1000);
  if (seconds < 60) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

/**
 * Past threads for the selected project.
 *
 * Scoped to the project because a conversation is: the backend rejects a
 * conversation id that crosses projects, so showing one here would offer a
 * thread that cannot be continued.
 */
export function HistoryRail({ conversations, activeId, onOpen, onDelete }: HistoryRailProps) {
  if (conversations.length === 0) return null;

  return (
    <section aria-label="Recent conversations" className="mt-6">
      <p className="m-0 mb-3 font-mono text-[10px] font-semibold uppercase tracking-[0.18em] text-ink3">
        Recent
      </p>
      <ul className="m-0 flex list-none flex-col gap-1.5 p-0">
        {conversations.map((conversation) => {
          const active = conversation.id === activeId;
          return (
            <li className="group relative" key={conversation.id}>
              <button
                aria-current={active ? "true" : undefined}
                className={`flex w-full items-start gap-2 border-2 py-2 pl-2.5 pr-9 text-left transition ${
                  active ? "border-ink bg-card" : "border-transparent hover:border-rule"
                }`}
                onClick={() => onOpen(conversation)}
                type="button"
              >
                <MessageSquareText
                  aria-hidden
                  className="mt-0.5 h-3.5 w-3.5 shrink-0 text-ink3"
                />
                <span className="min-w-0">
                  <span className="line-clamp-2 block font-serif text-[13px] leading-snug text-ink">
                    {conversation.title}
                  </span>
                  <span className="mt-1 block font-mono text-[9.5px] uppercase tracking-[0.1em] text-ink3">
                    {conversation.turns.length}{" "}
                    {conversation.turns.length === 1 ? "turn" : "turns"} ·{" "}
                    {relative(conversation.updatedAt)}
                  </span>
                </span>
              </button>
              <button
                aria-label={`Delete conversation: ${conversation.title}`}
                className="absolute right-1.5 top-1.5 border border-transparent p-1 text-ink3 opacity-0 transition hover:border-bad hover:text-bad focus-visible:opacity-100 group-hover:opacity-100"
                onClick={() => onDelete(conversation.id)}
                type="button"
              >
                <Trash2 aria-hidden className="h-3 w-3" />
              </button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
