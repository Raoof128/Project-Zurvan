import pytest

from scripts import failure_anatomy as fa


def test_expected_doc_absent_from_every_retriever_is_a_candidate_miss():
    # Nothing retrieved it at all. A reranker only reorders what was retrieved,
    # so it CANNOT fix this by construction.
    c = fa.classify(fts_rank=None, semantic_rank=None, fused_rank=None,
                    in_top50=False, indexed=True)
    assert c == fa.CANDIDATE_MISS
    assert fa.is_rerankable(c) is False


def test_retrieved_into_the_pool_but_ranked_below_three_is_a_fusion_miss():
    # The document IS in the candidate pool; fusion put it out of reach.
    # This is exactly what a reranker exists to fix.
    c = fa.classify(fts_rank=40, semantic_rank=12, fused_rank=9,
                    in_top50=True, indexed=True)
    assert c == fa.FUSION_RANKING_MISS
    assert fa.is_rerankable(c) is True


def test_found_by_one_retriever_but_demoted_out_of_the_pool_is_a_fusion_miss():
    # Lexical found it at rank 5, yet the fused list drops it past 50 —
    # the fusion weighting is the culprit, and it is rerankable if the pool
    # is widened.
    c = fa.classify(fts_rank=5, semantic_rank=None, fused_rank=None,
                    in_top50=False, indexed=True)
    assert c == fa.FUSION_RANKING_MISS


def test_an_unindexed_expected_document_is_an_index_coverage_miss():
    # The gold expected README.md / AGENTS.md / CLAUDE.md — real, important
    # project documents. The gold was RIGHT; the corpus was incomplete. Calling
    # that a "gold annotation issue" blames the evaluation set for a corpus bug,
    # so coverage gets its own class.
    c = fa.classify(fts_rank=None, semantic_rank=None, fused_rank=None,
                    in_top50=False, indexed=False)
    assert c == fa.INDEX_COVERAGE_MISS
    assert fa.is_rerankable(c) is False, "no reranker can rank an absent document"


def test_gold_annotation_issue_remains_a_distinct_category():
    # Still needed for a gold entry that is genuinely wrong (e.g. names a path
    # that should not exist at all). Kept separate from coverage.
    assert fa.GOLD_ANNOTATION_ISSUE != fa.INDEX_COVERAGE_MISS
    assert fa.is_rerankable(fa.GOLD_ANNOTATION_ISSUE) is False


def test_a_passing_query_is_not_a_failure():
    assert fa.classify(fts_rank=1, semantic_rank=1, fused_rank=1,
                       in_top50=True, indexed=True) == fa.NOT_A_FAILURE


def test_rerankability_rate_and_the_decision_rule_fixed_in_advance():
    # Thresholds are fixed BEFORE measuring so a marginal result cannot be
    # rationalised into a build decision afterwards.
    assert fa.decision(rerankable=8, total=14) == fa.BUILD_2B
    assert fa.decision(rerankable=10, total=14) == fa.BUILD_2B
    assert fa.decision(rerankable=4, total=14) == fa.DROP_2B
    assert fa.decision(rerankable=0, total=14) == fa.DROP_2B
    assert fa.decision(rerankable=5, total=14) == fa.DEFER_2B
    assert fa.decision(rerankable=7, total=14) == fa.DEFER_2B


def test_rate_is_reported_against_the_actual_failure_count():
    assert fa.rerankability_rate([fa.FUSION_RANKING_MISS, fa.CANDIDATE_MISS,
                                  fa.FUSION_RANKING_MISS]) == (2, 3)
