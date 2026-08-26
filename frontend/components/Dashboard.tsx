"use client";

import { useEffect, useState, useTransition } from "react";
import {
  AlertCircle,
  CheckCircle2,
  ChevronRight,
  Code2,
  CornerDownRight,
  Copy,
  Database,
  GitBranch,
  KeyRound,
  ListChecks,
  MessagesSquare,
  Loader2,
  Plus,
  RefreshCw,
  RotateCcw,
  Send,
  ShieldCheck,
  X
} from "lucide-react";

import {
  askAgent,
  configureJira,
  createProject,
  getGitHubSyncStatus,
  getJiraSyncStatus,
  getSlackSyncStatus,
  listProjects,
  syncGitHub,
  syncJira,
  syncSlack,
  configureSlack,
  type GitHubSyncReport,
  type GitHubSyncStatus,
  type JiraSyncReport,
  type JiraSyncStatus,
  type SlackSyncReport,
  type SlackSyncStatus,
  type Project,
  type QueryResponse
} from "@/lib/api";

const initialAnswer: QueryResponse = {
  conversation_id: "welcome",
  answer:
    "Select a project, sync GitHub or Jira, then ask about commits, issue status, ownership, or blockers.",
  retrieval_grade: "correct",
  tools_used: [],
  citations: [],
  evidence: [],
  unresolved_gaps: [],
  trace: []
};

/** One exchange in the transcript: what was typed, and the response it produced. */
type TranscriptTurn = {
  question: string;
  response: QueryResponse;
};

function formatDate(value?: string | null) {
  if (!value) return "Never";
  return new Intl.DateTimeFormat("en", {
    dateStyle: "medium",
    timeStyle: "short"
  }).format(new Date(value));
}

const GRADE_STYLES: Record<QueryResponse["retrieval_grade"], string> = {
  correct: "bg-emerald-100 text-emerald-700",
  ambiguous: "bg-amber-100 text-amber-800",
  incorrect: "bg-red-100 text-red-700"
};

/** Report what the trace actually shows, rather than a fixed success message. */
function describeTrace(answer: QueryResponse) {
  const failed = answer.trace.filter((step) => step.status === "failed");
  if (failed.length) {
    return {
      className: "border-red-200 bg-red-50 text-red-800",
      message: `${failed.length} step(s) failed: ${failed.map((step) => step.name).join(", ")}.`
    };
  }
  if (answer.retrieval_grade === "incorrect") {
    return {
      className: "border-red-200 bg-red-50 text-red-800",
      message: "No evidence supported an answer. Nothing was generated and the gap is disclosed above."
    };
  }
  if (answer.unresolved_gaps.length) {
    return {
      className: "border-amber-200 bg-amber-50 text-amber-900",
      message: `Trace completed with ${answer.unresolved_gaps.length} unresolved gap(s).`
    };
  }
  if (!answer.citations.length) {
    return {
      className: "border-slate-200 bg-slate-50 text-slate-700",
      message: "Trace completed. No citation was required for this answer."
    };
  }
  return {
    className: "border-emerald-200 bg-emerald-50 text-emerald-800",
    message: `Trace completed. Every material claim maps to one of ${answer.citations.length} validated citation(s).`
  };
}

function projectIdFromName(name: string) {
  return name
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "")
    .slice(0, 63);
}

type AddProjectFormProps = {
  onClose: () => void;
  onCreated: (project: Project) => void;
};

function AddProjectForm({ onClose, onCreated }: AddProjectFormProps) {
  const [name, setName] = useState("");
  const [repo, setRepo] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [isPending, startTransition] = useTransition();

  function submit() {
    const id = projectIdFromName(name);
    if (id.length < 2 || !/^[^/\s]+\/[^/\s]+$/.test(repo)) {
      setError("Enter a project name and repository in owner/repo format.");
      return;
    }
    setError(null);
    startTransition(async () => {
      try {
        onCreated(await createProject({ id, name: name.trim(), repo: repo.trim() }));
      } catch (err) {
        setError(err instanceof Error ? err.message : "Could not add project.");
      }
    });
  }

  return (
    <div className="rounded-2xl border border-blue-200 bg-blue-50/70 p-4">
      <div className="mb-4 flex items-center justify-between">
        <div>
          <h3 className="font-semibold text-slate-950">Connect a repository</h3>
          <p className="mt-1 text-xs text-slate-600">Use the owner/repository format from GitHub.</p>
        </div>
        <button aria-label="Close form" className="icon-button" onClick={onClose} type="button">
          <X className="h-4 w-4" />
        </button>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="field-label">
          Project name
          <input
            autoFocus
            className="field-input"
            onChange={(event) => setName(event.target.value)}
            placeholder="AskBase"
            value={name}
          />
        </label>
        <label className="field-label">
          GitHub repository
          <input
            className="field-input"
            onChange={(event) => setRepo(event.target.value)}
            placeholder="Manav0411/AskBase"
            value={repo}
          />
        </label>
      </div>
      {error ? <p className="mt-3 text-sm text-red-700">{error}</p> : null}
      <button
        className="primary-button mt-4"
        disabled={isPending}
        onClick={submit}
        type="button"
      >
        {isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <GitBranch className="h-4 w-4" />}
        Add repository
      </button>
    </div>
  );
}

export function Dashboard() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [syncStatuses, setSyncStatuses] = useState<Record<string, GitHubSyncStatus>>({});
  const [lastReport, setLastReport] = useState<GitHubSyncReport | null>(null);
  const [jiraStatuses, setJiraStatuses] = useState<Record<string, JiraSyncStatus>>({});
  const [slackStatuses, setSlackStatuses] = useState<Record<string, SlackSyncStatus>>({});
  const [lastSlackReport, setLastSlackReport] = useState<SlackSyncReport | null>(null);
  const [slackChannels, setSlackChannels] = useState("");
  const [slackSyncing, setSlackSyncing] = useState(false);
  const [slackConnecting, setSlackConnecting] = useState(false);
  const [lastJiraReport, setLastJiraReport] = useState<JiraSyncReport | null>(null);
  const [jiraProjectKey, setJiraProjectKey] = useState("");
  const [showAddProject, setShowAddProject] = useState(false);
  const [loadingProjects, setLoadingProjects] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [jiraSyncing, setJiraSyncing] = useState(false);
  const [jiraConnecting, setJiraConnecting] = useState(false);
  const [query, setQuery] = useState("What was the last commit by Manav0411?");
  const [turns, setTurns] = useState<TranscriptTurn[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [isQueryPending, startQueryTransition] = useTransition();

  // The trace panel and its verdict always describe the most recent turn, so the rest of the page
  // keeps reading a single `answer` and needs no knowledge of the transcript.
  const answer = turns.length ? turns[turns.length - 1].response : initialAnswer;
  const conversationId = turns.length ? turns[turns.length - 1].response.conversation_id : null;

  const selectedProject = projects.find((project) => project.id === selectedId) ?? null;
  const selectedSync = selectedId ? syncStatuses[selectedId] : undefined;
  const selectedJiraSync = selectedId ? jiraStatuses[selectedId] : undefined;
  const selectedSlackSync = selectedId ? slackStatuses[selectedId] : undefined;
  const slackConnected = Boolean(selectedProject?.slack_channel_ids?.length);
  const traceVerdict = describeTrace(answer);

  useEffect(() => {
    let active = true;
    async function load() {
      try {
        const loadedProjects = await listProjects();
        const statusEntries = await Promise.all(
          loadedProjects.map(async (project) => {
            const [github, jira, slack] = await Promise.all([
              getGitHubSyncStatus(project.id),
              getJiraSyncStatus(project.id),
              getSlackSyncStatus(project.id)
            ]);
            return [project.id, github, jira, slack] as const;
          })
        );
        if (!active) return;
        setProjects(loadedProjects);
        setSyncStatuses(Object.fromEntries(statusEntries.map(([id, github]) => [id, github])));
        setJiraStatuses(Object.fromEntries(statusEntries.map(([id, , jira]) => [id, jira])));
        setSlackStatuses(Object.fromEntries(statusEntries.map(([id, , , slack]) => [id, slack])));
        const initialProject = loadedProjects.find((project) => project.id === "askbase") ?? loadedProjects[0];
        setSelectedId(initialProject?.id ?? "");
        setJiraProjectKey(initialProject?.jira_project_key ?? "");
      } catch (err) {
        if (active) setError(err instanceof Error ? err.message : "Could not load projects.");
      } finally {
        if (active) setLoadingProjects(false);
      }
    }
    void load();
    return () => {
      active = false;
    };
  }, []);

  function handleProjectCreated(project: Project) {
    setProjects((current) => [...current, project].sort((a, b) => a.name.localeCompare(b.name)));
    setSyncStatuses((current) => ({
      ...current,
      [project.id]: { project_id: project.id, status: "never_synced" }
    }));
    setJiraStatuses((current) => ({
      ...current,
      [project.id]: { project_id: project.id, status: "never_synced" }
    }));
    setSlackStatuses((current) => ({
      ...current,
      [project.id]: { project_id: project.id, status: "never_synced" }
    }));
    setSelectedId(project.id);
    setJiraProjectKey(project.jira_project_key ?? "");
    setTurns([]);
    setShowAddProject(false);
    setLastReport(null);
    setLastJiraReport(null);
  }

  async function handleConfigureJira() {
    if (!selectedProject) return;
    const normalizedKey = jiraProjectKey.trim().toUpperCase();
    if (!/^[A-Z][A-Z0-9_]{1,19}$/.test(normalizedKey)) {
      setError("Enter a Jira project key such as ASK.");
      return;
    }
    setError(null);
    setJiraConnecting(true);
    try {
      const updated = await configureJira(selectedProject.id, normalizedKey);
      const status = await getJiraSyncStatus(selectedProject.id);
      setProjects((current) => current.map((project) => project.id === updated.id ? updated : project));
      setJiraStatuses((current) => ({ ...current, [updated.id]: status }));
      setJiraProjectKey(normalizedKey);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not connect Jira.");
    } finally {
      setJiraConnecting(false);
    }
  }

  async function handleConfigureSlack() {
    if (!selectedProject) return;
    const ids = slackChannels.split(/[\s,]+/).map((item) => item.trim().replace(/^#/, "")).filter(Boolean);
    if (!ids.length || ids.some((id) => !/^[CGD][A-Z0-9]{4,}$/.test(id))) {
      setError("Enter Slack channel IDs such as C01ABC23DEF, separated by commas.");
      return;
    }
    setError(null);
    setSlackConnecting(true);
    try {
      const updated = await configureSlack(selectedProject.id, ids);
      const status = await getSlackSyncStatus(selectedProject.id);
      setProjects((current) => current.map((project) => project.id === updated.id ? updated : project));
      setSlackStatuses((current) => ({ ...current, [updated.id]: status }));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not connect Slack.");
    } finally {
      setSlackConnecting(false);
    }
  }

  async function handleSlackSync() {
    if (!selectedProject) return;
    setError(null);
    setSlackSyncing(true);
    setSlackStatuses((current) => ({
      ...current,
      [selectedProject.id]: { ...current[selectedProject.id], project_id: selectedProject.id, status: "running" }
    }));
    try {
      const report = await syncSlack(selectedProject.id);
      setLastSlackReport(report);
      setSlackStatuses((current) => ({
        ...current,
        [selectedProject.id]: { ...report, status: "succeeded", last_succeeded_at: report.completed_at }
      }));
    } catch (err) {
      const message = err instanceof Error ? err.message : "Slack sync failed.";
      setError(message);
      setSlackStatuses((current) => ({
        ...current,
        [selectedProject.id]: { ...current[selectedProject.id], project_id: selectedProject.id, status: "failed", last_error: message }
      }));
    } finally {
      setSlackSyncing(false);
    }
  }

  async function handleJiraSync() {
    if (!selectedProject) return;
    setError(null);
    setJiraSyncing(true);
    setJiraStatuses((current) => ({
      ...current,
      [selectedProject.id]: { ...current[selectedProject.id], project_id: selectedProject.id, status: "running" }
    }));
    try {
      const report = await syncJira(selectedProject.id);
      setLastJiraReport(report);
      setJiraStatuses((current) => ({
        ...current,
        [selectedProject.id]: { ...report, status: "succeeded", last_succeeded_at: report.completed_at }
      }));
    } catch (err) {
      const message = err instanceof Error ? err.message : "Jira sync failed.";
      setError(message);
      setJiraStatuses((current) => ({
        ...current,
        [selectedProject.id]: {
          ...current[selectedProject.id],
          project_id: selectedProject.id,
          status: "failed",
          last_error: message
        }
      }));
    } finally {
      setJiraSyncing(false);
    }
  }

  async function handleSync() {
    if (!selectedProject) return;
    setError(null);
    setSyncing(true);
    setSyncStatuses((current) => ({
      ...current,
      [selectedProject.id]: { ...current[selectedProject.id], project_id: selectedProject.id, status: "running" }
    }));
    try {
      const report = await syncGitHub(selectedProject.id);
      setLastReport(report);
      setSyncStatuses((current) => ({
        ...current,
        [selectedProject.id]: {
          ...report,
          status: "succeeded",
          last_succeeded_at: report.completed_at
        }
      }));
    } catch (err) {
      const message = err instanceof Error ? err.message : "GitHub sync failed.";
      setError(message);
      setSyncStatuses((current) => ({
        ...current,
        [selectedProject.id]: {
          ...current[selectedProject.id],
          project_id: selectedProject.id,
          status: "failed",
          last_error: message
        }
      }));
    } finally {
      setSyncing(false);
    }
  }

  function submitQuery() {
    if (!selectedProject || !query.trim()) return;
    const question = query.trim();
    setError(null);
    startQueryTransition(async () => {
      try {
        const response = await askAgent(question, selectedProject.id, conversationId);
        setTurns((previous) => [...previous, { question, response }]);
        setQuery("");
      } catch (err) {
        setError(err instanceof Error ? err.message : "Unknown request failure.");
      }
    });
  }

  function startNewConversation() {
    setTurns([]);
    setError(null);
  }

  async function copyAnswer() {
    await navigator.clipboard.writeText(answer.answer);
  }

  return (
    <main className="min-h-screen bg-[#f6f8fb]">
      <header className="flex min-h-16 flex-wrap items-center justify-between gap-3 border-b border-slate-800 bg-slate-950 px-4 py-3 text-white sm:px-6">
        <div className="flex items-center gap-3">
          <div className="rounded-lg bg-blue-500/15 p-2"><Code2 className="h-5 w-5 text-blue-300" /></div>
          <div>
            <h1 className="text-lg font-semibold tracking-tight">Groundwork</h1>
            <p className="text-xs text-slate-400">Engineering knowledge, with evidence</p>
          </div>
        </div>
        <div className="flex items-center gap-2 text-xs sm:text-sm">
          <div className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-slate-200">
            <span className="mr-2 inline-block h-2 w-2 rounded-full bg-emerald-400" />Operational
          </div>
          <div className="hidden items-center gap-2 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-slate-300 sm:flex">
            <KeyRound className="h-4 w-4" /> API protected
          </div>
        </div>
      </header>

      <div className="grid min-h-[calc(100vh-4rem)] xl:grid-cols-[270px_minmax(0,1fr)_350px]">
        <aside className="border-b border-slate-800 bg-slate-900 p-4 text-white xl:border-b-0 xl:border-r xl:p-5">
          <div className="mb-3 flex items-center justify-between">
            <span className="section-label">Projects</span>
            <button
              aria-label="Add project"
              className="rounded-lg p-1.5 text-slate-400 transition hover:bg-slate-800 hover:text-white"
              onClick={() => setShowAddProject(true)}
              type="button"
            >
              <Plus className="h-4 w-4" />
            </button>
          </div>
          <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-1">
            {loadingProjects ? (
              <div className="flex items-center gap-2 p-3 text-sm text-slate-400"><Loader2 className="h-4 w-4 animate-spin" />Loading projects</div>
            ) : null}
            {projects.map((project) => {
              const status = syncStatuses[project.id]?.status;
              const selected = project.id === selectedId;
              return (
                <button
                  className={`flex items-center justify-between rounded-xl border px-3 py-3 text-left transition ${selected ? "border-blue-500 bg-blue-500/10" : "border-slate-800 bg-slate-950/40 hover:border-slate-600"}`}
                  key={project.id}
                  onClick={() => {
                    setSelectedId(project.id);
                    setJiraProjectKey(project.jira_project_key ?? "");
                    setLastReport(null);
                    setLastJiraReport(null);
                    // A conversation is scoped to one project; carrying it across would be
                    // rejected by the backend anyway.
                    setTurns([]);
                  }}
                  type="button"
                >
                  <span className="min-w-0">
                    <span className="block truncate text-sm font-medium">{project.name}</span>
                    <span className="block truncate text-xs text-slate-400">{project.repo}</span>
                  </span>
                  <span className={`ml-3 h-2.5 w-2.5 shrink-0 rounded-full ${status === "succeeded" ? "bg-emerald-400" : status === "failed" ? "bg-red-400" : status === "running" ? "animate-pulse bg-blue-400" : "bg-slate-500"}`} />
                </button>
              );
            })}
          </div>
          <button className="mt-4 flex w-full items-center justify-center gap-2 rounded-xl border border-dashed border-slate-700 px-3 py-2.5 text-sm text-slate-300 transition hover:border-blue-400 hover:text-white" onClick={() => setShowAddProject(true)} type="button">
            <Plus className="h-4 w-4" /> Connect repository
          </button>
        </aside>

        <section className="space-y-5 p-4 sm:p-6">
          <div className="flex flex-wrap items-end justify-between gap-3">
            <div>
              <p className="text-xs font-semibold uppercase tracking-[0.16em] text-blue-600">Workspace</p>
              <h2 className="mt-1 text-2xl font-semibold text-slate-950">{selectedProject?.name ?? "Choose a project"}</h2>
              <p className="mt-1 text-sm text-slate-600">{selectedProject?.repo ?? "Connect a GitHub repository to begin."}</p>
            </div>
            {selectedProject ? <a className="flex items-center gap-1 text-sm font-medium text-blue-700 hover:text-blue-800" href={`https://github.com/${selectedProject.repo}`} rel="noreferrer" target="_blank">Open GitHub <ChevronRight className="h-4 w-4" /></a> : null}
          </div>

          {showAddProject ? <AddProjectForm onClose={() => setShowAddProject(false)} onCreated={handleProjectCreated} /> : null}

          {selectedProject ? (
            <section className="rounded-2xl border border-line bg-white p-5 shadow-sm" aria-labelledby="github-connection-heading">
              <div className="flex flex-wrap items-start justify-between gap-4">
                <div className="flex gap-3">
                  <div className="rounded-xl bg-slate-950 p-2.5 text-white"><GitBranch className="h-5 w-5" /></div>
                  <div>
                    <h3 className="font-semibold text-slate-950" id="github-connection-heading">GitHub connection</h3>
                    <p className="mt-1 text-sm text-slate-600">Commit history is indexed for exact author and activity questions.</p>
                  </div>
                </div>
                <button className="primary-button" disabled={syncing} onClick={handleSync} type="button">
                  {syncing ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
                  {syncing ? "Syncing…" : selectedSync?.status === "never_synced" ? "Run first sync" : "Sync now"}
                </button>
              </div>
              <div className="mt-5 grid gap-3 sm:grid-cols-3">
                <div className="metric-card"><span>Status</span><strong className="capitalize">{selectedSync?.status?.replace("_", " ") ?? "Loading"}</strong></div>
                <div className="metric-card"><span>Last successful sync</span><strong>{formatDate(selectedSync?.last_succeeded_at)}</strong></div>
                <div className="metric-card"><span>GitHub requests left</span><strong>{selectedSync?.rate_limit_remaining?.toLocaleString() ?? "—"}</strong></div>
              </div>
              {lastReport?.project_id === selectedProject.id ? (
                <div className="mt-4 flex flex-wrap gap-x-5 gap-y-2 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800">
                  <span className="flex items-center gap-2 font-medium"><CheckCircle2 className="h-4 w-4" />Sync complete</span>
                  <span>{lastReport.fetched} commits fetched</span><span>{lastReport.documents} documents</span><span>{lastReport.embedded} embedded</span>
                </div>
              ) : null}
            </section>
          ) : null}

          {selectedProject ? (
            <section className="rounded-2xl border border-line bg-white p-5 shadow-sm" aria-labelledby="jira-connection-heading">
              <div className="flex flex-wrap items-start justify-between gap-4">
                <div className="flex gap-3">
                  <div className="rounded-xl bg-blue-600 p-2.5 text-white"><ListChecks className="h-5 w-5" /></div>
                  <div>
                    <h3 className="font-semibold text-slate-950" id="jira-connection-heading">Jira connection</h3>
                    <p className="mt-1 text-sm text-slate-600">Issues are indexed for exact status, ownership, and blocker questions.</p>
                  </div>
                </div>
                {selectedProject.jira_project_key ? (
                  <button className="primary-button" disabled={jiraSyncing} onClick={handleJiraSync} type="button">
                    {jiraSyncing ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
                    {jiraSyncing ? "Syncing…" : selectedJiraSync?.status === "never_synced" ? "Run first sync" : "Sync now"}
                  </button>
                ) : (
                  <div className="flex gap-2">
                    <input
                      aria-label="Jira project key"
                      className="w-28 rounded-lg border border-line px-3 text-sm uppercase outline-none focus:border-blue-500"
                      maxLength={20}
                      onChange={(event) => setJiraProjectKey(event.target.value.toUpperCase())}
                      placeholder="ASK"
                      value={jiraProjectKey}
                    />
                    <button className="primary-button" disabled={jiraConnecting} onClick={handleConfigureJira} type="button">
                      {jiraConnecting ? <Loader2 className="h-4 w-4 animate-spin" /> : <ListChecks className="h-4 w-4" />}
                      Connect Jira
                    </button>
                  </div>
                )}
              </div>
              {selectedProject.jira_project_key ? (
                <>
                  <div className="mt-5 grid gap-3 sm:grid-cols-3">
                    <div className="metric-card"><span>Project key</span><strong>{selectedProject.jira_project_key}</strong></div>
                    <div className="metric-card"><span>Status</span><strong className="capitalize">{selectedJiraSync?.status?.replace("_", " ") ?? "Loading"}</strong></div>
                    <div className="metric-card"><span>Last successful sync</span><strong>{formatDate(selectedJiraSync?.last_succeeded_at)}</strong></div>
                  </div>
                  {lastJiraReport?.project_id === selectedProject.id ? (
                    <div className="mt-4 flex flex-wrap gap-x-5 gap-y-2 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800">
                      <span className="flex items-center gap-2 font-medium"><CheckCircle2 className="h-4 w-4" />Sync complete</span>
                      <span>{lastJiraReport.fetched} issues fetched</span><span>{lastJiraReport.documents} documents</span><span>{lastJiraReport.embedded} embedded</span>
                    </div>
                  ) : null}
                </>
              ) : (
                <p className="mt-4 rounded-xl border border-blue-100 bg-blue-50 px-4 py-3 text-sm text-blue-800">Enter the short key shown in Jira issue IDs, for example ASK from ASK-6.</p>
              )}
            </section>
          ) : null}

          {selectedProject ? (
            <section className="rounded-2xl border border-line bg-white p-5 shadow-sm" aria-labelledby="slack-connection-heading">
              <div className="flex flex-wrap items-start justify-between gap-4">
                <div className="flex gap-3">
                  <div className="rounded-xl bg-violet-600 p-2.5 text-white"><MessagesSquare className="h-5 w-5" /></div>
                  <div>
                    <h3 className="font-semibold text-slate-950" id="slack-connection-heading">Slack connection</h3>
                    <p className="mt-1 text-sm text-slate-600">Threads are indexed for decision-history questions commits and tickets cannot answer.</p>
                  </div>
                </div>
                {slackConnected ? (
                  <button className="primary-button" disabled={slackSyncing} onClick={handleSlackSync} type="button">
                    {slackSyncing ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
                    {slackSyncing ? "Syncing…" : selectedSlackSync?.status === "never_synced" ? "Run first sync" : "Sync now"}
                  </button>
                ) : (
                  <div className="flex gap-2">
                    <input
                      aria-label="Slack channel IDs"
                      className="w-56 rounded-lg border border-line px-3 text-sm outline-none focus:border-violet-500"
                      onChange={(event) => setSlackChannels(event.target.value)}
                      placeholder="C01ABC23DEF, C04XYZ…"
                      value={slackChannels}
                    />
                    <button className="primary-button" disabled={slackConnecting} onClick={handleConfigureSlack} type="button">
                      {slackConnecting ? <Loader2 className="h-4 w-4 animate-spin" /> : <MessagesSquare className="h-4 w-4" />}
                      Connect Slack
                    </button>
                  </div>
                )}
              </div>
              {slackConnected ? (
                <>
                  <div className="mt-5 grid gap-3 sm:grid-cols-3">
                    <div className="metric-card"><span>Channels</span><strong>{selectedProject.slack_channel_ids?.length ?? 0}</strong></div>
                    <div className="metric-card"><span>Status</span><strong className="capitalize">{selectedSlackSync?.status?.replace("_", " ") ?? "Loading"}</strong></div>
                    <div className="metric-card"><span>Last successful sync</span><strong>{formatDate(selectedSlackSync?.last_succeeded_at)}</strong></div>
                  </div>
                  {lastSlackReport?.project_id === selectedProject.id ? (
                    <div className="mt-4 flex flex-wrap gap-x-5 gap-y-2 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800">
                      <span className="flex items-center gap-2 font-medium"><CheckCircle2 className="h-4 w-4" />Sync complete</span>
                      <span>{lastSlackReport.fetched} threads fetched</span><span>{lastSlackReport.documents} documents</span><span>{lastSlackReport.embedded} embedded</span>
                    </div>
                  ) : null}
                  {selectedSlackSync?.last_error ? (
                    <p className="mt-4 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">{selectedSlackSync.last_error}</p>
                  ) : null}
                </>
              ) : (
                <p className="mt-4 rounded-xl border border-violet-100 bg-violet-50 px-4 py-3 text-sm text-violet-900">Channel IDs, not names — find one under channel details in Slack. Only the channels you list are indexed.</p>
              )}
            </section>
          ) : null}

          <div className="rounded-2xl border border-line bg-panel p-4 shadow-panel">
            <label className="mb-2 block text-sm font-medium text-slate-800" htmlFor="agent-query">Ask about {selectedProject?.name ?? "your project"}</label>
            <div className="flex gap-3">
              <input
                className="min-h-14 min-w-0 flex-1 rounded-xl border border-line px-4 text-sm outline-none transition focus:border-blue-500 focus:ring-4 focus:ring-blue-100"
                id="agent-query"
                onChange={(event) => setQuery(event.target.value)}
                onKeyDown={(event) => { if (event.key === "Enter") submitQuery(); }}
                value={query}
              />
              <button aria-label="Ask agent" className="flex w-14 shrink-0 items-center justify-center rounded-xl bg-blue-600 text-white transition hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-60" disabled={isQueryPending || !selectedProject} onClick={submitQuery} type="button">
                {isQueryPending ? <Loader2 className="h-5 w-5 animate-spin" /> : <Send className="h-5 w-5" />}
              </button>
            </div>
            {error ? <p className="mt-3 flex items-center gap-2 text-sm text-red-700"><AlertCircle className="h-4 w-4" />{error}</p> : null}
          </div>

          <article className="rounded-2xl border border-line bg-panel shadow-panel">
            <div className="flex items-center justify-between border-b border-line px-5 py-4">
              <div className="flex items-center gap-3">
                <h3 className="font-semibold text-slate-950">Conversation</h3>
                {turns.length ? <span className="text-xs text-slate-500">{turns.length} turn{turns.length === 1 ? "" : "s"}</span> : null}
              </div>
              <div className="flex items-center gap-2">
                {turns.length ? <button className="secondary-button" onClick={startNewConversation} type="button"><RotateCcw className="h-4 w-4" />New</button> : null}
                <button className="secondary-button" onClick={copyAnswer} type="button"><Copy className="h-4 w-4" />Copy</button>
              </div>
            </div>

            {turns.length ? (
              <div className="divide-y divide-line">
                {turns.map((turn, index) => (
                  <div className="space-y-4 p-5 text-sm leading-6 text-slate-700" key={`${turn.response.conversation_id}-${index}`}>
                    <div>
                      <p className="font-medium text-slate-950">{turn.question}</p>
                      {/* Shown only when the question was rewritten, which is the feature made visible. */}
                      {turn.response.resolved_query ? (
                        <p className="mt-1 flex items-start gap-1.5 text-xs text-slate-500">
                          <CornerDownRight className="mt-0.5 h-3 w-3 shrink-0" />
                          <span>Resolved to “{turn.response.resolved_query}”</span>
                        </p>
                      ) : null}
                    </div>
                    <div className="flex items-center gap-2">
                      <span className={`rounded-full px-2.5 py-1 text-xs font-medium ${GRADE_STYLES[turn.response.retrieval_grade] ?? "bg-slate-100 text-slate-700"}`}>{turn.response.retrieval_grade}</span>
                    </div>
                    <p>{turn.response.answer}</p>
                    {turn.response.citations.length ? (
                      <div><h4 className="mb-2 font-semibold text-slate-950">Citations</h4><div className="flex flex-wrap gap-2">{turn.response.citations.map((citation) => citation.url ? <a className="rounded-lg border border-blue-100 bg-blue-50 px-2.5 py-1 text-xs text-blue-700 hover:border-blue-300" href={citation.url} key={citation.id} rel="noreferrer" target="_blank">[{citation.id}] {citation.title}</a> : <span className="rounded-lg border border-blue-100 bg-blue-50 px-2.5 py-1 text-xs text-blue-700" key={citation.id}>[{citation.id}] {citation.title}</span>)}</div></div>
                    ) : null}
                    {turn.response.unresolved_gaps.length ? (
                      <div className="rounded-xl border border-amber-200 bg-amber-50 p-4">
                        <h4 className="mb-2 flex items-center gap-2 font-semibold text-amber-900"><AlertCircle className="h-4 w-4" />Unresolved gaps</h4>
                        <ul className="list-disc space-y-1 pl-5 text-xs text-amber-900">{turn.response.unresolved_gaps.map((gap) => <li key={gap}>{gap}</li>)}</ul>
                      </div>
                    ) : null}
                  </div>
                ))}
              </div>
            ) : (
              <div className="p-5 text-sm leading-6 text-slate-700">
                <p>{initialAnswer.answer}</p>
                <p className="mt-3 text-xs text-slate-500">Ask a follow-up after the first answer — “who is it assigned to?” is resolved against the conversation before it is routed.</p>
              </div>
            )}
          </article>
        </section>

        <aside className="border-t border-line bg-white p-5 xl:border-l xl:border-t-0">
          <div className="mb-5 flex items-center justify-between"><div><p className="section-label text-slate-500">Observability</p><h3 className="mt-1 font-semibold text-slate-950">Agent trace</h3></div><ShieldCheck className="h-5 w-5 text-emerald-500" /></div>
          {answer.trace.length ? <div className="space-y-4">{answer.trace.map((step) => <div className="rounded-xl border border-line bg-white p-4 shadow-sm" key={step.name}><div className="mb-2 flex items-center justify-between"><div className="flex items-center gap-2">{step.status === "failed" ? <AlertCircle className="h-4 w-4 text-red-500" /> : <CheckCircle2 className="h-4 w-4 text-emerald-500" />}<span className="font-medium text-slate-900">{step.name}</span></div><span className="text-xs text-slate-500">{step.duration_ms}ms</span></div><p className="text-sm leading-5 text-slate-600">{step.summary}</p></div>)}</div> : <div className="rounded-xl border border-dashed border-line p-6 text-center"><Database className="mx-auto h-6 w-6 text-slate-400" /><p className="mt-2 text-sm text-slate-600">Run a question to inspect planning, retrieval, and citation validation.</p></div>}
          {answer.trace.length ? <div className={`mt-5 rounded-xl border p-4 text-sm ${traceVerdict.className}`}>{traceVerdict.message}</div> : null}
        </aside>
      </div>
    </main>
  );
}
