import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models import ConnectorSyncState, DocumentChunk, SourceDocument
from app.services.identity import group_by_shared_identity, normalize_identity
from app.services.retrieval import RetrievedRecord

AUTHOR_PATTERN = re.compile(
    r"\bby\s+(.+?)(?="
    # Any prepositional clause ends the name, not only the ones naming a repository. Restricting
    # this to "on project|repo" meant "the reply by Manav on that?" captured "Manav on that" as an
    # author — which matches nobody, and worse, made the question look like it named a record so
    # follow-up resolution skipped it entirely.
    r"\s+(?:on|for|in|from|about|regarding|with|at|during)\b"
    # A positional clause ends it too: "by Manav0411 before 4121d76?" captured the hash as part of
    # the name.
    r"|\s+(?:before|after|preceding|prior\s+to)\b"
    r"|[?,!]|$)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class LatestCommitLookup:
    status: str
    author_query: str | None
    record: RetrievedRecord | None
    sha: str | None
    author: str | None
    candidates: list[str]
    last_synced_at: datetime | None
    stale: bool
    # How far back from the newest commit the answer is. 0 is "the latest".
    offset: int = 0
    available: int = 0


SHA_PATTERN = re.compile(r"\b[0-9a-f]{7,40}\b", re.IGNORECASE)

# "second-to-last", "3rd latest one", "the 50th commit", "one before that".
ORDINAL_WORDS = {
    "first": 0,
    "second": 1,
    "third": 2,
    "fourth": 3,
    "fifth": 4,
    "sixth": 5,
    "seventh": 6,
    "eighth": 7,
    "ninth": 8,
    "tenth": 9,
}
# The numeric branch matters as much as the words: "the 50th commit" was read as offset 0 and
# answered with the newest commit — the confidently wrong answer this whole path exists to avoid.
ORDINAL_PATTERN = re.compile(
    r"\b(?:the\s+)?(?:(\d{1,3})(?:st|nd|rd|th)|"
    r"(first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth))"
    r"[\s-]*(?:to[\s-]*)?(?:last|latest|most\s+recent|newest|commit|one)\b",
    re.IGNORECASE,
)
PREVIOUS_PATTERN = re.compile(
    # A bare "before" is positional only when a hash follows it: "the commit before 4121d76" asks
    # for a different commit, while "what changed before the release" is an ordinary question.
    r"\b(?:previous|prior|one\s+before|before\s+that|preceding|before\s+(?=[0-9a-f]{7,40}\b))",
    re.IGNORECASE,
)
MAX_COMMIT_OFFSET = 99


def extract_commit_author(query: str) -> str | None:
    match = AUTHOR_PATTERN.search(query.strip())
    if not match:
        return None
    author = " ".join(match.group(1).split()).strip(" '\".?!,")
    return author or None


def extract_commit_sha(query: str) -> str | None:
    """A commit hash named directly in the question.

    Requires at least one digit. Plenty of English words are pure hex — "defaced", "facade" — and
    without that check a harmless sentence would be read as naming a commit.
    """
    for match in SHA_PATTERN.finditer(query):
        candidate = match.group(0)
        if any(character.isdigit() for character in candidate):
            return candidate.lower()
    return None


def extract_commit_offset(query: str) -> int:
    """How far back from the newest commit the question is asking about.

    "What was the second-to-last commit by X?" used to route to the same lookup as "the last commit
    by X" and return the newest one — a confidently wrong answer that nothing distinguished from a
    right one. Position is part of the question, so it has to be part of the query.
    """
    match = ORDINAL_PATTERN.search(query)
    if match:
        numeric, word = match.group(1), match.group(2)
        # An out-of-range position is answered by refusing, not by clamping into a real commit.
        if numeric is not None:
            return min(max(int(numeric) - 1, 0), MAX_COMMIT_OFFSET)
        return ORDINAL_WORDS[word.casefold()]
    if PREVIOUS_PATTERN.search(query):
        return 1
    return 0


def describe_offset(offset: int) -> str:
    return {0: "latest", 1: "second most recent", 2: "third most recent"}.get(
        offset, f"{offset + 1}th most recent"
    )


def normalize_author_identity(author: str) -> str:
    """Kept as the name this module has always exported; the rule lives in `identity`."""
    return normalize_identity(author)


async def _sync_freshness(session: AsyncSession, project_id: str) -> tuple[datetime | None, bool]:
    last_synced_at = await session.scalar(
        select(ConnectorSyncState.last_succeeded_at).where(
            ConnectorSyncState.project_id == project_id,
            ConnectorSyncState.source_type == "github",
        )
    )
    if last_synced_at is None:
        return None, True
    stale_after = timedelta(minutes=settings.github_sync_stale_after_minutes)
    return last_synced_at, datetime.now(UTC) - last_synced_at > stale_after


def _record_from_row(row) -> RetrievedRecord:
    document = row.SourceDocument
    chunk = row.DocumentChunk
    return RetrievedRecord(
        chunk_id=chunk.id,
        document_id=document.id,
        source_type="github",
        title=document.title,
        content=chunk.content,
        url=document.url,
        source_timestamp=document.source_created_at,
        authority=0.95,
        lexical_score=1.0,
        vector_score=0.0,
    )


def _anchor_index(rows, anchor_sha: str | None) -> int | None:
    """Where to start counting: the named commit's position, or the newest.

    Returns None when the anchor was named but not found, so "the commit before <sha>" cannot
    silently become "the commit before the newest one".
    """
    if not anchor_sha:
        return 0
    for index, row in enumerate(rows):
        document = row.SourceDocument
        sha = str(document.source_metadata.get("sha") or document.external_id)
        if sha.lower().startswith(anchor_sha.lower()):
            return index
    return None


async def latest_commit_by_author(
    session: AsyncSession,
    project_id: str,
    author_query: str | None = None,
    offset: int = 0,
    anchor_sha: str | None = None,
) -> LatestCommitLookup:
    """The commit `offset` positions back from an anchor, ordered by commit time.

    The anchor is the author's newest commit unless the question named a specific hash — "what came
    before f4a941f?" counts from that commit, not from the top of the list.

    `author_query` is optional, and None means the whole project's history rather than one
    person's. "What was the last commit?" is the first thing a visitor asks, and it used to be
    answered with "I need an author name" — a demand for input that the data did not require, since
    the newest commit overall is exactly as well defined as the newest by a named author. Ordering
    and offsets are identical either way; only the filter differs, so both paths share everything
    below.
    """
    offset = max(0, min(offset, MAX_COMMIT_OFFSET))
    last_synced_at, stale = await _sync_freshness(session, project_id)
    base = (
        select(SourceDocument, DocumentChunk)
        .join(
            DocumentChunk,
            (DocumentChunk.document_id == SourceDocument.id) & (DocumentChunk.chunk_index == 0),
        )
        .where(
            SourceDocument.project_id == project_id,
            SourceDocument.source_type == "github",
        )
    )
    ordered = base.order_by(desc(SourceDocument.source_created_at), desc(SourceDocument.id))
    normalized = normalize_author_identity(author_query) if author_query is not None else None
    exact_rows = (
        await session.execute(
            ordered.limit(100)
            if normalized is None
            else ordered.where(SourceDocument.author_identities.contains([normalized])).limit(100)
        )
    ).all()
    start = _anchor_index(exact_rows, anchor_sha)
    if start is None:
        return LatestCommitLookup(
            status="not_found",
            author_query=author_query,
            record=None,
            sha=None,
            author=None,
            candidates=[],
            last_synced_at=last_synced_at,
            stale=stale,
            offset=offset,
            available=len(exact_rows),
        )
    target = start + offset
    if exact_rows and len(exact_rows) > target:
        row = exact_rows[target]
        document = row.SourceDocument
        return LatestCommitLookup(
            status="found",
            author_query=author_query,
            record=_record_from_row(row),
            sha=str(document.source_metadata.get("sha") or document.external_id),
            author=document.author,
            candidates=[],
            last_synced_at=last_synced_at,
            stale=stale,
            # The absolute position, so the answer's wording matches the list rather than the
            # relative step taken from an anchor part-way down it.
            offset=target,
            available=len(exact_rows),
        )
    if exact_rows:
        # The author matched but does not have that many commits. Saying so is the answer; the
        # newest commit is not a substitute for the one that was asked for.
        return LatestCommitLookup(
            status="out_of_range",
            author_query=author_query,
            record=None,
            sha=None,
            author=exact_rows[0].SourceDocument.author,
            candidates=[],
            last_synced_at=last_synced_at,
            stale=stale,
            offset=target,
            available=len(exact_rows),
        )

    if normalized is None:
        # Nothing to partially match against: the question named no author, so an empty result
        # means the project has no indexed commits at all.
        return LatestCommitLookup(
            status="not_found",
            author_query=None,
            record=None,
            sha=None,
            author=None,
            candidates=[],
            last_synced_at=last_synced_at,
            stale=stale,
            offset=offset,
            available=0,
        )

    recent_rows = (
        await session.execute(
            base.order_by(desc(SourceDocument.source_created_at), desc(SourceDocument.id)).limit(
                100
            )
        )
    ).all()
    partial_rows = [
        item
        for item in recent_rows
        if any(normalized in identity for identity in item.SourceDocument.author_identities)
    ]
    # Cluster by shared identity token rather than by display name. Keying on the display name
    # meant one human reported two ways -- `Manav0411` on some commits and `Manav Goel` on others,
    # same login and same email -- came back as an ambiguity and was refused, even though the
    # identity arrays overlapped plainly. The exact-match path above unified them, so this only
    # ever surfaced on a partial query.
    clusters = group_by_shared_identity(
        [list(item.SourceDocument.author_identities) for item in partial_rows]
    )
    candidates = sorted(
        {
            partial_rows[cluster[0]].SourceDocument.author or "Unknown author"
            for cluster in clusters
        },
        key=str.casefold,
    )
    if len(clusters) == 1 and partial_rows:
        partial_start = _anchor_index(partial_rows, anchor_sha)
        if partial_start is None:
            partial_start = 0
        offset = partial_start + offset
        if len(partial_rows) <= offset:
            return LatestCommitLookup(
                status="out_of_range",
                author_query=author_query,
                record=None,
                sha=None,
                author=partial_rows[0].SourceDocument.author,
                candidates=candidates,
                last_synced_at=last_synced_at,
                stale=stale,
                offset=offset,
                available=len(partial_rows),
            )
        row = partial_rows[offset]
        document = row.SourceDocument
        return LatestCommitLookup(
            status="found",
            author_query=author_query,
            record=_record_from_row(row),
            sha=str(document.source_metadata.get("sha") or document.external_id),
            author=document.author,
            candidates=candidates,
            last_synced_at=last_synced_at,
            stale=stale,
            offset=offset,
            available=len(partial_rows),
        )
    return LatestCommitLookup(
        status="ambiguous" if candidates else "not_found",
        author_query=author_query,
        record=None,
        sha=None,
        author=None,
        candidates=candidates,
        last_synced_at=last_synced_at,
        stale=stale,
    )


async def commit_by_sha(session: AsyncSession, project_id: str, sha: str) -> LatestCommitLookup:
    """Look up one commit by hash, exactly.

    This path used to go through retrieval and a 3B model, which retrieved the right commit and
    then answered "I couldn't find any information about commit f4a941f" — contradicting the
    evidence sitting in position one. A question that names a record has an exact answer, so it
    gets the same deterministic treatment as every other exact question here.
    """
    last_synced_at, stale = await _sync_freshness(session, project_id)
    rows = (
        await session.execute(
            select(SourceDocument, DocumentChunk)
            .join(
                DocumentChunk,
                (DocumentChunk.document_id == SourceDocument.id)
                & (DocumentChunk.chunk_index == 0),
            )
            .where(
                SourceDocument.project_id == project_id,
                SourceDocument.source_type == "github",
                SourceDocument.external_id.ilike(f"{sha}%"),
            )
            .limit(2)
        )
    ).all()
    if len(rows) != 1:
        return LatestCommitLookup(
            status="ambiguous" if len(rows) > 1 else "not_found",
            author_query=None,
            record=None,
            sha=None,
            author=None,
            candidates=[str(row.SourceDocument.external_id)[:7] for row in rows],
            last_synced_at=last_synced_at,
            stale=stale,
        )
    row = rows[0]
    document = row.SourceDocument
    return LatestCommitLookup(
        status="found",
        author_query=None,
        record=_record_from_row(row),
        sha=str(document.source_metadata.get("sha") or document.external_id),
        author=document.author,
        candidates=[],
        last_synced_at=last_synced_at,
        stale=stale,
        available=1,
    )
