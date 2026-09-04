"""Contributor identity: what normalises to the same token, and who counts as the same person.

Both rules used to live in two places that disagreed, so these are unit tests rather than only
integration ones: the DB is not what makes them true.
"""

import pytest

from app.services.identity import (
    group_by_shared_identity,
    normalize_identities,
    normalize_identity,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  RAGHAV   Sharma ", "raghav sharma"),
        ("Raghav  Rao", "raghav rao"),
        ("Manav0411", "manav0411"),
        ("ME.ManavGoel@Gmail.com", "me.manavgoel@gmail.com"),
    ],
)
def test_normalisation_folds_case_and_whitespace_runs(raw: str, expected: str) -> None:
    assert normalize_identity(raw) == expected


def test_the_stored_form_drops_empties_dedupes_and_sorts() -> None:
    values = ["Manav Goel", None, "manav0411", "  ", "MANAV GOEL", "manav0411"]

    assert normalize_identities(values) == ["manav goel", "manav0411"]


def test_ingestion_and_lookup_agree_on_a_name_with_an_internal_double_space() -> None:
    """The exact shape of the bug: written one way, searched another, never found."""
    stored = normalize_identities(["Raghav  Rao"])

    assert normalize_identity("Raghav Rao") in stored


def test_rows_sharing_a_login_are_one_person() -> None:
    """`Manav0411` and `Manav Goel`, same login and email, is the case in both indexed repos."""
    clusters = group_by_shared_identity(
        [
            ["manav0411", "me.manavgoel@gmail.com"],
            ["manav goel", "manav0411", "me.manavgoel@gmail.com"],
        ]
    )

    assert clusters == [[0, 1]]


def test_rows_sharing_nothing_are_different_people() -> None:
    clusters = group_by_shared_identity(
        [
            ["raghav rao", "raghav-rao", "raghav-rao@example.com"],
            ["raghav menon", "raghav-menon", "raghav-menon@example.com"],
        ]
    )

    assert clusters == [[0], [1]]


def test_merging_is_transitive() -> None:
    """A shares a login with B, B shares an email with C, so all three are one person."""
    clusters = group_by_shared_identity(
        [
            ["alex", "alex-dev"],
            ["alex-dev", "alex@example.com"],
            ["alex@example.com", "a. rivera"],
        ]
    )

    assert clusters == [[0, 1, 2]]


def test_an_empty_input_produces_no_clusters() -> None:
    assert group_by_shared_identity([]) == []


def test_a_row_with_no_identities_stands_alone() -> None:
    """Unassigned Jira issues carry an empty array and must not all collapse into one person."""
    assert group_by_shared_identity([[], [], ["someone"]]) == [[0], [1], [2]]
