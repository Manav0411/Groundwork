"use client";

import { useCallback, useSyncExternalStore } from "react";
import type { QueryResponse } from "@/lib/api";

/**
 * Conversations, kept in the browser.
 *
 * The backend already stores every turn, but `GET /conversations/{id}` returns
 * only the question, the answer text and the grade — deliberately, so that
 * history can never become a source for a later answer. That is the right call
 * on the server and it means the server cannot restore a full answer card:
 * there are no citations, no evidence and no trace to render.
 *
 * So the full response is kept client-side, where it already arrived intact.
 * localStorage restores a thread at full fidelity in the browser that asked;
 * the server endpoint remains the fallback for anywhere else, rendered as the
 * reduced thing it honestly is.
 */

const KEY = "groundwork:conversations:v1";
/** Enough to be useful, small enough that eviction is rare. */
const MAX_CONVERSATIONS = 12;
const MAX_TURNS = 30;

export type StoredTurn = {
  question: string;
  response: QueryResponse;
  elapsedMs: number;
};

export type StoredConversation = {
  id: string;
  projectId: string;
  /** The first question asked, which is what a person recognises it by. */
  title: string;
  updatedAt: number;
  turns: StoredTurn[];
};

/*
 * Subscription plumbing.
 *
 * localStorage is an external store, so React reads it through
 * useSyncExternalStore rather than copying it into state inside an effect.
 * getSnapshot has to be referentially stable or the component re-renders
 * forever, so the parsed array is cached against the raw string it came from.
 *
 * The browser fires `storage` only for *other* tabs, so writes in this tab
 * notify listeners explicitly. Between the two, every tab stays current.
 */
const listeners = new Set<() => void>();
let cachedRaw: string | null = null;
let cachedValue: StoredConversation[] = [];
const EMPTY: StoredConversation[] = [];

function emit() {
  cachedRaw = null;
  listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  const onStorage = (event: StorageEvent) => {
    if (event.key === KEY || event.key === null) emit();
  };
  window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", onStorage);
  };
}

function getSnapshot(): StoredConversation[] {
  if (typeof window === "undefined") return EMPTY;
  let raw: string | null;
  try {
    raw = window.localStorage.getItem(KEY);
  } catch {
    return EMPTY;
  }
  if (raw === cachedRaw) return cachedValue;
  cachedRaw = raw;
  try {
    const parsed = raw ? JSON.parse(raw) : [];
    cachedValue = Array.isArray(parsed) ? (parsed as StoredConversation[]) : EMPTY;
  } catch {
    cachedValue = EMPTY;
  }
  return cachedValue;
}

/** Server render has no localStorage; the same empty array keeps it stable. */
function getServerSnapshot(): StoredConversation[] {
  return EMPTY;
}

/** Conversations for one project, newest first, kept in sync with storage. */
export function useConversations(projectId: string | null): StoredConversation[] {
  const conversations = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
  const select = useCallback(
    (all: StoredConversation[]) =>
      all
        .filter((c) => !projectId || c.projectId === projectId)
        .sort((a, b) => b.updatedAt - a.updatedAt),
    [projectId]
  );
  return select(conversations);
}

function read(): StoredConversation[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? (parsed as StoredConversation[]) : [];
  } catch {
    // Corrupt or unreadable storage is not worth failing a page load over.
    return [];
  }
}

function write(conversations: StoredConversation[]): void {
  if (typeof window === "undefined") return;
  let queue = conversations.slice(0, MAX_CONVERSATIONS);
  // Quota is per-origin and a trace-heavy thread is not small. Drop the oldest
  // conversation and retry rather than losing the write — and the thread the
  // person is actually in is newest, so it survives.
  for (let attempt = 0; attempt < MAX_CONVERSATIONS; attempt++) {
    try {
      window.localStorage.setItem(KEY, JSON.stringify(queue));
      emit();
      return;
    } catch {
      if (queue.length <= 1) return;
      queue = queue.slice(0, queue.length - 1);
    }
  }
}

export function listConversations(projectId?: string): StoredConversation[] {
  const all = read().sort((a, b) => b.updatedAt - a.updatedAt);
  return projectId ? all.filter((c) => c.projectId === projectId) : all;
}

export function loadConversation(id: string): StoredConversation | null {
  return read().find((c) => c.id === id) ?? null;
}

/** Append a turn, creating the conversation on the first one. */
export function saveTurn(projectId: string, turn: StoredTurn): void {
  const id = turn.response.conversation_id;
  const existing = read();
  const found = existing.find((c) => c.id === id);

  const updated: StoredConversation = found
    ? { ...found, updatedAt: Date.now(), turns: [...found.turns, turn].slice(-MAX_TURNS) }
    : {
        id,
        projectId,
        title: turn.question,
        updatedAt: Date.now(),
        turns: [turn]
      };

  write([updated, ...existing.filter((c) => c.id !== id)]);
}

export function deleteConversation(id: string): void {
  write(read().filter((c) => c.id !== id));
}
