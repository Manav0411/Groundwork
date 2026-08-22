"""Synthetic development workspace.

This is demo fixture data for the two `project-atlas` / `project-orion` sample projects only.
`get_weekly_brief_evidence` previously ignored its `project_id` argument and returned this fiction
for *any* project, so a real repository whose retrieval missed received fabricated Project Atlas
evidence presented as cited fact. Every accessor is now scoped to `SYNTHETIC_PROJECT_IDS`.
"""

from app.models.schemas import (
    Citation,
    EvidenceItem,
    ProjectSummary,
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


SYNTHETIC_PROJECT_IDS = frozenset(project.id for project in PROJECTS)


def get_projects() -> list[ProjectSummary]:
    return PROJECTS


def is_synthetic_project(project_id: str) -> bool:
    return project_id in SYNTHETIC_PROJECT_IDS


def get_weekly_brief_evidence(project_id: str) -> tuple[list[EvidenceItem], list[Citation]]:
    """Demo evidence for the sample projects. Returns nothing for a real project."""
    if not is_synthetic_project(project_id):
        return [], []
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
