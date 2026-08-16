from datetime import UTC, datetime

import pytest
import respx
from httpx import Response

from app.connectors.github import GitHubConnector, GitHubRateLimitError


def commit_payload(sha: str, name: str = "Raghav") -> dict[str, object]:
    return {
        "sha": sha,
        "html_url": f"https://github.com/acme/project/commit/{sha}",
        "author": {"login": "raghav-dev"},
        "commit": {
            "message": f"Commit {sha}",
            "author": {
                "name": name,
                "email": "raghav@example.com",
                "date": "2026-08-10T09:00:00Z",
            },
            "committer": {
                "name": name,
                "email": "raghav@example.com",
                "date": "2026-08-10T09:05:00Z",
            },
        },
    }


@pytest.mark.asyncio
@respx.mock
async def test_list_commits_follows_github_link_pagination() -> None:
    route = respx.get("https://api.github.test/repos/acme/project/commits").mock(
        side_effect=[
            Response(
                200,
                json=[commit_payload("first")],
                headers={
                    "link": (
                        '<https://api.github.test/repos/acme/project/commits?page=2>; rel="next"'
                    ),
                    "x-ratelimit-remaining": "4999",
                    "x-ratelimit-reset": "1786352400",
                },
            ),
            Response(
                200,
                json=[commit_payload("second")],
                headers={"x-ratelimit-remaining": "4998"},
            ),
        ]
    )
    connector = GitHubConnector(base_url="https://api.github.test", token="test-token")

    result = await connector.list_commits(
        "acme/project",
        since=datetime(2026, 8, 1, tzinfo=UTC),
        max_commits=10,
    )

    assert route.call_count == 2
    assert [commit.sha for commit in result.commits] == ["first", "second"]
    assert result.pages_fetched == 2
    assert result.rate_limit_remaining == 4998
    assert route.calls[0].request.headers["x-github-api-version"] == "2026-03-10"
    assert route.calls[0].request.url.params["per_page"] == "100"
    assert "since" in route.calls[0].request.url.params


@pytest.mark.asyncio
@respx.mock
async def test_list_commits_surfaces_rate_limit_reset() -> None:
    respx.get("https://api.github.test/repos/acme/project/commits").mock(
        return_value=Response(
            403,
            json={"message": "API rate limit exceeded"},
            headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1786352400"},
        )
    )
    connector = GitHubConnector(base_url="https://api.github.test")

    with pytest.raises(GitHubRateLimitError) as error:
        await connector.list_commits("acme/project")

    assert error.value.reset_at is not None


@pytest.mark.asyncio
@respx.mock
async def test_list_commits_handles_secondary_rate_limit_retry_after() -> None:
    respx.get("https://api.github.test/repos/acme/project/commits").mock(
        return_value=Response(
            403,
            json={"message": "You have exceeded a secondary rate limit"},
            headers={"x-ratelimit-remaining": "42", "retry-after": "60"},
        )
    )
    connector = GitHubConnector(base_url="https://api.github.test")

    with pytest.raises(GitHubRateLimitError) as error:
        await connector.list_commits("acme/project")

    assert error.value.retry_after_seconds == 60
    assert error.value.reset_at is not None
