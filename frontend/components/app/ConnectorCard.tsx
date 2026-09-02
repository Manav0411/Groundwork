"use client";

import { GitBranch, ListChecks, Loader2, MessagesSquare, RefreshCw } from "lucide-react";
import type { SourceKey } from "@/components/evidence";

/**
 * One card, three connectors.
 *
 * GitHub, Jira and Slack had three near-identical blocks in the old dashboard,
 * differing only in an icon, a noun and which "not connected yet" form they
 * showed. Keeping them separate meant every fix had to be made three times and
 * usually was not. The shape they actually share is: a source, a sync status,
 * a set of counts, and — before it is connected — one field to fill in.
 */

export type ConnectorStatus = {
  status?: string;
  last_succeeded_at?: string | null;
  last_error?: string | null;
};

export type ConnectorCounts = { label: string; value: string }[];

type ConnectorCardProps = {
  source: SourceKey;
  /** What this connector contributes that the others cannot. */
  rationale: string;
  connected: boolean;
  status?: ConnectorStatus;
  counts: ConnectorCounts;
  syncing: boolean;
  onSync: () => void;
  /** Rendered instead of the sync button while `connected` is false. */
  connectForm?: React.ReactNode;
  /** Shown under the card when not connected: how to find the right value. */
  connectHint?: string;
  lastReport?: React.ReactNode;
};

const ICONS = {
  github: GitBranch,
  jira: ListChecks,
  slack: MessagesSquare,
  other: GitBranch
} as const;

const TITLES = {
  github: "GitHub",
  jira: "Jira",
  slack: "Slack",
  other: "Source"
} as const;

/** The connector's own ink, so the card matches the citations it will produce. */
const INK = {
  github: "bg-gh",
  jira: "bg-jira",
  slack: "bg-slack",
  other: "bg-ink3"
} as const;

export function formatDate(value?: string | null) {
  if (!value) return "Never";
  return new Intl.DateTimeFormat("en", { dateStyle: "medium", timeStyle: "short" }).format(
    new Date(value)
  );
}

export function ConnectorCard({
  source,
  rationale,
  connected,
  status,
  counts,
  syncing,
  onSync,
  connectForm,
  connectHint,
  lastReport
}: ConnectorCardProps) {
  const Icon = ICONS[source];
  const neverSynced = status?.status === "never_synced";

  return (
    <section className="border-2 border-ink bg-card" aria-labelledby={`connector-${source}`}>
      <div className="flex flex-wrap items-start justify-between gap-4 border-b border-ruleSoft px-4 py-3.5">
        <div className="flex gap-3">
          <span className={`${INK[source]} flex h-9 w-9 shrink-0 items-center justify-center`}>
            <Icon className="h-4 w-4 text-card" aria-hidden />
          </span>
          <div>
            <h3 className="m-0 font-sans text-base font-bold tracking-tight" id={`connector-${source}`}>
              {TITLES[source]}
            </h3>
            <p className="m-0 mt-0.5 max-w-[46ch] font-serif text-sm leading-snug text-ink2">
              {rationale}
            </p>
          </div>
        </div>

        {connected ? (
          <button
            className="flex items-center gap-2 border-2 border-ink bg-ink px-4 py-2.5 font-mono text-[11px] font-semibold uppercase tracking-[0.13em] text-card transition hover:bg-card hover:text-ink disabled:opacity-60"
            disabled={syncing}
            onClick={onSync}
            type="button"
          >
            {syncing ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
            ) : (
              <RefreshCw className="h-3.5 w-3.5" aria-hidden />
            )}
            {syncing ? "Syncing" : neverSynced ? "Run first sync" : "Sync now"}
          </button>
        ) : (
          connectForm
        )}
      </div>

      {connected ? (
        <>
          <dl className="grid grid-cols-1 sm:grid-cols-3">
            {counts.map((item, index) => (
              <div
                key={item.label}
                className={`px-4 py-3 ${index > 0 ? "border-t border-ruleSoft sm:border-l sm:border-t-0" : ""}`}
              >
                <dt className="m-0 font-mono text-[10px] font-semibold uppercase tracking-[0.14em] text-ink3">
                  {item.label}
                </dt>
                <dd className="break-identifier m-0 mt-1.5 font-mono text-sm font-semibold tabular-nums">
                  {item.value}
                </dd>
              </div>
            ))}
          </dl>
          {lastReport}
          {status?.last_error ? (
            <p className="break-identifier m-0 border-t border-ruleSoft px-4 py-3 font-mono text-xs text-bad">
              {status.last_error}
            </p>
          ) : null}
        </>
      ) : connectHint ? (
        <p className="m-0 px-4 py-3.5 font-serif text-sm leading-snug text-ink2">{connectHint}</p>
      ) : null}
    </section>
  );
}
