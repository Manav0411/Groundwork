from datetime import UTC, datetime

import pytest
import respx
from httpx import Response

from app.connectors.jira import JiraConnector, JiraRateLimitError, adf_to_text


def issue_payload(key: str, summary: str = "Build connector") -> dict[str, object]:
    return {
        "key": key,
        "fields": {
            "summary": summary,
            "description": {
                "type": "doc",
                "content": [
                    {
                        "type": "paragraph",
                        "content": [{"type": "text", "text": "Read Jira safely."}],
                    }
                ],
            },
            "status": {
                "name": "In Progress",
                "statusCategory": {"key": "indeterminate"},
            },
            "priority": {"name": "High"},
            "issuetype": {"name": "Task"},
            "assignee": {"displayName": "Manav Goel", "accountId": "account-1"},
            "reporter": {"displayName": "Manav Goel", "accountId": "account-1"},
            "labels": ["backend"],
            "comment": {
                "comments": [
                    {
                        "author": {"displayName": "Manav Goel"},
                        "body": {
                            "type": "doc",
                            "content": [
                                {
                                    "type": "paragraph",
                                    "content": [{"type": "text", "text": "Looks good."}],
                                }
                            ],
                        },
                    }
                ]
            },
            "created": "2026-08-16T10:00:00.000+0000",
            "updated": "2026-08-16T11:00:00.000+0000",
        },
    }


def test_adf_to_text_preserves_blocks() -> None:
    value = {
        "type": "doc",
        "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": "First"}]},
            {"type": "paragraph", "content": [{"type": "text", "text": "Second"}]},
        ],
    }

    assert adf_to_text(value) == "First\nSecond\n"


@pytest.mark.asyncio
@respx.mock
async def test_list_issues_discovers_cloud_id_and_follows_token_pagination() -> None:
    respx.get("https://jira.test/_edge/tenant_info").mock(
        return_value=Response(200, json={"cloudId": "cloud-123"})
    )
    route = respx.post("https://api.atlassian.com/ex/jira/cloud-123/rest/api/3/search/jql").mock(
        side_effect=[
            Response(
                200,
                json={"issues": [issue_payload("ASK-1")], "nextPageToken": "page-2"},
                headers={"x-ratelimit-remaining": "998"},
            ),
            Response(
                200,
                json={"issues": [issue_payload("ASK-2", "Second issue")]},
                headers={"x-ratelimit-remaining": "997"},
            ),
        ]
    )
    connector = JiraConnector(site_url="https://jira.test", token="scoped-token")

    result = await connector.list_issues(
        "ASK",
        updated_since=datetime(2026, 8, 1, tzinfo=UTC),
    )

    assert route.call_count == 2
    assert [issue.key for issue in result.issues] == ["ASK-1", "ASK-2"]
    assert result.issues[0].description == "Read Jira safely."
    assert result.issues[0].comments == ["Manav Goel: Looks good."]
    assert result.rate_limit_remaining == 997
    assert route.calls[0].request.headers["authorization"] == "Bearer scoped-token"
    first_body = route.calls[0].request.content.decode()
    second_body = route.calls[1].request.content.decode()
    assert "updated" in first_body
    assert "page-2" in second_body


@pytest.mark.asyncio
@respx.mock
async def test_list_issues_surfaces_rate_limit_retry() -> None:
    respx.post("https://jira-api.test/rest/api/3/search/jql").mock(
        return_value=Response(429, headers={"retry-after": "30"})
    )
    connector = JiraConnector(
        site_url="https://jira.test",
        token="scoped-token",
        api_base_url="https://jira-api.test",
    )

    with pytest.raises(JiraRateLimitError) as error:
        await connector.list_issues("ASK")

    assert error.value.retry_after_seconds == 30
