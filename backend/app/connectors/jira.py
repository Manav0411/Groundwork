import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.config import settings

PROJECT_KEY_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{1,19}$")
BLOCK_TYPES = {"paragraph", "heading", "bulletList", "orderedList", "listItem", "blockquote"}


class JiraRateLimitError(RuntimeError):
    def __init__(self, retry_after_seconds: int | None) -> None:
        self.retry_after_seconds = retry_after_seconds
        message = "Jira API rate limit exceeded"
        if retry_after_seconds is not None:
            message += f"; retry after {retry_after_seconds} seconds"
        super().__init__(message)


@dataclass(frozen=True)
class JiraUser:
    display_name: str
    account_id: str | None
    email: str | None


@dataclass(frozen=True)
class JiraIssue:
    key: str
    summary: str
    description: str
    status: str
    status_category: str
    priority: str | None
    issue_type: str
    assignee: JiraUser | None
    reporter: JiraUser | None
    labels: list[str]
    comments: list[str]
    created_at: str | None
    updated_at: str | None
    url: str


@dataclass(frozen=True)
class JiraIssuePageResult:
    issues: list[JiraIssue]
    pages_fetched: int
    rate_limit_remaining: int | None


def _integer_header(response: httpx.Response, name: str) -> int | None:
    value = response.headers.get(name)
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def adf_to_text(value: Any) -> str:
    """Flatten Atlassian Document Format while preserving readable block boundaries."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(adf_to_text(item) for item in value)
    if not isinstance(value, dict):
        return str(value)
    if value.get("type") == "text":
        return str(value.get("text") or "")
    content = adf_to_text(value.get("content") or [])
    if value.get("type") in BLOCK_TYPES and content:
        return content + "\n"
    return content


def _parse_user(value: object) -> JiraUser | None:
    if not isinstance(value, dict):
        return None
    display_name = str(value.get("displayName") or "").strip()
    if not display_name:
        return None
    return JiraUser(
        display_name=display_name,
        account_id=str(value["accountId"]) if value.get("accountId") else None,
        email=str(value["emailAddress"]) if value.get("emailAddress") else None,
    )


def _parse_issue(item: dict[str, Any], site_url: str) -> JiraIssue:
    fields = item.get("fields")
    if not isinstance(fields, dict):
        raise ValueError("Jira returned issue data without fields.")
    status = fields.get("status") if isinstance(fields.get("status"), dict) else {}
    status_category = (
        status.get("statusCategory") if isinstance(status.get("statusCategory"), dict) else {}
    )
    priority = fields.get("priority") if isinstance(fields.get("priority"), dict) else {}
    issue_type = fields.get("issuetype") if isinstance(fields.get("issuetype"), dict) else {}
    comment_page = fields.get("comment") if isinstance(fields.get("comment"), dict) else {}
    comments: list[str] = []
    for comment in comment_page.get("comments") or []:
        if not isinstance(comment, dict):
            continue
        author = _parse_user(comment.get("author"))
        body = " ".join(adf_to_text(comment.get("body")).split())
        if body:
            comments.append(f"{author.display_name if author else 'Unknown author'}: {body}")
    key = str(item.get("key") or "").strip()
    if not key:
        raise ValueError("Jira returned an issue without a key.")
    return JiraIssue(
        key=key,
        summary=" ".join(str(fields.get("summary") or "Untitled issue").split()),
        description=" ".join(adf_to_text(fields.get("description")).split()),
        status=str(status.get("name") or "Unknown"),
        status_category=str(status_category.get("key") or "undefined"),
        priority=str(priority["name"]) if priority.get("name") else None,
        issue_type=str(issue_type.get("name") or "Issue"),
        assignee=_parse_user(fields.get("assignee")),
        reporter=_parse_user(fields.get("reporter")),
        labels=sorted(
            {str(label).strip() for label in fields.get("labels") or [] if str(label).strip()}
        ),
        comments=comments,
        created_at=str(fields["created"]) if fields.get("created") else None,
        updated_at=str(fields["updated"]) if fields.get("updated") else None,
        url=f"{site_url.rstrip('/')}/browse/{key}",
    )


class JiraConnector:
    def __init__(
        self,
        *,
        site_url: str | None = None,
        token: str | None = None,
        cloud_id: str | None = None,
        api_base_url: str | None = None,
        timeout_seconds: float = 20,
    ) -> None:
        self.site_url = (site_url or settings.jira_site_url or "").rstrip("/")
        self.token = token or settings.jira_api_token
        self.cloud_id = cloud_id or settings.jira_cloud_id
        self.api_base_url = api_base_url.rstrip("/") if api_base_url else None
        self.timeout_seconds = timeout_seconds
        if not self.site_url:
            raise ValueError("JIRA_SITE_URL is required.")
        if not self.token:
            raise ValueError("JIRA_API_TOKEN is required.")

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.token}",
        }

    async def _base_url(self, client: httpx.AsyncClient) -> str:
        if self.api_base_url:
            return self.api_base_url
        cloud_id = self.cloud_id
        if not cloud_id:
            response = await client.get(f"{self.site_url}/_edge/tenant_info")
            response.raise_for_status()
            cloud_id = str(response.json().get("cloudId") or "").strip()
            if not cloud_id:
                raise ValueError("Could not discover Jira Cloud ID from the site URL.")
            self.cloud_id = cloud_id
        return f"https://api.atlassian.com/ex/jira/{cloud_id}"

    async def list_issues(
        self,
        project_key: str,
        *,
        updated_since: datetime | None = None,
        max_issues: int = 500,
        max_pages: int = 20,
    ) -> JiraIssuePageResult:
        normalized_key = project_key.strip().upper()
        if not PROJECT_KEY_PATTERN.fullmatch(normalized_key):
            raise ValueError(f"Invalid Jira project key: {project_key!r}")
        jql = f'project = "{normalized_key}"'
        if updated_since is not None:
            since = updated_since.astimezone(UTC).strftime("%Y-%m-%d %H:%M")
            jql += f' AND updated >= "{since}"'
        jql += " ORDER BY updated ASC, key ASC"
        issues: list[JiraIssue] = []
        pages_fetched = 0
        next_page_token: str | None = None
        remaining: int | None = None
        async with httpx.AsyncClient(timeout=self.timeout_seconds, follow_redirects=True) as client:
            base_url = await self._base_url(client)
            while pages_fetched < max_pages and len(issues) < max_issues:
                body: dict[str, object] = {
                    "jql": jql,
                    "maxResults": min(100, max_issues - len(issues)),
                    "fields": [
                        "summary",
                        "description",
                        "status",
                        "priority",
                        "issuetype",
                        "assignee",
                        "reporter",
                        "labels",
                        "comment",
                        "created",
                        "updated",
                    ],
                }
                if next_page_token:
                    body["nextPageToken"] = next_page_token
                response = await client.post(
                    f"{base_url}/rest/api/3/search/jql",
                    headers=self._headers(),
                    json=body,
                )
                remaining = _integer_header(response, "x-ratelimit-remaining")
                if response.status_code == 429:
                    raise JiraRateLimitError(_integer_header(response, "retry-after"))
                response.raise_for_status()
                payload = response.json()
                page_items = payload.get("issues")
                if not isinstance(page_items, list):
                    raise ValueError("Jira returned an invalid issue search response.")
                issues.extend(_parse_issue(item, self.site_url) for item in page_items)
                pages_fetched += 1
                next_page_token = payload.get("nextPageToken")
                if not next_page_token or not page_items:
                    break
        return JiraIssuePageResult(
            issues=issues[:max_issues],
            pages_fetched=pages_fetched,
            rate_limit_remaining=remaining,
        )
