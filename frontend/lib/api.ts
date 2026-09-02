export type Citation = {
  id: number;
  source_type: string;
  title: string;
  url?: string | null;
  timestamp?: string | null;
};

export type EvidenceItem = {
  id: string;
  source_type: string;
  title: string;
  snippet: string;
  citation_id: number;
  authority: number;
};

export type TraceStep = {
  name: string;
  status: "pending" | "running" | "completed" | "failed";
  duration_ms: number;
  summary: string;
};

export type QueryResponse = {
  conversation_id: string;
  answer: string;
  retrieval_grade: "correct" | "ambiguous" | "incorrect";
  tools_used: string[];
  citations: Citation[];
  evidence: EvidenceItem[];
  unresolved_gaps: string[];
  trace: TraceStep[];
  /** The standalone question a follow-up was answered as; null when it was already self-contained. */
  resolved_query?: string | null;
};

export type Project = {
  id: string;
  name: string;
  repo: string;
  jira_project_key?: string | null;
  slack_channel_ids?: string[];
  status: string;
  health: "green" | "yellow" | "red" | "gray";
};

export type GitHubSyncStatus = {
  project_id: string;
  status: "never_synced" | "running" | "succeeded" | "failed" | string;
  last_started_at?: string | null;
  last_succeeded_at?: string | null;
  last_error?: string | null;
  rate_limit_remaining?: number | null;
  rate_limit_reset_at?: string | null;
};

export type GitHubSyncReport = GitHubSyncStatus & {
  repo: string;
  fetched: number;
  pages_fetched: number;
  documents: number;
  chunks: number;
  embedded: number;
  incremental_since: string | null;
  completed_at: string;
};

export type JiraSyncStatus = GitHubSyncStatus & {
  jira_project_key?: string | null;
};

export type JiraSyncReport = JiraSyncStatus & {
  jira_project_key: string;
  fetched: number;
  pages_fetched: number;
  documents: number;
  chunks: number;
  embedded: number;
  incremental_since: string | null;
  completed_at: string;
};

// Same-origin. The API key is attached by `app/api/[...path]/route.ts`, in the server process.
// It used to be read from NEXT_PUBLIC_APP_API_KEY here, which inlined it into the browser bundle.
const apiBaseUrl = "/api";

async function apiRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${apiBaseUrl}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...init?.headers
    }
  });

  if (!response.ok) {
    let message = `Request failed: ${response.status}`;
    try {
      const body = (await response.json()) as { detail?: string | { message?: string } };
      if (typeof body.detail === "string") message = body.detail;
      if (typeof body.detail === "object" && body.detail?.message) message = body.detail.message;
    } catch {
      // Keep the status-based fallback for non-JSON errors.
    }
    throw new Error(message);
  }

  return response.json() as Promise<T>;
}

export function listProjects(): Promise<Project[]> {
  return apiRequest<Project[]>("/projects");
}

export function createProject(project: Pick<Project, "id" | "name" | "repo">): Promise<Project> {
  return apiRequest<Project>("/projects", {
    method: "POST",
    body: JSON.stringify(project)
  });
}

export function getGitHubSyncStatus(projectId: string): Promise<GitHubSyncStatus> {
  return apiRequest<GitHubSyncStatus>(`/projects/${encodeURIComponent(projectId)}/sync/github`);
}

export function syncGitHub(projectId: string, maxCommits = 500): Promise<GitHubSyncReport> {
  return apiRequest<GitHubSyncReport>(
    `/projects/${encodeURIComponent(projectId)}/sync/github?max_commits=${maxCommits}`,
    { method: "POST" }
  );
}

export function configureJira(projectId: string, projectKey: string): Promise<Project> {
  return apiRequest<Project>(`/projects/${encodeURIComponent(projectId)}/connectors/jira`, {
    method: "PUT",
    body: JSON.stringify({ project_key: projectKey })
  });
}

export function getJiraSyncStatus(projectId: string): Promise<JiraSyncStatus> {
  return apiRequest<JiraSyncStatus>(`/projects/${encodeURIComponent(projectId)}/sync/jira`);
}

export function syncJira(projectId: string, maxIssues = 500): Promise<JiraSyncReport> {
  return apiRequest<JiraSyncReport>(
    `/projects/${encodeURIComponent(projectId)}/sync/jira?max_issues=${maxIssues}`,
    { method: "POST" }
  );
}

export function askAgent(
  query: string,
  projectId: string,
  conversationId?: string | null
): Promise<QueryResponse> {
  return apiRequest<QueryResponse>("/query", {
    method: "POST",
    body: JSON.stringify({
      query,
      project_id: projectId,
      include_trace: true,
      // Omitted on the first turn; the backend then mints a new conversation.
      conversation_id: conversationId ?? null
    })
  });
}

export type SlackSyncStatus = {
  project_id: string;
  slack_channel_ids?: string[];
  status: "never_synced" | "running" | "succeeded" | "failed" | string;
  last_started_at?: string | null;
  last_succeeded_at?: string | null;
  last_error?: string | null;
  rate_limit_remaining?: number | null;
};

export type SlackSyncReport = SlackSyncStatus & {
  channels: string[];
  fetched: number;
  pages_fetched: number;
  documents: number;
  chunks: number;
  embedded: number;
  completed_at: string;
};

export function configureSlack(projectId: string, channelIds: string[]): Promise<Project> {
  return apiRequest<Project>(`/projects/${encodeURIComponent(projectId)}/connectors/slack`, {
    method: "PUT",
    body: JSON.stringify({ channel_ids: channelIds })
  });
}

export function getSlackSyncStatus(projectId: string): Promise<SlackSyncStatus> {
  return apiRequest<SlackSyncStatus>(`/projects/${encodeURIComponent(projectId)}/sync/slack`);
}

export function syncSlack(projectId: string, maxMessages = 500): Promise<SlackSyncReport> {
  return apiRequest<SlackSyncReport>(
    `/projects/${encodeURIComponent(projectId)}/sync/slack?max_messages=${maxMessages}`,
    { method: "POST" }
  );
}

/**
 * One turn as the server remembers it.
 *
 * Deliberately smaller than `QueryResponse`: `load_conversation_history` returns
 * the question, the answer text and the grade and nothing else, so that stored
 * history can never become a source for a later answer. A thread restored from
 * here therefore has no citations, evidence or trace to show, and the interface
 * must say so rather than imply the evidence was checked and found absent.
 */
export type RemoteTurn = {
  query: string;
  resolved_query?: string | null;
  answer: string;
  retrieval_grade: QueryResponse["retrieval_grade"];
  created_at?: string | null;
};

export type RemoteConversation = {
  conversation_id: string;
  project_id: string;
  turns: RemoteTurn[];
};

export function getConversation(
  conversationId: string,
  projectId: string
): Promise<RemoteConversation> {
  return apiRequest<RemoteConversation>(
    `/conversations/${encodeURIComponent(conversationId)}?project_id=${encodeURIComponent(projectId)}`
  );
}
