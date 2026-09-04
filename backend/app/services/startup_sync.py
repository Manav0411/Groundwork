"""Refresh every project's sources when the process starts.

Nothing kept the index fresh. Syncs ran only when something called the sync endpoint, and all three
staleness thresholds are 60 minutes, so any demo opened after an idle hour led with `ambiguous` and
a staleness caveat on every structured answer. The answers were right and the hedge was honest; it
just made a correct system look unsure of itself on the first question anyone asked.

Startup is the right trigger for this deployment specifically. The instance is stopped between
demos to stay inside a free tier, so waking it is exactly the moment the index is most out of date
and the moment nobody is waiting on it yet.

The alternative was raising the staleness threshold. That would have silenced the signal rather
than fixing what it correctly reported, and the caveat is one of the more honest things the product
does.

Two properties this must have, both learned from the sync endpoints:

- **It cannot block startup.** Health has to answer immediately or the container looks unhealthy
  while it is merely busy. Work is scheduled onto the loop and awaited by nobody.
- **It cannot raise.** There is no caller to receive an error. `_run_sync_detached` already logs,
  counts and swallows, so failures stay visible in metrics and logs without taking the app down.
"""

import asyncio

from sqlalchemy import select

from app.core.config import settings
from app.core.observability import logger
from app.db.models import Project
from app.db.session import SessionFactory


async def _configured_sources() -> list[tuple[str, str]]:
    """Every (project, source) pair worth syncing, read from what each project has configured.

    Asking Slack to sync a project with no channels is answered with a rejection, which would fill
    the log with failures that are really just configuration.
    """
    async with SessionFactory() as session:
        projects = (await session.execute(select(Project))).scalars().all()
        pairs: list[tuple[str, str]] = []
        for project in projects:
            pairs.append((project.id, "github"))
            if project.jira_project_key:
                pairs.append((project.id, "jira"))
            if project.slack_channel_ids:
                pairs.append((project.id, "slack"))
        return pairs


async def _sync_all() -> None:
    # Imported here rather than at module scope: routes imports this module's caller, and taking
    # the dependency the other way at import time closes the loop.
    from app.api.routes import _run_sync_detached

    try:
        pairs = await _configured_sources()
    except Exception as exc:
        logger.warning("startup sync could not read projects", extra={"error": str(exc)[:200]})
        return

    logger.info("startup sync beginning", extra={"pairs": len(pairs)})
    # Sequential on purpose. These share one Ollama for embeddings and one connector rate limit
    # budget each; running them together would contend for both to no benefit, since nothing is
    # waiting on the result.
    for project_id, source in pairs:
        await _run_sync_detached(source, project_id, None)
    logger.info("startup sync finished", extra={"pairs": len(pairs)})


def schedule_startup_sync() -> None:
    """Kick the refresh onto the running loop, if it is enabled."""
    if not settings.startup_sync_enabled:
        return
    # Held in a module-level reference: asyncio keeps only a weak reference to a bare task, so one
    # that nobody awaits can be garbage-collected mid-flight.
    global _TASK
    _TASK = asyncio.create_task(_sync_all())


_TASK: asyncio.Task | None = None
