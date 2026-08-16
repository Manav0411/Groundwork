from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx

from app.core.config import settings

GITHUB_API_VERSION = "2026-03-10"


class GitHubRateLimitError(RuntimeError):
    def __init__(self, reset_at: datetime | None, retry_after_seconds: int | None = None) -> None:
        self.reset_at = reset_at
        self.retry_after_seconds = retry_after_seconds
        message = "GitHub API rate limit exceeded"
        if reset_at is not None:
            message += f" until {reset_at.isoformat()}"
        super().__init__(message)


@dataclass(frozen=True)
class GitHubCommit:
    sha: str
    message: str
    author: str
    author_email: str | None
    author_login: str | None
    committer: str | None
    authored_at: str | None
    committed_at: str | None
    url: str

    @property
    def date(self) -> str | None:
        return self.committed_at or self.authored_at


@dataclass(frozen=True)
class GitHubCommitPageResult:
    commits: list[GitHubCommit]
    pages_fetched: int
    rate_limit_remaining: int | None
    rate_limit_reset_at: datetime | None


def _integer_header(response: httpx.Response, name: str) -> int | None:
    value = response.headers.get(name)
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _rate_limit_reset(response: httpx.Response) -> datetime | None:
    reset_epoch = _integer_header(response, "x-ratelimit-reset")
    if reset_epoch is None:
        return None
    return datetime.fromtimestamp(reset_epoch, tz=UTC)


def _parse_commit(item: dict[str, object]) -> GitHubCommit:
    commit = item["commit"]
    if not isinstance(commit, dict):
        raise ValueError("GitHub returned an invalid commit payload.")
    author_details = commit.get("author") or {}
    committer_details = commit.get("committer") or {}
    account = item.get("author") or {}
    if not isinstance(author_details, dict) or not isinstance(committer_details, dict):
        raise ValueError("GitHub returned invalid author metadata.")
    if not isinstance(account, dict):
        account = {}
    return GitHubCommit(
        sha=str(item["sha"]),
        message=str(commit.get("message") or "Untitled commit"),
        author=str(author_details.get("name") or account.get("login") or "Unknown author"),
        author_email=(str(author_details["email"]) if author_details.get("email") else None),
        author_login=(str(account["login"]) if account.get("login") else None),
        committer=(str(committer_details["name"]) if committer_details.get("name") else None),
        authored_at=(str(author_details["date"]) if author_details.get("date") else None),
        committed_at=(str(committer_details["date"]) if committer_details.get("date") else None),
        url=str(item["html_url"]),
    )


class GitHubConnector:
    def __init__(
        self,
        token: str | None = None,
        base_url: str = "https://api.github.com",
        timeout_seconds: float = 20,
    ) -> None:
        self.token = token or settings.github_token
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    async def list_commits(
        self,
        repo: str,
        *,
        since: datetime | None = None,
        branch: str | None = None,
        max_commits: int = 500,
        max_pages: int = 10,
    ) -> GitHubCommitPageResult:
        params: dict[str, str | int] = {"per_page": 100}
        if since is not None:
            params["since"] = since.astimezone(UTC).isoformat().replace("+00:00", "Z")
        if branch:
            params["sha"] = branch

        commits: list[GitHubCommit] = []
        pages_fetched = 0
        remaining: int | None = None
        reset_at: datetime | None = None
        next_url: str | None = f"{self.base_url}/repos/{repo}/commits"
        async with httpx.AsyncClient(timeout=self.timeout_seconds, follow_redirects=True) as client:
            while next_url and pages_fetched < max_pages and len(commits) < max_commits:
                response = await client.get(
                    next_url,
                    params=params if pages_fetched == 0 else None,
                    headers=self._headers(),
                )
                remaining = _integer_header(response, "x-ratelimit-remaining")
                reset_at = _rate_limit_reset(response)
                retry_after = _integer_header(response, "retry-after")
                message = ""
                if response.status_code in {403, 429}:
                    try:
                        message = str(response.json().get("message") or "").casefold()
                    except (ValueError, AttributeError):
                        pass
                rate_limited = response.status_code == 429 or (
                    response.status_code == 403
                    and (remaining == 0 or retry_after is not None or "rate limit" in message)
                )
                if rate_limited:
                    if reset_at is None and retry_after is not None:
                        reset_at = datetime.now(UTC) + timedelta(seconds=retry_after)
                    raise GitHubRateLimitError(reset_at, retry_after)
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, list):
                    raise ValueError("GitHub returned an invalid commits response.")
                commits.extend(_parse_commit(item) for item in payload)
                pages_fetched += 1
                next_link = response.links.get("next")
                next_url = next_link.get("url") if next_link else None

        return GitHubCommitPageResult(
            commits=commits[:max_commits],
            pages_fetched=pages_fetched,
            rate_limit_remaining=remaining,
            rate_limit_reset_at=reset_at,
        )

    async def latest_commits(self, repo: str, limit: int = 5) -> list[GitHubCommit]:
        result = await self.list_commits(repo, max_commits=max(1, min(limit, 100)), max_pages=1)
        return result.commits
