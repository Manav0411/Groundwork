"""Slack connector.

Follows the same contract as the GitHub and Jira connectors: typed results, provider pagination
followed rather than reconstructed, rate-limit headers captured, and no knowledge of the ORM.

The document unit here is a **thread**, not a message. A decision is a discussion; splitting it into
one document per message would scatter the reasoning across dozens of tiny chunks and undo the
retrieval work of Phase 2. A message with no replies simply becomes a single-message thread.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx

from app.core.config import settings

SLACK_API_BASE = "https://slack.com/api"


class SlackRateLimitError(RuntimeError):
    def __init__(self, retry_after_seconds: int | None = None) -> None:
        self.retry_after_seconds = retry_after_seconds
        message = "Slack API rate limit exceeded"
        if retry_after_seconds is not None:
            message += f"; retry after {retry_after_seconds}s"
        super().__init__(message)


class SlackAPIError(RuntimeError):
    """Raised when Slack reports failure in the payload rather than the status code."""

    def __init__(self, method: str, error: str) -> None:
        self.method = method
        self.error = error
        super().__init__(f"Slack {method} failed: {error}")


@dataclass(frozen=True)
class SlackUser:
    user_id: str
    display_name: str
    real_name: str | None = None
    email: str | None = None

    @property
    def name(self) -> str:
        return self.display_name or self.real_name or self.user_id


@dataclass(frozen=True)
class SlackMessage:
    ts: str
    user_id: str | None
    author: str
    text: str

    @property
    def posted_at(self) -> datetime | None:
        try:
            return datetime.fromtimestamp(float(self.ts), tz=UTC)
        except (TypeError, ValueError):
            return None


@dataclass(frozen=True)
class SlackThread:
    channel_id: str
    channel_name: str
    thread_ts: str
    messages: list[SlackMessage]
    permalink: str

    @property
    def external_id(self) -> str:
        # Stable as replies accumulate, which is what lets content-hash upserts absorb
        # thread growth without special cases.
        return f"{self.channel_id}:{self.thread_ts}"

    @property
    def root(self) -> SlackMessage:
        return self.messages[0]

    @property
    def latest_at(self) -> datetime | None:
        """Newest message time, so recency reflects activity rather than when the thread began."""
        stamps = [message.posted_at for message in self.messages if message.posted_at]
        return max(stamps) if stamps else None

    @property
    def participants(self) -> list[str]:
        seen: list[str] = []
        for message in self.messages:
            if message.author and message.author not in seen:
                seen.append(message.author)
        return seen


@dataclass(frozen=True)
class SlackThreadPageResult:
    threads: list[SlackThread]
    pages_fetched: int
    rate_limit_remaining: int | None = None
    users: dict[str, SlackUser] = field(default_factory=dict)


def _integer_header(response: httpx.Response, name: str) -> int | None:
    value = response.headers.get(name)
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def build_permalink(workspace_domain: str, channel_id: str, ts: str) -> str:
    """Construct an archive link instead of calling `chat.getPermalink` per message.

    One extra API call per message would dominate the sync and burn the rate limit, and the archive
    URL format is stable.
    """
    domain = workspace_domain.strip().removesuffix(".slack.com").strip("/")
    return f"https://{domain}.slack.com/archives/{channel_id}/p{ts.replace('.', '')}"


class SlackConnector:
    def __init__(
        self,
        *,
        bot_token: str | None = None,
        workspace_domain: str | None = None,
        timeout_seconds: float = 20,
    ) -> None:
        self.bot_token = bot_token or settings.slack_bot_token
        self.workspace_domain = workspace_domain or settings.slack_workspace_domain or ""
        self.timeout_seconds = timeout_seconds
        if not self.bot_token:
            raise ValueError("SLACK_BOT_TOKEN is required.")
        if not self.workspace_domain:
            raise ValueError("SLACK_WORKSPACE_DOMAIN is required for citation permalinks.")

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.bot_token}",
            "Accept": "application/json",
        }

    async def _call(
        self, client: httpx.AsyncClient, method: str, params: dict[str, object]
    ) -> tuple[dict, int | None]:
        """Make one Slack API call.

        Slack signals API-level failure with HTTP 200 and `{"ok": false, "error": ...}`, so
        `raise_for_status()` on its own accepts errors silently. The `ok` flag is the real check.
        """
        response = await client.get(
            f"{SLACK_API_BASE}/{method}", headers=self._headers(), params=params
        )
        remaining = _integer_header(response, "x-rate-limit-remaining")
        if response.status_code == 429:
            raise SlackRateLimitError(_integer_header(response, "retry-after"))
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            raise SlackAPIError(method, str(payload.get("error") or "unknown_error"))
        return payload, remaining

    async def list_users(self, client: httpx.AsyncClient) -> dict[str, SlackUser]:
        """Resolve user ids once per sync; raw `U0123ABC` ids must never reach an answer."""
        users: dict[str, SlackUser] = {}
        cursor: str | None = None
        while True:
            params: dict[str, object] = {"limit": 200}
            if cursor:
                params["cursor"] = cursor
            payload, _ = await self._call(client, "users.list", params)
            for member in payload.get("members", []):
                profile = member.get("profile") or {}
                user_id = str(member.get("id") or "")
                if not user_id:
                    continue
                users[user_id] = SlackUser(
                    user_id=user_id,
                    display_name=str(
                        profile.get("display_name") or profile.get("real_name") or user_id
                    ),
                    real_name=str(profile.get("real_name")) if profile.get("real_name") else None,
                    email=str(profile.get("email")) if profile.get("email") else None,
                )
            cursor = (payload.get("response_metadata") or {}).get("next_cursor") or None
            if not cursor:
                break
        return users

    async def channel_name(self, client: httpx.AsyncClient, channel_id: str) -> str:
        """Resolve a channel id to its name.

        Costs one call per channel per sync, and without it every citation title reads
        `#C0BS7F85ADU`, which tells a reader nothing. Falls back to the id if the lookup is denied
        so a missing scope degrades the title rather than failing the sync.
        """
        try:
            payload, _ = await self._call(client, "conversations.info", {"channel": channel_id})
        except (SlackAPIError, httpx.HTTPError):
            return channel_id
        return str((payload.get("channel") or {}).get("name") or channel_id)

    def _message(self, raw: dict, users: dict[str, SlackUser]) -> SlackMessage:
        user_id = str(raw.get("user") or "") or None
        user = users.get(user_id or "")
        return SlackMessage(
            ts=str(raw.get("ts") or ""),
            user_id=user_id,
            author=user.name if user else (str(raw.get("username") or "") or "Unknown user"),
            text=str(raw.get("text") or "").strip(),
        )

    async def list_threads(
        self,
        channel_id: str,
        *,
        channel_name: str | None = None,
        oldest: datetime | None = None,
        max_messages: int = 500,
        max_pages: int = 20,
    ) -> SlackThreadPageResult:
        """Fetch channel history, then expand each message that has replies into a full thread."""
        threads: list[SlackThread] = []
        pages_fetched = 0
        remaining: int | None = None
        cursor: str | None = None
        roots: list[dict] = []

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            users = await self.list_users(client)
            resolved_name = channel_name or await self.channel_name(client, channel_id)
            while pages_fetched < max_pages and len(roots) < max_messages:
                params: dict[str, object] = {
                    "channel": channel_id,
                    "limit": min(200, max_messages - len(roots)),
                }
                if oldest is not None:
                    params["oldest"] = f"{oldest.timestamp():.6f}"
                if cursor:
                    params["cursor"] = cursor
                payload, remaining = await self._call(client, "conversations.history", params)
                page = payload.get("messages")
                if not isinstance(page, list):
                    raise SlackAPIError("conversations.history", "invalid_messages_payload")
                # Skip joins, leaves, and other system events; they carry no discussion.
                roots.extend(item for item in page if not item.get("subtype"))
                pages_fetched += 1
                cursor = (payload.get("response_metadata") or {}).get("next_cursor") or None
                if not cursor or not page:
                    break

            for raw in roots[:max_messages]:
                thread_ts = str(raw.get("thread_ts") or raw.get("ts") or "")
                # Replies are only fetched for roots that have them, so a quiet channel costs one
                # call rather than one per message.
                if int(raw.get("reply_count") or 0) > 0:
                    payload, remaining = await self._call(
                        client,
                        "conversations.replies",
                        {"channel": channel_id, "ts": thread_ts, "limit": 200},
                    )
                    raw_messages = [
                        item for item in payload.get("messages", []) if not item.get("subtype")
                    ]
                else:
                    raw_messages = [raw]
                messages = [self._message(item, users) for item in raw_messages]
                messages = [message for message in messages if message.text]
                if not messages:
                    continue
                threads.append(
                    SlackThread(
                        channel_id=channel_id,
                        channel_name=resolved_name,
                        thread_ts=thread_ts,
                        messages=messages,
                        permalink=build_permalink(self.workspace_domain, channel_id, thread_ts),
                    )
                )

        return SlackThreadPageResult(
            threads=threads,
            pages_fetched=pages_fetched,
            rate_limit_remaining=remaining,
            users=users,
        )
