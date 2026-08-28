"""Empty sample projects for the demo project picker.

This module used to carry fabricated Project Atlas evidence — blockers, sprint plans, Slack
updates — which `get_weekly_brief_evidence` returned for *any* project, so a real repository whose
retrieval missed received that fiction presented as cited fact. Scoping it to the sample projects
contained the leak but kept the mechanism.

The evidence is gone now. What remains is two project shells with no documents, so a question
asked against them takes the same no-evidence path as any other unsynced project: grade
`incorrect`, no citations, and an explicit gap. Nothing here can be cited, because there is
nothing here.
"""

from app.models.schemas import ProjectSummary

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
