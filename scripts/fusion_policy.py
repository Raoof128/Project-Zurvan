"""The hybrid fusion policy — ONE object the scorer applies and the trace
records.

Audit 2026-10-22 F5: `hybrid_search` fused at keyword 0.2 / semantic 0.8 while
`context_export` wrote `{"fts": 0.6, "embedding": 0.4}` into every
`retrieval.fusion` event, so a score replayed from a trace was wrong (1.0/0.5
scored 0.6; the trace said 0.8). Two copies of a number drift; this is the one
copy. Changing it is a RANKING change and needs a before/after `eval_search`
run in CHANGELOG.md — bump `policy_id` with it.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class FusionPolicy:
    policy_id: str
    keyword_weight: float
    min_score: float

    def score(self, keyword: float, semantic: float) -> float:
        return self.keyword_weight * keyword + (1.0 - self.keyword_weight) * semantic

    def as_payload(self) -> dict:
        return {
            "fusion": "weighted_sum",
            "policy_id": self.policy_id,
            "weights": {"fts": self.keyword_weight, "embedding": 1.0 - self.keyword_weight},
            "min_score": self.min_score,
        }


# Values measured on the 50-query gold set (see the comment block in
# scripts/hybrid_search.py for the sweep).
CURRENT = FusionPolicy("weighted_sum_v1_kw0.2", 0.2, 0.2)
