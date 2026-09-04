"""Who counts as the same person.

Contributor identity is not one string. GitHub reports a display name, a login and an email, and
the same human appears under different combinations of them across commits: the two indexed
projects both carry one person as `Manav0411` on some commits and `Manav Goel` on others. Jira and
Slack have the same shape with different field names.

Two rules live here because both sides of the problem have to agree on them, and when they did not,
they were wrong in opposite directions:

- **Normalisation** used to differ between write and read. Ingestion stored `strip().casefold()`
  while lookup searched `" ".join(casefold().split())`, so a display name carrying an internal
  double space was written one way, searched another, and could never be found.
- **Grouping** used to key on the display name alone, so one human under two display names was
  reported as an ambiguity and refused, even though the identity arrays plainly shared a login and
  an email.
"""


def normalize_identity(value: str) -> str:
    """Fold one identity token to its comparable form.

    Whitespace runs collapse as well as strip, so "Raghav  Rao" and "Raghav Rao" are the same
    token. Both ingestion and lookup call this; that is the entire point of it being here.
    """
    return " ".join(value.casefold().split())


def normalize_identities(values: list[str | None]) -> list[str]:
    """Normalise, drop empties, dedupe, sort. The stored form of `author_identities`."""
    return sorted({normalize_identity(value) for value in values if value and value.strip()})


def group_by_shared_identity(identity_sets: list[list[str]]) -> list[list[int]]:
    """Cluster rows that belong to the same person, returning indices per cluster.

    Two rows are the same person when their identity sets share any token, applied transitively:
    `{manav0411, email}` and `{manav goel, manav0411, email}` share a login, so they merge.

    Display names participate in the merge rather than being excluded, which is a deliberate
    trade. Excluding them would split one human who used two logins and two emails — a case the
    suite already covers — and that is the more common shape. The cost is that two genuinely
    different people with an identical display name and no other overlap merge into one. That is
    not a regression: keying on the display name, as this replaced, merged them too. Telling those
    two apart needs evidence this corpus does not carry.
    """
    parent = list(range(len(identity_sets)))

    def find(node: int) -> int:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    def union(left: int, right: int) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[max(left_root, right_root)] = min(left_root, right_root)

    first_seen: dict[str, int] = {}
    for index, identities in enumerate(identity_sets):
        for token in identities:
            if token in first_seen:
                union(first_seen[token], index)
            else:
                first_seen[token] = index

    clusters: dict[int, list[int]] = {}
    for index in range(len(identity_sets)):
        clusters.setdefault(find(index), []).append(index)
    # Ordered by first appearance, so the caller's row order decides cluster order.
    return [clusters[key] for key in sorted(clusters)]
