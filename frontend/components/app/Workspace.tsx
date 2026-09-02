"use client";

import { useEffect, useState, useTransition } from "react";
import Link from "next/link";
import { AlertCircle, CheckCircle2, ListChecks, Loader2, MessagesSquare } from "lucide-react";

import {
  askAgent,
  configureJira,
  configureSlack,
  getGitHubSyncStatus,
  getJiraSyncStatus,
  getSlackSyncStatus,
  listProjects,
  syncGitHub,
  syncJira,
  syncSlack,
  type GitHubSyncStatus,
  type JiraSyncStatus,
  type Project,
  type SlackSyncStatus
} from "@/lib/api";
import { useBackendStatus } from "@/lib/useBackendStatus";
import { AnswerCard } from "@/components/evidence";
import {
  RECORDED_AT,
  RECORDED_ELAPSED_MS,
  RECORDED_QUESTION,
  RECORDED_RUN
} from "@/lib/fixtures/recorded-run";

import { AddProjectForm } from "./AddProjectForm";
import { Composer } from "./Composer";
import { ConnectorCard, formatDate, type ConnectorCounts } from "./ConnectorCard";
import { ProjectRail } from "./ProjectRail";
import { Transcript, type TranscriptTurn } from "./Transcript";

type SyncReportLine = { projectId: string; parts: string[] } | null;

function ReportLine({ report, projectId }: { report: SyncReportLine; projectId: string }) {
  if (!report || report.projectId !== projectId) return null;
  return (
    <p className="m-0 flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-ruleSoft px-4 py-3 font-mono text-xs text-ok">
      <span className="flex items-center gap-1.5 font-semibold">
        <CheckCircle2 className="h-3.5 w-3.5" aria-hidden /> Sync complete
      </span>
      {report.parts.map((part) => (
        <span key={part}>{part}</span>
      ))}
    </p>
  );
}

export function Workspace() {
  const backend = useBackendStatus();

  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [githubStatuses, setGithubStatuses] = useState<Record<string, GitHubSyncStatus>>({});
  const [jiraStatuses, setJiraStatuses] = useState<Record<string, JiraSyncStatus>>({});
  const [slackStatuses, setSlackStatuses] = useState<Record<string, SlackSyncStatus>>({});
  const [report, setReport] = useState<Record<string, SyncReportLine>>({});

  const [jiraProjectKey, setJiraProjectKey] = useState("");
  const [slackChannels, setSlackChannels] = useState("");
  const [busy, setBusy] = useState<Record<string, boolean>>({});

  const [showAddProject, setShowAddProject] = useState(false);
  const [projectsLoaded, setProjectsLoaded] = useState(false);
  const [query, setQuery] = useState("");
  const [turns, setTurns] = useState<TranscriptTurn[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [isQueryPending, startQueryTransition] = useTransition();

  const selectedProject = projects.find((project) => project.id === selectedId) ?? null;
  const conversationId = turns.length ? turns[turns.length - 1].response.conversation_id : null;
  const slackConnected = Boolean(selectedProject?.slack_channel_ids?.length);
  const asleep = backend === "asleep";
  // Derived rather than stored: "are we loading projects" is entirely a
  // function of the backend check and whether the fetch has returned.
  const loadingProjects = backend === "checking" || (backend === "awake" && !projectsLoaded);

  function setBusyFor(key: string, value: boolean) {
    setBusy((current) => ({ ...current, [key]: value }));
  }

  useEffect(() => {
    // Nothing here can succeed against a stopped instance, and trying just
    // produces a screenful of failures explaining the same one fact.
    if (backend !== "awake") return;

    let active = true;
    async function load() {
      try {
        const loaded = await listProjects();
        const statuses = await Promise.all(
          loaded.map(async (project) => {
            const [github, jira, slack] = await Promise.all([
              getGitHubSyncStatus(project.id),
              getJiraSyncStatus(project.id),
              getSlackSyncStatus(project.id)
            ]);
            return [project.id, github, jira, slack] as const;
          })
        );
        if (!active) return;
        setProjects(loaded);
        setGithubStatuses(Object.fromEntries(statuses.map(([id, github]) => [id, github])));
        setJiraStatuses(Object.fromEntries(statuses.map(([id, , jira]) => [id, jira])));
        setSlackStatuses(Object.fromEntries(statuses.map(([id, , , slack]) => [id, slack])));
        const initial = loaded.find((project) => project.id === "groundwork") ?? loaded[0];
        setSelectedId(initial?.id ?? "");
        setJiraProjectKey(initial?.jira_project_key ?? "");
      } catch (err) {
        if (active) setError(err instanceof Error ? err.message : "Could not load projects.");
      } finally {
        if (active) setProjectsLoaded(true);
      }
    }
    void load();
    return () => {
      active = false;
    };
  }, [backend]);

  function selectProject(project: Project) {
    setSelectedId(project.id);
    setJiraProjectKey(project.jira_project_key ?? "");
    setReport({});
    // A conversation is scoped to one project; the backend rejects one that
    // crosses projects, so carrying it over would only produce a 404.
    setTurns([]);
  }

  function handleProjectCreated(project: Project) {
    const fresh = { project_id: project.id, status: "never_synced" as const };
    setProjects((current) => [...current, project].sort((a, b) => a.name.localeCompare(b.name)));
    setGithubStatuses((current) => ({ ...current, [project.id]: fresh }));
    setJiraStatuses((current) => ({ ...current, [project.id]: fresh }));
    setSlackStatuses((current) => ({ ...current, [project.id]: fresh }));
    selectProject(project);
    setShowAddProject(false);
  }

  /** Every sync differs only in which endpoint and which noun. */
  async function runSync(
    key: "github" | "jira" | "slack",
    call: (projectId: string) => Promise<{ fetched: number; documents: number; embedded: number; completed_at: string }>,
    noun: string,
    setStatuses: (updater: (current: Record<string, GitHubSyncStatus>) => Record<string, GitHubSyncStatus>) => void
  ) {
    if (!selectedProject) return;
    const projectId = selectedProject.id;
    setError(null);
    setBusyFor(key, true);
    setStatuses((current) => ({
      ...current,
      [projectId]: { ...current[projectId], project_id: projectId, status: "running" }
    }));
    try {
      const result = await call(projectId);
      setReport((current) => ({
        ...current,
        [key]: {
          projectId,
          parts: [
            `${result.fetched} ${noun} fetched`,
            `${result.documents} documents`,
            `${result.embedded} embedded`
          ]
        }
      }));
      setStatuses((current) => ({
        ...current,
        [projectId]: {
          ...current[projectId],
          project_id: projectId,
          status: "succeeded",
          last_succeeded_at: result.completed_at
        }
      }));
    } catch (err) {
      const message = err instanceof Error ? err.message : `${key} sync failed.`;
      setError(message);
      setStatuses((current) => ({
        ...current,
        [projectId]: { ...current[projectId], project_id: projectId, status: "failed", last_error: message }
      }));
    } finally {
      setBusyFor(key, false);
    }
  }

  async function connectJira() {
    if (!selectedProject) return;
    const key = jiraProjectKey.trim().toUpperCase();
    if (!/^[A-Z][A-Z0-9_]{1,19}$/.test(key)) {
      setError("Enter a Jira project key such as GW.");
      return;
    }
    setError(null);
    setBusyFor("jira-connect", true);
    try {
      const updated = await configureJira(selectedProject.id, key);
      const status = await getJiraSyncStatus(selectedProject.id);
      setProjects((current) => current.map((p) => (p.id === updated.id ? updated : p)));
      setJiraStatuses((current) => ({ ...current, [updated.id]: status }));
      setJiraProjectKey(key);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not connect Jira.");
    } finally {
      setBusyFor("jira-connect", false);
    }
  }

  async function connectSlack() {
    if (!selectedProject) return;
    const ids = slackChannels
      .split(/[\s,]+/)
      .map((item) => item.trim().replace(/^#/, ""))
      .filter(Boolean);
    if (!ids.length || ids.some((id) => !/^[CGD][A-Z0-9]{4,}$/.test(id))) {
      setError("Enter Slack channel IDs such as C01ABC23DEF, separated by commas.");
      return;
    }
    setError(null);
    setBusyFor("slack-connect", true);
    try {
      const updated = await configureSlack(selectedProject.id, ids);
      const status = await getSlackSyncStatus(selectedProject.id);
      setProjects((current) => current.map((p) => (p.id === updated.id ? updated : p)));
      setSlackStatuses((current) => ({ ...current, [updated.id]: status }));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not connect Slack.");
    } finally {
      setBusyFor("slack-connect", false);
    }
  }

  function submitQuery() {
    if (!selectedProject || !query.trim() || asleep) return;
    const question = query.trim();
    setError(null);
    startQueryTransition(async () => {
      const started = performance.now();
      try {
        const response = await askAgent(question, selectedProject.id, conversationId);
        setTurns((previous) => [
          ...previous,
          { question, response, elapsedMs: Math.round(performance.now() - started) }
        ]);
        setQuery("");
      } catch (err) {
        setError(err instanceof Error ? err.message : "The request failed.");
      }
    });
  }

  const githubCounts: ConnectorCounts = [
    { label: "Status", value: (githubStatuses[selectedId]?.status ?? "loading").replace("_", " ") },
    { label: "Last sync", value: formatDate(githubStatuses[selectedId]?.last_succeeded_at) },
    {
      label: "Requests left",
      value: githubStatuses[selectedId]?.rate_limit_remaining?.toLocaleString() ?? "—"
    }
  ];

  return (
    <main className="surface-board min-h-screen">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b-2 border-ink px-4 py-3.5 sm:px-6">
        <div className="flex items-baseline gap-3">
          <Link className="font-sans text-lg font-bold tracking-tight" href="/">
            Groundwork
          </Link>
          <span className="font-mono text-[10px] uppercase tracking-[0.16em] text-ink3">
            Workspace
          </span>
        </div>
        <span className="flex items-center gap-2 font-mono text-[10px] font-semibold uppercase tracking-[0.14em] text-ink2">
          <span
            className={`h-2 w-2 rounded-full ${
              backend === "awake" ? "bg-ok" : backend === "asleep" ? "bg-bad" : "animate-pulse bg-rule"
            }`}
          />
          {backend === "awake" ? "Backend live" : backend === "asleep" ? "Backend asleep" : "Checking"}
        </span>
      </header>

      {asleep ? (
        <div className="border-b-2 border-ink bg-card px-4 py-4 sm:px-6">
          <p className="m-0 font-mono text-[11px] font-semibold uppercase tracking-[0.16em] text-bad">
            Backend asleep
          </p>
          <p className="m-0 mt-2 max-w-[70ch] font-serif text-[15px] leading-relaxed text-ink2">
            The demo instance is stopped between sessions to stay inside a free tier, so nothing can
            be asked right now. Below is a real captured run from {RECORDED_AT} — the same components
            a live answer renders in, with the same measured trace.
          </p>
        </div>
      ) : null}

      <div className="grid xl:grid-cols-[264px_minmax(0,1fr)]">
        <ProjectRail
          loading={loadingProjects}
          onAdd={() => setShowAddProject(true)}
          onSelect={selectProject}
          projects={projects}
          selectedId={selectedId}
          syncStatuses={githubStatuses}
        />

        <div className="flex flex-col gap-5 p-4 sm:p-6">
          <div>
            <p className="m-0 font-mono text-[10px] font-semibold uppercase tracking-[0.18em] text-ink3">
              {selectedProject ? selectedProject.repo : "No project selected"}
            </p>
            <h1 className="m-0 mt-1.5 font-sans text-[clamp(24px,3vw,34px)] font-bold tracking-tight">
              {selectedProject?.name ?? (asleep ? "Recorded run" : "Choose a project")}
            </h1>
          </div>

          {error ? (
            <p
              className="break-identifier m-0 flex items-start gap-2 border-2 border-bad bg-card px-3.5 py-2.5 font-mono text-xs text-bad"
              role="alert"
            >
              <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
              {error}
            </p>
          ) : null}

          {showAddProject ? (
            <AddProjectForm
              onClose={() => setShowAddProject(false)}
              onCreated={handleProjectCreated}
            />
          ) : null}

          {selectedProject ? (
            <div className="flex flex-col gap-4">
              <ConnectorCard
                connected
                counts={githubCounts}
                lastReport={<ReportLine projectId={selectedProject.id} report={report.github} />}
                onSync={() =>
                  runSync("github", (id) => syncGitHub(id), "commits", setGithubStatuses)
                }
                rationale="Commit history, indexed for exact author and activity questions."
                source="github"
                status={githubStatuses[selectedId]}
                syncing={Boolean(busy.github)}
              />

              <ConnectorCard
                connectForm={
                  <div className="flex gap-2">
                    <input
                      aria-label="Jira project key"
                      className="w-24 border-2 border-rule bg-card px-2.5 py-2 font-mono text-sm uppercase outline-none focus:border-ink"
                      maxLength={20}
                      onChange={(event) => setJiraProjectKey(event.target.value.toUpperCase())}
                      placeholder="GW"
                      value={jiraProjectKey}
                    />
                    <button
                      className="flex items-center gap-2 border-2 border-ink bg-ink px-3.5 py-2.5 font-mono text-[11px] font-semibold uppercase tracking-[0.13em] text-card transition hover:bg-card hover:text-ink disabled:opacity-60"
                      disabled={Boolean(busy["jira-connect"])}
                      onClick={connectJira}
                      type="button"
                    >
                      {busy["jira-connect"] ? (
                        <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
                      ) : (
                        <ListChecks className="h-3.5 w-3.5" aria-hidden />
                      )}
                      Connect
                    </button>
                  </div>
                }
                connectHint="The short key from your issue IDs — GW, from GW-7."
                connected={Boolean(selectedProject.jira_project_key)}
                counts={[
                  { label: "Project key", value: selectedProject.jira_project_key ?? "—" },
                  {
                    label: "Status",
                    value: (jiraStatuses[selectedId]?.status ?? "loading").replace("_", " ")
                  },
                  { label: "Last sync", value: formatDate(jiraStatuses[selectedId]?.last_succeeded_at) }
                ]}
                lastReport={<ReportLine projectId={selectedProject.id} report={report.jira} />}
                onSync={() => runSync("jira", (id) => syncJira(id), "issues", setJiraStatuses)}
                rationale="Issues, indexed for exact status, ownership and blocker questions."
                source="jira"
                status={jiraStatuses[selectedId]}
                syncing={Boolean(busy.jira)}
              />

              <ConnectorCard
                connectForm={
                  <div className="flex gap-2">
                    <input
                      aria-label="Slack channel IDs"
                      className="w-52 border-2 border-rule bg-card px-2.5 py-2 font-mono text-sm outline-none focus:border-ink"
                      onChange={(event) => setSlackChannels(event.target.value)}
                      placeholder="C01ABC23DEF, C04XYZ…"
                      value={slackChannels}
                    />
                    <button
                      className="flex items-center gap-2 border-2 border-ink bg-ink px-3.5 py-2.5 font-mono text-[11px] font-semibold uppercase tracking-[0.13em] text-card transition hover:bg-card hover:text-ink disabled:opacity-60"
                      disabled={Boolean(busy["slack-connect"])}
                      onClick={connectSlack}
                      type="button"
                    >
                      {busy["slack-connect"] ? (
                        <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
                      ) : (
                        <MessagesSquare className="h-3.5 w-3.5" aria-hidden />
                      )}
                      Connect
                    </button>
                  </div>
                }
                connectHint="Channel IDs, not names — find one under channel details. Only the channels you list are indexed."
                connected={slackConnected}
                counts={[
                  {
                    label: "Channels",
                    value: String(selectedProject.slack_channel_ids?.length ?? 0)
                  },
                  {
                    label: "Status",
                    value: (slackStatuses[selectedId]?.status ?? "loading").replace("_", " ")
                  },
                  {
                    label: "Last sync",
                    value: formatDate(slackStatuses[selectedId]?.last_succeeded_at)
                  }
                ]}
                lastReport={<ReportLine projectId={selectedProject.id} report={report.slack} />}
                onSync={() => runSync("slack", (id) => syncSlack(id), "threads", setSlackStatuses)}
                rationale="Threads, the only source that records why a decision was made."
                source="slack"
                status={slackStatuses[selectedId]}
                syncing={Boolean(busy.slack)}
              />
            </div>
          ) : null}

          <Composer
            disabled={asleep || !selectedProject}
            disabledReason={
              asleep
                ? "The backend is asleep — nothing can be asked until it is started."
                : "Select a project first."
            }
            onChange={setQuery}
            onSubmit={submitQuery}
            pending={isQueryPending}
            projectName={selectedProject?.name}
            value={query}
          />

          <Transcript
            onReset={() => {
              setTurns([]);
              setError(null);
            }}
            placeholder={
              <AnswerCard
                answer={RECORDED_RUN}
                elapsedMs={RECORDED_ELAPSED_MS}
                footnote={
                  <>
                    <strong className="font-semibold text-ink">Recorded run.</strong> Captured{" "}
                    {RECORDED_AT}. Ask your own question above once the backend is live.
                  </>
                }
                question={RECORDED_QUESTION}
              />
            }
            turns={turns}
          />
        </div>
      </div>
    </main>
  );
}
