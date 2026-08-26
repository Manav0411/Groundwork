"""Read ground truth out of a project's corpus, so cases can be built without knowing the data.

Every other eval dataset in this repo hardcodes its expectations: `askbase.jsonl` names the SHA it
expects, `jira_askbase.jsonl` names the issue key. That is fine for guarding known behaviour and
useless for demonstrating that the system works on a project nobody wrote cases for — a dataset
whose answers were typed by hand from the data it tests cannot show generalization.

This module asks the database what is actually there. The suite then builds questions from those
values and checks the answers against them, so it runs unchanged against any project.

Ground truth comes from the *same tools the answer path uses*, not from parallel SQL. The first
version of this file grouped commits by `SourceDocument.author` and got a different answer to the
product, because that column holds whatever GitHub reported per commit — "Manav0411" on 31 of them
and "Manav Goel" on 5 — while `author_identities` correctly unifies both into one person. The probe
was wrong and the system was right, which is the expensive way round to discover that a harness
should not reimplement the thing it is checking.
"""

from dataclasses import dataclass, field

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import SourceDocument
from app.services.structured_github import latest_commit_by_author
from app.services.structured_jira import jira_issues_by_assignee


@dataclass
class CorpusFacts:
    """What a project's indexed corpus actually contains."""

    project_id: str
    commit_count: int = 0
    issue_count: int = 0
    top_author: str | None = None
    top_author_commits: int = 0
    newest_sha: str | None = None
    second_newest_sha: str | None = None
    sampled_sha: str | None = None
    issue_key: str | None = None
    issue_status: str | None = None
    assignee: str | None = None
    assignee_issue_key: str | None = None
    notes: list[str] = field(default_factory=list)

    def as_counts(self) -> dict[str, int]:
        return {
            "github_commits": self.commit_count,
            "jira_issues": self.issue_count,
            "commits_by_top_author": self.top_author_commits,
        }


def _metadata(column: str):
    return SourceDocument.source_metadata[column].astext


async def probe_corpus(session: AsyncSession, project_id: str) -> CorpusFacts:
    facts = CorpusFacts(project_id=project_id)

    def scoped(source_type: str):
        return select(SourceDocument).where(
            SourceDocument.project_id == project_id,
            SourceDocument.source_type == source_type,
        )

    facts.commit_count = int(
        (
            await session.execute(
                select(func.count()).select_from(scoped("github").subquery())
            )
        ).scalar_one()
    )
    facts.issue_count = int(
        (
            await session.execute(select(func.count()).select_from(scoped("jira").subquery()))
        ).scalar_one()
    )

    if facts.commit_count:
        # Group by normalized identity, not by the author column: one person commits under
        # several names, and the identity array is what the lookup actually matches on.
        identity = func.unnest(SourceDocument.author_identities).label("identity")
        identity_rows = (
            await session.execute(
                select(identity, func.count().label("total"))
                .where(
                    SourceDocument.project_id == project_id,
                    SourceDocument.source_type == "github",
                )
                .group_by(identity)
                .order_by(desc("total"), identity)
            )
        ).all()
        # An email is a valid identity but an unnatural thing to put in a question, and the author
        # regex stops at "@". Prefer a name-shaped one.
        named = [row for row in identity_rows if "@" not in row.identity]
        chosen = (named or identity_rows)[0] if identity_rows else None

        if chosen is not None:
            facts.top_author = chosen.identity
            # Ask the real tool how many commits that identity has, so the count the suite asserts
            # against is the count the product would use.
            newest = await latest_commit_by_author(session, project_id, facts.top_author, 0)
            facts.top_author_commits = newest.available
            facts.newest_sha = newest.sha
            if newest.available > 1:
                second = await latest_commit_by_author(session, project_id, facts.top_author, 1)
                facts.second_newest_sha = second.sha
        else:
            facts.notes.append("No GitHub commit carries an author, so author cases were skipped.")

        # A commit sampled from the middle of history rather than the top, so a case that names a
        # hash cannot pass by accidentally returning the newest one.
        sampled = (
            await session.execute(
                scoped("github")
                .order_by(desc(SourceDocument.source_created_at))
                .offset(min(2, facts.commit_count - 1))
                .limit(1)
            )
        ).scalar_one_or_none()
        if sampled is not None:
            facts.sampled_sha = str(sampled.source_metadata.get("sha") or sampled.external_id)

    if facts.issue_count:
        issue = (
            await session.execute(
                scoped("jira").order_by(desc(SourceDocument.source_created_at)).limit(1)
            )
        ).scalar_one_or_none()
        if issue is not None:
            facts.issue_key = str(issue.source_metadata.get("key") or issue.external_id)
            facts.issue_status = str(issue.source_metadata.get("status") or "")

        assigned = (
            await session.execute(
                scoped("jira")
                .where(_metadata("assignee").is_not(None))
                .order_by(desc(SourceDocument.source_created_at))
                .limit(1)
            )
        ).scalar_one_or_none()
        if assigned is None:
            facts.notes.append("No Jira issue has an assignee, so the assignee case was skipped.")
        else:
            candidate = str(assigned.source_metadata.get("assignee") or "")
            # Confirm through the tool: an assignee the lookup cannot resolve unambiguously would
            # make the case assert a failure of the corpus rather than of the code.
            lookup = await jira_issues_by_assignee(session, project_id, candidate)
            if lookup.status == "found" and lookup.issues:
                facts.assignee = candidate
                facts.assignee_issue_key = lookup.issues[0].key
            else:
                facts.notes.append(
                    f"Assignee {candidate!r} resolves as {lookup.status!r}; case skipped."
                )

    return facts
