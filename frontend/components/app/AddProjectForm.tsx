"use client";

import { useState, useTransition } from "react";
import { GitBranch, Loader2, X } from "lucide-react";
import { createProject, type Project } from "@/lib/api";

type AddProjectFormProps = {
  onClose: () => void;
  onCreated: (project: Project) => void;
};

/** The backend wants a slug; a person wants to type a name. */
function projectIdFromName(name: string) {
  return name
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "")
    .slice(0, 63);
}

export function AddProjectForm({ onClose, onCreated }: AddProjectFormProps) {
  const [name, setName] = useState("");
  const [repo, setRepo] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [isPending, startTransition] = useTransition();

  function submit() {
    const id = projectIdFromName(name);
    if (id.length < 2 || !/^[^/\s]+\/[^/\s]+$/.test(repo)) {
      setError("Enter a project name, and a repository as owner/repository.");
      return;
    }
    setError(null);
    startTransition(async () => {
      try {
        onCreated(await createProject({ id, name: name.trim(), repo: repo.trim() }));
      } catch (err) {
        setError(err instanceof Error ? err.message : "Could not add the project.");
      }
    });
  }

  return (
    <section className="border-2 border-ink bg-card" aria-labelledby="add-project-heading">
      <div className="flex items-start justify-between gap-3 border-b border-ruleSoft px-4 py-3">
        <div>
          <h3 className="m-0 font-sans text-base font-bold tracking-tight" id="add-project-heading">
            Connect a repository
          </h3>
          <p className="m-0 mt-0.5 font-serif text-sm text-ink2">
            The project id is derived from the name.
          </p>
        </div>
        <button
          aria-label="Close"
          className="border border-rule p-1 text-ink2 transition hover:border-ink hover:text-ink"
          onClick={onClose}
          type="button"
        >
          <X className="h-3.5 w-3.5" aria-hidden />
        </button>
      </div>

      <div className="grid gap-3 px-4 py-4 sm:grid-cols-2">
        <label className="font-mono text-[10px] font-semibold uppercase tracking-[0.14em] text-ink3">
          Project name
          <input
            autoFocus
            className="mt-1.5 block w-full border-2 border-rule bg-card px-3 py-2 font-sans text-sm text-ink outline-none transition focus:border-ink"
            onChange={(event) => setName(event.target.value)}
            placeholder="AskBase"
            value={name}
          />
        </label>
        <label className="font-mono text-[10px] font-semibold uppercase tracking-[0.14em] text-ink3">
          GitHub repository
          <input
            className="mt-1.5 block w-full border-2 border-rule bg-card px-3 py-2 font-mono text-sm text-ink outline-none transition focus:border-ink"
            onChange={(event) => setRepo(event.target.value)}
            placeholder="Manav0411/AskBase"
            value={repo}
          />
        </label>
      </div>

      {error ? (
        <p className="m-0 px-4 pb-3 font-mono text-xs text-bad" role="alert">
          {error}
        </p>
      ) : null}

      <div className="border-t border-ruleSoft px-4 py-3">
        <button
          className="flex items-center gap-2 border-2 border-ink bg-ink px-4 py-2.5 font-mono text-[11px] font-semibold uppercase tracking-[0.13em] text-card transition hover:bg-card hover:text-ink disabled:opacity-60"
          disabled={isPending}
          onClick={submit}
          type="button"
        >
          {isPending ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
          ) : (
            <GitBranch className="h-3.5 w-3.5" aria-hidden />
          )}
          Add repository
        </button>
      </div>
    </section>
  );
}
