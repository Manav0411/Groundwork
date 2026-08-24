from dataclasses import dataclass, field
from datetime import datetime
from hashlib import sha256
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.github import GitHubCommit
from app.connectors.jira import JiraIssue
from app.connectors.slack import SlackThread
from app.connectors.synthetic_workspace import get_projects, get_weekly_brief_evidence
from app.db.models import DocumentChunk, Project, SourceDocument
from app.services.llm import OllamaClient


@dataclass(frozen=True)
class IngestDocument:
    project_id: str
    source_type: str
    external_id: str
    title: str
    content: str
    url: str | None = None
    author: str | None = None
    author_identities: list[str] = field(default_factory=list)
    source_created_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def chunk_text(text: str, max_chars: int = 1200, overlap_chars: int = 150) -> list[str]:
    normalized = " ".join(text.split())
    if not normalized:
        return []
    chunks: list[str] = []
    start = 0
    while start < len(normalized):
        end = min(start + max_chars, len(normalized))
        if end < len(normalized):
            boundary = normalized.rfind(" ", start, end)
            if boundary > start + max_chars // 2:
                end = boundary
        chunks.append(normalized[start:end].strip())
        if end >= len(normalized):
            break
        start = max(end - overlap_chars, start + 1)
    return chunks


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


async def upsert_project(session: AsyncSession, project: Project | Any) -> None:
    statement = insert(Project).values(
        id=project.id,
        name=project.name,
        repo=project.repo,
        jira_project_key=getattr(project, "jira_project_key", None),
        status=project.status,
        health=project.health,
    )
    statement = statement.on_conflict_do_update(
        index_elements=[Project.id],
        set_={
            "name": statement.excluded.name,
            "repo": statement.excluded.repo,
            "jira_project_key": statement.excluded.jira_project_key,
            "status": statement.excluded.status,
            "health": statement.excluded.health,
            "updated_at": func.now(),
        },
    )
    await session.execute(statement)


async def ingest_documents(
    session: AsyncSession,
    documents: list[IngestDocument],
    ollama: OllamaClient | None = None,
) -> dict[str, int]:
    embedded = 0
    chunks_written = 0
    for item in documents:
        content_hash = sha256(item.content.encode("utf-8")).hexdigest()
        existing_document = (
            await session.execute(
                select(SourceDocument.id, SourceDocument.content_hash).where(
                    SourceDocument.project_id == item.project_id,
                    SourceDocument.source_type == item.source_type,
                    SourceDocument.external_id == item.external_id,
                )
            )
        ).one_or_none()
        statement = insert(SourceDocument).values(
            project_id=item.project_id,
            source_type=item.source_type,
            external_id=item.external_id,
            title=item.title,
            content=item.content,
            url=item.url,
            author=item.author,
            author_identities=item.author_identities,
            source_created_at=item.source_created_at,
            content_hash=content_hash,
            source_metadata=item.metadata,
        )
        statement = statement.on_conflict_do_update(
            constraint="uq_source_documents_project_id",
            set_={
                "title": statement.excluded.title,
                "content": statement.excluded.content,
                "url": statement.excluded.url,
                "author": statement.excluded.author,
                "author_identities": statement.excluded.author_identities,
                "source_created_at": statement.excluded.source_created_at,
                "content_hash": statement.excluded.content_hash,
                "source_metadata": statement.excluded.source_metadata,
                "updated_at": func.now(),
            },
        ).returning(SourceDocument.id)
        document_id = (await session.execute(statement)).scalar_one()

        if existing_document is not None and existing_document.content_hash == content_hash:
            if ollama is not None:
                stored_chunks = list(
                    (
                        await session.scalars(
                            select(DocumentChunk)
                            .where(DocumentChunk.document_id == document_id)
                            .order_by(DocumentChunk.chunk_index)
                        )
                    ).all()
                )
                missing = [chunk for chunk in stored_chunks if chunk.embedding is None]
                if missing:
                    try:
                        generated = await ollama.embed([chunk.content for chunk in missing])
                        for chunk, embedding in zip(missing, generated, strict=True):
                            await session.execute(
                                update(DocumentChunk)
                                .where(DocumentChunk.id == chunk.id)
                                .values(embedding=embedding, updated_at=func.now())
                            )
                        embedded += len(generated)
                    except Exception:
                        pass
            continue

        await session.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document_id))
        chunks = chunk_text(item.content)
        embeddings: list[list[float] | None] = [None] * len(chunks)
        if chunks and ollama is not None:
            try:
                generated = await ollama.embed(chunks)
                embeddings = generated
                embedded += len(generated)
            except Exception:
                # Full-text retrieval remains available if the local embedding model is offline.
                pass
        if chunks:
            await session.execute(
                insert(DocumentChunk),
                [
                    {
                        "document_id": document_id,
                        "project_id": item.project_id,
                        "chunk_index": index,
                        "content": chunk,
                        "token_count": max(1, len(chunk) // 4),
                        "embedding": embeddings[index],
                    }
                    for index, chunk in enumerate(chunks)
                ],
            )
            chunks_written += len(chunks)
    await session.commit()
    return {"documents": len(documents), "chunks": chunks_written, "embedded": embedded}


async def seed_synthetic_workspace(session: AsyncSession) -> dict[str, int]:
    projects = get_projects()
    for project in projects:
        await upsert_project(session, project)
    await session.flush()

    documents: list[IngestDocument] = []
    for project in projects:
        evidence, citations = get_weekly_brief_evidence(project.id)
        citation_by_id = {citation.id: citation for citation in citations}
        for item in evidence:
            citation = citation_by_id[item.citation_id]
            documents.append(
                IngestDocument(
                    project_id=project.id,
                    source_type=item.source_type,
                    external_id=f"synthetic-{item.id}",
                    title=item.title,
                    content=item.snippet,
                    url=citation.url,
                    metadata={"synthetic": True, "authority": item.authority},
                )
            )
    stats = await ingest_documents(session, documents, OllamaClient())
    return {"projects": len(projects), **stats}


def github_commit_documents(project_id: str, commits: list[GitHubCommit]) -> list[IngestDocument]:
    def identities(commit: GitHubCommit) -> list[str]:
        values = [commit.author, commit.author_email, commit.author_login]
        return sorted({value.strip().casefold() for value in values if value and value.strip()})

    return [
        IngestDocument(
            project_id=project_id,
            source_type="github",
            external_id=commit.sha,
            title=commit.message.splitlines()[0],
            content=(
                f"Git commit {commit.sha} by {commit.author}. "
                f"Commit message: {commit.message}. Committed at {commit.date or 'unknown time'}."
            ),
            url=commit.url,
            author=commit.author,
            author_identities=identities(commit),
            source_created_at=_parse_datetime(commit.date),
            metadata={
                "sha": commit.sha,
                "author_email": commit.author_email,
                "author_login": commit.author_login,
                "committer": commit.committer,
                "authored_at": commit.authored_at,
                "committed_at": commit.committed_at,
            },
        )
        for commit in commits
    ]


def jira_issue_documents(project_id: str, issues: list[JiraIssue]) -> list[IngestDocument]:
    def assignee_identities(issue: JiraIssue) -> list[str]:
        if issue.assignee is None:
            return []
        values = [
            issue.assignee.display_name,
            issue.assignee.account_id,
            issue.assignee.email,
        ]
        return sorted({value.strip().casefold() for value in values if value and value.strip()})

    documents: list[IngestDocument] = []
    for issue in issues:
        assignee = issue.assignee.display_name if issue.assignee else "Unassigned"
        priority = issue.priority or "Unspecified"
        labels = ", ".join(issue.labels) if issue.labels else "None"
        comments = " ".join(f"Comment: {comment}" for comment in issue.comments)
        content = (
            f"Jira issue {issue.key}: {issue.summary}. Type: {issue.issue_type}. "
            f"Status: {issue.status}. Priority: {priority}. Assignee: {assignee}. "
            f"Labels: {labels}. Description: {issue.description or 'None'}. {comments}"
        ).strip()
        documents.append(
            IngestDocument(
                project_id=project_id,
                source_type="jira",
                external_id=issue.key,
                title=f"{issue.key} — {issue.summary}",
                content=content,
                url=issue.url,
                author=issue.reporter.display_name if issue.reporter else None,
                author_identities=assignee_identities(issue),
                source_created_at=_parse_datetime(issue.updated_at or issue.created_at),
                metadata={
                    "key": issue.key,
                    "summary": issue.summary,
                    "description": issue.description,
                    "status": issue.status,
                    "status_category": issue.status_category,
                    "priority": issue.priority,
                    "issue_type": issue.issue_type,
                    "assignee": issue.assignee.display_name if issue.assignee else None,
                    "assignee_account_id": issue.assignee.account_id if issue.assignee else None,
                    "reporter": issue.reporter.display_name if issue.reporter else None,
                    "labels": issue.labels,
                    "comment_count": len(issue.comments),
                    "created_at": issue.created_at,
                    "updated_at": issue.updated_at,
                },
            )
        )
    return documents


def slack_thread_documents(project_id: str, threads: list[SlackThread]) -> list[IngestDocument]:
    """One document per thread.

    A decision is a discussion, so the thread is the unit that holds the reasoning. Participant
    identities are normalized the same way GitHub authors and Jira assignees are, so an exact-match
    tool could be added later without re-ingesting.
    """
    documents: list[IngestDocument] = []
    for thread in threads:
        root = thread.root
        headline = root.text.splitlines()[0][:120] if root.text else "(no text)"
        transcript = " ".join(
            f"{message.author}: {message.text}" for message in thread.messages if message.text
        )
        identities = sorted(
            {
                value.strip().casefold()
                for message in thread.messages
                for value in (message.author, message.user_id)
                if value and value.strip()
            }
        )
        documents.append(
            IngestDocument(
                project_id=project_id,
                source_type="slack",
                external_id=thread.external_id,
                title=f"#{thread.channel_name} — {headline}",
                content=(
                    f"Slack thread in #{thread.channel_name} started by {root.author} with "
                    f"{len(thread.messages)} message(s). {transcript}"
                ),
                url=thread.permalink,
                author=root.author,
                author_identities=identities,
                source_created_at=thread.latest_at,
                metadata={
                    "channel_id": thread.channel_id,
                    "channel_name": thread.channel_name,
                    "thread_ts": thread.thread_ts,
                    "participants": thread.participants,
                    "message_count": len(thread.messages),
                    "permalink": thread.permalink,
                },
            )
        )
    return documents
