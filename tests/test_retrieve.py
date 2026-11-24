"""Offline tests for reciprocal rank fusion and the page collapse."""

from app.retrieve import RRF_K, fuse

A, B = "0000000000-26-000001", "0000000000-26-000002"


def test_fusion_rewards_chunks_found_by_both_searches() -> None:
    vector = [(1, A, 5), (2, A, 6), (3, B, 1)]
    fts = [(3, B, 1), (4, A, 7)]
    hits = fuse([vector, fts], k=10)
    assert [(h.accession, h.page) for h in hits][0] == (B, 1)  # rank 3 + rank 1 beats rank 1 alone
    assert hits[0].score == 1 / (RRF_K + 3) + 1 / (RRF_K + 1)


def test_pages_collapse_to_best_chunk_and_k_limits() -> None:
    vector = [(1, A, 5), (2, A, 5), (3, A, 6), (4, A, 7)]
    hits = fuse([vector], k=2)
    assert [(h.page, h.chunk_id) for h in hits] == [(5, 1), (6, 3)]


def test_empty_rankings() -> None:
    assert fuse([[], []]) == []


def test_pages_found_by_several_queries_outrank_a_single_first_place() -> None:
    from app.retrieve import Hit, merge_page_rankings

    q1 = [Hit(A, 1, 0.0, 1), Hit(A, 2, 0.0, 2)]
    q2 = [Hit(A, 3, 0.0, 3), Hit(A, 2, 0.0, 4)]
    q3 = [Hit(B, 9, 0.0, 5), Hit(A, 2, 0.0, 6)]
    assert merge_page_rankings([q1, q2, q3], n=3) == [(A, 2), (A, 1), (A, 3)]
    assert merge_page_rankings([q1, q2, q3], n=1) == [(A, 2)]
    assert merge_page_rankings([]) == []
