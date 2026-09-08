"""Ingest the commits a GitHub push webhook names.

Polling cannot see every commit. `GET /repos/{repo}/commits?since=...` filters on the *author*
date, so a rebased, cherry-picked or backdated commit arrives with a date behind the overlap cursor
and no future poll will ever return it. Not late: never seen. Since the exact-answer path is
deterministic SQL over what was indexed, `latest_commit` then names the wrong commit confidently,
which is the failure mode this project exists to avoid.

Addressing each commit by sha is what closes that gap. The cursor is never consulted here.

Two things this deliberately does not do:

- **It does not advance the polling cursor.** `last_succeeded_at` means "everything up to here has
  been fetched", which a payload of two shas does not establish. Advancing it would let a push that
  arrived while the instance was stopped fall permanently between the cursor and the webhook.
  GitHub does not retry a delivery that could not connect, and this box is stopped between demos,
  so polling stays the reconciliation half of the strategy.
- **It does not raise.** It runs detached from the request that scheduled it, so there is no caller
  left to receive an error. Failures are logged and swallowed, as in `startup_sync`.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.github import GitHubCommit, GitHubConnector
from app.core.observability import logger
from app.db.models import Project
from app.db.session import SessionFactory
from app.services.ingestion import github_commit_documents, ingest_documents
from app.services.llm import embedding_client

# A push carries at most 20 commits in the payload; larger pushes are truncated by GitHub and the
# remainder is left to the reconciling poll. Bounded anyway so a forced push of a long branch
# cannot turn one delivery into hundreds of API calls.
MAX_COMMITS_PER_DELIVERY = 20


def commit_shas(payload: dict) -> list[str]:
    """The shas a push payload names, in order, de-duplicated.

    A commit can appear in both `commits` and `head_commit`, and a force push can repeat one across
    entries. Fetching the same sha twice is harmless but wasteful of a rate limit that is shared
    with polling.
    """
    shas: list[str] = []
    for entry in payload.get("commits") or []:
        if isinstance(entry, dict) and isinstance(entry.get("id"), str):
            shas.append(entry["id"])
    head = payload.get("head_commit")
    if isinstance(head, dict) and isinstance(head.get("id"), str):
        shas.append(head["id"])
    return list(dict.fromkeys(shas))[:MAX_COMMITS_PER_DELIVERY]


async def project_for_repo(session: AsyncSession, full_name: str) -> Project | None:
    """The project indexing this repository, matched case-insensitively.

    GitHub preserves the case an owner typed; a project row may hold a different one. Matching
    exactly would silently ignore deliveries for a repository that is plainly configured.
    """
    statement = select(Project).where(Project.repo.ilike(full_name))
    return (await session.execute(statement)).scalars().first()


async def ingest_push(project_id: str, repo: str, shas: list[str]) -> dict[str, int]:
    """Fetch each sha and put it through the ordinary ingestion path."""
    connector = GitHubConnector()
    commits: list[GitHubCommit] = []
    for sha in shas:
        commits.append(await connector.get_commit(repo, sha))
    if not commits:
        return {"documents": 0, "chunks": 0, "embedded": 0}
    documents = github_commit_documents(project_id, commits)
    async with SessionFactory() as session:
        # `ingest_documents` upserts on (project_id, source_type, external_id) and compares content
        # hashes, so a redelivered payload re-embeds nothing. Idempotency is a property of the
        # existing path rather than something this module adds.
        return await ingest_documents(session, documents, ollama=embedding_client())


async def handle_push(project_id: str, repo: str, shas: list[str]) -> None:
    """Entry point for the background task. Logs its own outcome; raises nothing."""
    try:
        result = await ingest_push(project_id, repo, shas)
    except Exception as exc:  # noqa: BLE001 - nothing upstream can act on this
        logger.warning(
            "github webhook ingest failed",
            extra={"project_id": project_id, "repo": repo, "commits": len(shas),
                   "error": str(exc)[:200]},
        )
        return
    logger.info(
        "github webhook ingested",
        extra={"project_id": project_id, "repo": repo, "commits": len(shas), **result},
    )
