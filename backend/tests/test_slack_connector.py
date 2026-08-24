import httpx
import pytest
import respx

from app.connectors.slack import (
    SlackAPIError,
    SlackConnector,
    SlackRateLimitError,
    build_permalink,
)
from app.services.ingestion import slack_thread_documents

USERS_OK = {
    "ok": True,
    "members": [
        {"id": "U1", "profile": {"display_name": "raghav", "real_name": "Raghav Rao"}},
        {"id": "U2", "profile": {"display_name": "", "real_name": "Sarah Kim"}},
    ],
}


def _connector() -> SlackConnector:
    return SlackConnector(bot_token="xoxb-test", workspace_domain="groundwork")


def _mock_users(router: respx.Router, channel_name: str = "general") -> None:
    """Mock the two lookups every sync performs before reading history."""
    router.get("https://slack.com/api/users.list").mock(
        return_value=httpx.Response(200, json=USERS_OK)
    )
    router.get("https://slack.com/api/conversations.info").mock(
        return_value=httpx.Response(
            200, json={"ok": True, "channel": {"name": channel_name}}
        )
    )


@pytest.mark.parametrize(
    ("domain", "expected"),
    [
        ("groundwork", "https://groundwork.slack.com/archives/C1/p1700000000000100"),
        ("groundwork.slack.com", "https://groundwork.slack.com/archives/C1/p1700000000000100"),
    ],
)
def test_permalink_is_a_real_archive_url(domain: str, expected: str) -> None:
    """Citations must be clickable. Built rather than fetched: one call per message would
    dominate the sync and burn the rate limit."""
    assert build_permalink(domain, "C1", "1700000000.000100") == expected


@respx.mock
async def test_api_level_failure_is_raised_despite_http_200() -> None:
    """Slack reports failure as HTTP 200 with ok:false, so raise_for_status alone accepts errors."""
    _mock_users(respx.mock)
    respx.get("https://slack.com/api/conversations.history").mock(
        return_value=httpx.Response(200, json={"ok": False, "error": "channel_not_found"})
    )

    with pytest.raises(SlackAPIError) as excinfo:
        await _connector().list_threads("C1")

    assert excinfo.value.error == "channel_not_found"


@respx.mock
async def test_rate_limit_surfaces_retry_after() -> None:
    _mock_users(respx.mock)
    respx.get("https://slack.com/api/conversations.history").mock(
        return_value=httpx.Response(429, headers={"retry-after": "30"}, json={"ok": False})
    )

    with pytest.raises(SlackRateLimitError) as excinfo:
        await _connector().list_threads("C1")

    assert excinfo.value.retry_after_seconds == 30


@respx.mock
async def test_history_follows_cursor_pagination() -> None:
    """Assert call count and ordering separately, per the lesson from the GitHub pagination test."""
    _mock_users(respx.mock)
    route = respx.get("https://slack.com/api/conversations.history").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "ok": True,
                    "messages": [{"ts": "2.0", "user": "U1", "text": "second"}],
                    "response_metadata": {"next_cursor": "PAGE2"},
                },
            ),
            httpx.Response(
                200,
                json={
                    "ok": True,
                    "messages": [{"ts": "1.0", "user": "U2", "text": "first"}],
                    "response_metadata": {"next_cursor": ""},
                },
            ),
        ]
    )

    result = await _connector().list_threads("C1")

    assert route.call_count == 2
    assert result.pages_fetched == 2
    assert [thread.root.text for thread in result.threads] == ["second", "first"]
    assert route.calls[0].request.url.params.get("cursor") is None
    assert route.calls[1].request.url.params["cursor"] == "PAGE2"


@respx.mock
async def test_thread_replies_are_assembled_into_one_document_unit() -> None:
    _mock_users(respx.mock)
    respx.get("https://slack.com/api/conversations.history").mock(
        return_value=httpx.Response(
            200,
            json={
                "ok": True,
                "messages": [
                    {"ts": "10.0", "user": "U1", "text": "Should we drop HuggingFace?",
                     "reply_count": 2, "thread_ts": "10.0"}
                ],
                "response_metadata": {"next_cursor": ""},
            },
        )
    )
    replies = respx.get("https://slack.com/api/conversations.replies").mock(
        return_value=httpx.Response(
            200,
            json={
                "ok": True,
                "messages": [
                    {"ts": "10.0", "user": "U1", "text": "Should we drop HuggingFace?"},
                    {"ts": "11.0", "user": "U2", "text": "Yes, cold starts were too slow."},
                    {"ts": "12.0", "user": "U1", "text": "Agreed, moving to Cohere."},
                ],
            },
        )
    )

    result = await _connector().list_threads("C1", channel_name="eng")

    assert replies.call_count == 1
    (thread,) = result.threads
    assert len(thread.messages) == 3
    assert thread.participants == ["raghav", "Sarah Kim"]
    # `thread_ts` keeps the id stable as replies accumulate.
    assert thread.external_id == "C1:10.0"
    # Recency reflects the newest reply, not when the thread started.
    assert thread.latest_at is not None and thread.latest_at.timestamp() == 12.0


@respx.mock
async def test_messages_without_replies_cost_no_extra_call() -> None:
    _mock_users(respx.mock)
    respx.get("https://slack.com/api/conversations.history").mock(
        return_value=httpx.Response(
            200,
            json={
                "ok": True,
                "messages": [{"ts": "1.0", "user": "U1", "text": "standalone"}],
                "response_metadata": {"next_cursor": ""},
            },
        )
    )
    replies = respx.get("https://slack.com/api/conversations.replies")

    result = await _connector().list_threads("C1")

    assert replies.call_count == 0
    assert len(result.threads) == 1


@respx.mock
async def test_system_events_and_empty_messages_are_skipped() -> None:
    """Joins and leaves carry no discussion and would dilute retrieval."""
    _mock_users(respx.mock)
    respx.get("https://slack.com/api/conversations.history").mock(
        return_value=httpx.Response(
            200,
            json={
                "ok": True,
                "messages": [
                    {"ts": "1.0", "user": "U1", "text": "joined", "subtype": "channel_join"},
                    {"ts": "2.0", "user": "U1", "text": "   "},
                    {"ts": "3.0", "user": "U1", "text": "real content"},
                ],
                "response_metadata": {"next_cursor": ""},
            },
        )
    )

    result = await _connector().list_threads("C1")

    assert [thread.root.text for thread in result.threads] == ["real content"]


@respx.mock
async def test_documents_resolve_user_ids_and_never_leak_them() -> None:
    _mock_users(respx.mock)
    respx.get("https://slack.com/api/conversations.history").mock(
        return_value=httpx.Response(
            200,
            json={
                "ok": True,
                "messages": [{"ts": "5.0", "user": "U2", "text": "Pinned bcrypt to v4."}],
                "response_metadata": {"next_cursor": ""},
            },
        )
    )

    result = await _connector().list_threads("C1", channel_name="eng")
    (document,) = slack_thread_documents("askbase", result.threads)

    assert document.source_type == "slack"
    assert document.external_id == "C1:5.0"
    assert document.author == "Sarah Kim"
    assert "U2" not in document.title
    assert "Sarah Kim: Pinned bcrypt to v4." in document.content
    assert document.url.startswith("https://groundwork.slack.com/archives/C1/")
    # Identities are normalized like GitHub authors and Jira assignees, so an exact-match tool
    # could be added later without re-ingesting.
    assert "sarah kim" in document.author_identities


@respx.mock
async def test_channel_ids_are_resolved_to_names_for_citations() -> None:
    """A citation reading `#C0BS7F85ADU` tells a reader nothing; live data caught this."""
    respx.get("https://slack.com/api/users.list").mock(
        return_value=httpx.Response(200, json=USERS_OK)
    )
    info = respx.get("https://slack.com/api/conversations.info").mock(
        return_value=httpx.Response(
            200, json={"ok": True, "channel": {"id": "C1", "name": "decisions"}}
        )
    )
    respx.get("https://slack.com/api/conversations.history").mock(
        return_value=httpx.Response(
            200,
            json={
                "ok": True,
                "messages": [{"ts": "5.0", "user": "U1", "text": "Do we deploy to EC2?"}],
                "response_metadata": {"next_cursor": ""},
            },
        )
    )

    result = await _connector().list_threads("C1")
    (document,) = slack_thread_documents("askbase", result.threads)

    assert info.call_count == 1
    assert document.title.startswith("#decisions — ")
    assert "C1" not in document.title


@respx.mock
async def test_channel_name_lookup_failure_degrades_instead_of_failing_the_sync() -> None:
    respx.get("https://slack.com/api/users.list").mock(
        return_value=httpx.Response(200, json=USERS_OK)
    )
    respx.get("https://slack.com/api/conversations.info").mock(
        return_value=httpx.Response(200, json={"ok": False, "error": "missing_scope"})
    )
    respx.get("https://slack.com/api/conversations.history").mock(
        return_value=httpx.Response(
            200,
            json={
                "ok": True,
                "messages": [{"ts": "5.0", "user": "U1", "text": "hello"}],
                "response_metadata": {"next_cursor": ""},
            },
        )
    )

    result = await _connector().list_threads("C1")

    assert result.threads[0].channel_name == "C1"
