from typing import List

def get_rank(expected_paths: List[str], retrieved_paths: List[str]) -> int:
    """Returns the 1-based rank of the first expected path found in retrieved paths. Returns 0 if none found."""
    for i, path in enumerate(retrieved_paths, 1):
        if any(expected in path for expected in expected_paths):
            return i
    return 0

def calculate_top_k_hit(expected_paths: List[str], retrieved_paths: List[str], k: int) -> bool:
    """Returns True if any expected path is within the top K retrieved paths."""
    rank = get_rank(expected_paths, retrieved_paths)
    return 0 < rank <= k

def calculate_reciprocal_rank(expected_paths: List[str], retrieved_paths: List[str]) -> float:
    """Returns the Reciprocal Rank (1/rank) for the query."""
    rank = get_rank(expected_paths, retrieved_paths)
    return 1.0 / rank if rank > 0 else 0.0

def calculate_mean_reciprocal_rank(reciprocal_ranks: List[float]) -> float:
    """Returns the Mean Reciprocal Rank (MRR) for a list of RRs."""
    if not reciprocal_ranks:
        return 0.0
    return sum(reciprocal_ranks) / len(reciprocal_ranks)

def calculate_accuracy(hits: int, total: int) -> float:
    """Returns hit rate accuracy."""
    if total == 0:
        return 0.0
    return hits / total
