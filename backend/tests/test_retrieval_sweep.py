"""The sweep's own logic, separate from what it measures.

The numbers a sweep prints are only worth reading if the grid is what it claims to be and the
embedding cache is actually being hit, so those are what these assert.
"""

import pytest

from app.services.retrieval import CANDIDATE_DEPTH, LEXICAL_WEIGHT, RRF_K, VECTOR_WEIGHT
from evals.retrieval_sweep import (
    CANDIDATE_DEPTH_GRID,
    LEXICAL_WEIGHT_GRID,
    RRF_K_GRID,
    CachedEmbedder,
    Configuration,
    build_grid,
)


def test_the_grid_is_the_full_product() -> None:
    grid = build_grid()

    assert len(grid) == len(RRF_K_GRID) * len(CANDIDATE_DEPTH_GRID) * len(LEXICAL_WEIGHT_GRID)
    assert len({(c.rrf_k, c.candidate_depth, c.lexical_weight) for c in grid}) == len(grid)


def test_the_shipped_configuration_is_always_present() -> None:
    """It is the reference row. A grid edit must not silently remove the thing being compared to."""
    shipped = [c for c in build_grid() if c.is_shipped]

    assert len(shipped) == 1
    assert shipped[0].rrf_k == RRF_K
    assert shipped[0].candidate_depth == CANDIDATE_DEPTH
    assert shipped[0].lexical_weight == LEXICAL_WEIGHT
    assert shipped[0].vector_weight == VECTOR_WEIGHT


def test_vector_weight_is_fixed_across_the_grid() -> None:
    """Only the ratio between the weights changes ordering; scaling both is a no-op."""
    assert {c.vector_weight for c in build_grid()} == {1.0}


def test_a_configuration_maps_to_hybrid_retrieve_keywords() -> None:
    kwargs = Configuration(rrf_k=30, candidate_depth=60, lexical_weight=0.5).as_kwargs()

    assert kwargs == {
        "rrf_k": 30,
        "candidate_depth": 60,
        "lexical_weight": 0.5,
        "vector_weight": 1.0,
    }


async def test_the_cache_replays_vectors_without_embedding_again() -> None:
    embedder = CachedEmbedder({"why did we choose bcrypt?": [0.1, 0.2]})

    first = await embedder.embed(["why did we choose bcrypt?"])
    second = await embedder.embed(["why did we choose bcrypt?"])

    assert first == second == [[0.1, 0.2]]
    assert embedder.calls == 2  # called twice, embedded zero times


async def test_a_cache_miss_raises_rather_than_falling_back() -> None:
    """A silent miss would reintroduce the per-configuration embedding cost invisibly."""
    embedder = CachedEmbedder({})

    with pytest.raises(KeyError, match="no cached embedding"):
        await embedder.embed(["unseen query"])
