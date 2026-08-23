"""Tavily web search — the last corrective step.

Reached only when the project's own corpus has been graded insufficient, so it answers the case
where a question is legitimate but its answer was never going to be in GitHub or Jira. Disabled
unless `TAVILY_API_KEY` is set, which is the default.

Evidence from here is deliberately marked `source_type="web"` and can never produce a `correct`
grade: an answer assembled from the public internet is not the same claim as an answer traced to
the organisation's own records.
"""

from dataclasses import dataclass

import httpx

from app.core.config import settings
from app.models.schemas import Citation, EvidenceItem

TAVILY_SEARCH_URL = "https://api.tavily.com/search"


class TavilyError(RuntimeError):
    """Raised when the web search provider cannot answer."""


@dataclass(frozen=True)
class WebResult:
    title: str
    url: str
    content: str
    score: float


class TavilyConnector:
    def __init__(self, api_key: str | None = None, timeout_seconds: float | None = None) -> None:
        self.api_key = api_key or settings.tavily_api_key
        self.timeout_seconds = timeout_seconds or settings.tavily_timeout_seconds

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    async def search(self, query: str, max_results: int | None = None) -> list[WebResult]:
        if not self.enabled:
            raise TavilyError("Tavily is not configured; set TAVILY_API_KEY to enable it.")
        payload = {
            "query": query,
            "max_results": max(1, min(max_results or settings.tavily_max_results, 10)),
            "search_depth": "basic",
        }
        # The key goes in the Authorization header. Passing it as an `api_key` body field is the
        # older interface and is now treated as unauthenticated, which surfaces as a 432 rather
        # than a 401.
        headers = {"Authorization": f"Bearer {self.api_key}"}
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(TAVILY_SEARCH_URL, json=payload, headers=headers)
            if response.status_code == 429:
                raise TavilyError("Tavily rate limit exceeded.")
            if response.status_code == 432:
                raise TavilyError(
                    "Tavily rejected the request as exceeding the plan's usage limit (432); "
                    "check that TAVILY_API_KEY is valid and has remaining credits."
                )
            response.raise_for_status()
            data = response.json()

        results: list[WebResult] = []
        for item in data.get("results", []):
            url = str(item.get("url") or "").strip()
            # A citation must be clickable and verifiable, so drop anything without a real URL.
            if not url.startswith("https://"):
                continue
            results.append(
                WebResult(
                    title=str(item.get("title") or url),
                    url=url,
                    content=str(item.get("content") or "").strip(),
                    score=float(item.get("score") or 0.0),
                )
            )
        return results


def web_results_to_response(
    results: list[WebResult],
) -> tuple[list[EvidenceItem], list[Citation]]:
    """Shape web results like retrieved evidence, but labelled `web` so the source stays visible.

    `persist_query_run` maps only `chunk-` prefixed evidence ids to a chunk row, so these persist
    with a null `chunk_id` and `document_id` without any schema change.
    """
    evidence: list[EvidenceItem] = []
    citations: list[Citation] = []
    for ordinal, result in enumerate(results, start=1):
        citations.append(
            Citation(id=ordinal, source_type="web", title=result.title, url=result.url)
        )
        evidence.append(
            EvidenceItem(
                id=f"web-{ordinal}",
                source_type="web",
                title=result.title,
                snippet=result.content[:500],
                citation_id=ordinal,
                # Public web content is the least authoritative evidence this system handles.
                authority=0.4,
            )
        )
    return evidence, citations
