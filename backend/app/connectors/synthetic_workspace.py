from app.models.schemas import (
    Citation,
    EvidenceItem,
    ProjectSummary,
    TimelineItem,
)

PROJECTS = [
    ProjectSummary(
        id="project-atlas",
        name="Project Atlas",
        repo="octocat/Hello-World",
        status="On Track",
        health="green",
    ),
    ProjectSummary(
        id="project-orion",
        name="Project Orion",
        repo="octocat/Spoon-Knife",
        status="Blocked",
        health="yellow",
    ),
]


def get_projects() -> list[ProjectSummary]:
    return PROJECTS


def get_weekly_brief_evidence(project_id: str) -> tuple[list[EvidenceItem], list[Citation]]:
    citations = [
        Citation(id=1, source_type="jira", title="ATLAS-42 Payment gateway instability"),
        Citation(id=2, source_type="jira", title="ATLAS-47 Data backfill job failing"),
        Citation(id=3, source_type="docs", title="Project Atlas Sprint 24 Plan"),
        Citation(id=4, source_type="message", title="Slack update from payments channel"),
        Citation(id=5, source_type="github", title="Recent backend commits"),
    ]
    evidence = [
        EvidenceItem(
            id="ev-1",
            source_type="jira",
            title="Payment gateway sandbox instability",
            snippet="High impact blocker owned by Sarah Kim, updated May 17.",
            citation_id=1,
            authority=0.9,
        ),
        EvidenceItem(
            id="ev-2",
            source_type="jira",
            title="Data backfill job failing in staging",
            snippet="Medium impact issue owned by Jordan Lee, updated May 16.",
            citation_id=2,
            authority=0.85,
        ),
        EvidenceItem(
            id="ev-3",
            source_type="docs",
            title="Sprint 24 plan",
            snippet="Delivery velocity improved 12% compared with last week.",
            citation_id=3,
            authority=0.75,
        ),
        EvidenceItem(
            id="ev-4",
            source_type="message",
            title="Payments channel update",
            snippet="Stripe Connect chosen for multi-tenant payouts; rollout behind feature flag.",
            citation_id=4,
            authority=0.7,
        ),
        EvidenceItem(
            id="ev-5",
            source_type="github",
            title="Recent commits",
            snippet="Backend commits include retry logic, upload flow, and dependency updates.",
            citation_id=5,
            authority=0.8,
        ),
    ]
    return evidence, citations


def get_timeline(project_id: str) -> list[TimelineItem]:
    return [
        TimelineItem(
            id="tl-1",
            timestamp="2026-08-09T09:00:00Z",
            source_type="github",
            title="Commit batch synced",
            summary="Recent backend commits indexed for Project Atlas.",
        ),
        TimelineItem(
            id="tl-2",
            timestamp="2026-08-08T15:30:00Z",
            source_type="jira",
            title="Payment gateway blocker updated",
            summary="Owner confirmed external sandbox instability remains unresolved.",
        ),
        TimelineItem(
            id="tl-3",
            timestamp="2026-08-07T11:10:00Z",
            source_type="docs",
            title="Sprint 24 plan updated",
            summary="Scope reduced to protect payment integration milestone.",
        ),
    ]
