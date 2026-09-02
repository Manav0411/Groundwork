"use client";

import { Loader2, Plus } from "lucide-react";
import type { GitHubSyncStatus, Project } from "@/lib/api";

type ProjectRailProps = {
  projects: Project[];
  selectedId: string;
  syncStatuses: Record<string, GitHubSyncStatus>;
  loading: boolean;
  onSelect: (project: Project) => void;
  onAdd: () => void;
};

/** Sync state is the only thing a dot can honestly say at this size. */
function dotClass(status?: string) {
  if (status === "succeeded") return "bg-ok";
  if (status === "failed") return "bg-bad";
  if (status === "running") return "animate-pulse bg-ink";
  return "bg-rule";
}

export function ProjectRail({
  projects,
  selectedId,
  syncStatuses,
  loading,
  onSelect,
  onAdd
}: ProjectRailProps) {
  return (
    <nav aria-label="Projects" className="border-b-2 border-ink p-4 xl:border-b-0 xl:border-r-2">
      <div className="mb-3 flex items-center justify-between">
        <span className="font-mono text-[10px] font-semibold uppercase tracking-[0.18em] text-ink3">
          Projects
        </span>
        <button
          aria-label="Add project"
          className="border border-rule p-1 text-ink2 transition hover:border-ink hover:text-ink"
          onClick={onAdd}
          type="button"
        >
          <Plus className="h-3.5 w-3.5" aria-hidden />
        </button>
      </div>

      {loading ? (
        <p className="flex items-center gap-2 font-mono text-xs text-ink3">
          <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
          Loading projects
        </p>
      ) : null}

      <ul className="m-0 grid list-none gap-2 p-0 sm:grid-cols-2 xl:grid-cols-1">
        {projects.map((project) => {
          const selected = project.id === selectedId;
          return (
            <li key={project.id}>
              <button
                aria-current={selected ? "true" : undefined}
                className={`flex w-full items-center justify-between gap-3 border-2 px-3 py-2.5 text-left transition ${
                  selected ? "border-ink bg-card" : "border-rule bg-transparent hover:border-ink"
                }`}
                onClick={() => onSelect(project)}
                type="button"
              >
                <span className="min-w-0">
                  <span className="block truncate font-sans text-sm font-semibold">
                    {project.name}
                  </span>
                  <span className="break-identifier block truncate font-mono text-[10px] text-ink3">
                    {project.repo}
                  </span>
                </span>
                <span
                  className={`h-2.5 w-2.5 shrink-0 rounded-full ${dotClass(syncStatuses[project.id]?.status)}`}
                />
              </button>
            </li>
          );
        })}
      </ul>

      <button
        className="mt-4 flex w-full items-center justify-center gap-2 border-2 border-dashed border-rule px-3 py-2.5 font-mono text-[10.5px] font-semibold uppercase tracking-[0.12em] text-ink2 transition hover:border-ink hover:text-ink"
        onClick={onAdd}
        type="button"
      >
        <Plus className="h-3.5 w-3.5" aria-hidden /> Connect repository
      </button>
    </nav>
  );
}
