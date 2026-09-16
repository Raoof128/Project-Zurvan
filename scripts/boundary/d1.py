"""D1 — long-span verbatim matcher.

Winnowed k-gram fingerprints locate CANDIDATES; the verdict always comes from a
byte-exact comparison against the original source (Global Constraint 5). Hashes
never decide, so a collision cannot produce a false block.

Reused primitive, not a new invention: Project Simurgh's Stage 4X/4Y (VDR,
Verifiable Document Residue) already builds residue detection over a corpus. VDR
signs "coverage geometry, not leakage detection" and "the Map Is Not a Verdict";
this module points the same primitive at an egress sink and supplies the verdict
VDR declines to render.
"""
BASE = 257
MOD = (1 << 61) - 1


def kgram_hashes(data: bytes, k: int) -> list[int]:
    """Rabin-Karp rolling hashes of every k-gram. O(len(data))."""
    if len(data) < k:
        return []
    high = pow(BASE, k - 1, MOD)
    h = 0
    for b in data[:k]:
        h = (h * BASE + b) % MOD
    out = [h]
    for i in range(k, len(data)):
        h = ((h - data[i - k] * high) * BASE + data[i]) % MOD
        out.append(h)
    return out


def winnow(hashes: list[int], w: int) -> list[tuple[int, int]]:
    """Schleimer winnowing: in each window of w hashes select the minimum.

    Ties break on the RIGHTMOST occurrence, consistently, so the same text
    selects the same fingerprints wherever it appears.

    O(n) via a monotonic deque. The first implementation sliced, min'd and
    reversed every window — O(n*w) — which took over four minutes on 300 real
    source pages and would have made `build` unusable at corpus scale.
    `test_deque_winnow_matches_the_naive_definition` pins the output to the naive
    definition, because an optimisation that changes which fingerprints are
    selected changes every stored fingerprint.
    """
    from collections import deque

    n = len(hashes)
    if n < w:
        return []
    out, prev = [], -1
    dq = deque()                      # indices, hashes non-decreasing left->right
    for i, h in enumerate(hashes):
        # Strict `>` keeps the RIGHTMOST minimum: an equal value evicts the older
        # one, so the survivor is always the latest occurrence.
        while dq and hashes[dq[-1]] >= h:
            dq.pop()
        dq.append(i)
        if dq[0] <= i - w:
            dq.popleft()
        if i >= w - 1:
            idx = dq[0]
            if idx != prev:
                out.append((hashes[idx], idx))
                prev = idx
    return out


def index_source(conn, origin_id: str, data: bytes, cfg) -> int:
    """Index one origin's blob. The origin row must already exist (FK)."""
    rows = [(h.to_bytes(8, "big"), origin_id, off)
            for h, off in winnow(kgram_hashes(data, cfg.k), cfg.w)]
    conn.executemany("INSERT INTO fingerprints VALUES (?,?,?)", rows)
    return len(rows)


def candidates(conn, data: bytes, cfg):
    """Every posting for every matching fingerprint — Global Constraint 4.

    Returning only the first posting is the defect this exists to avoid: a span
    duplicated across a public and a protected source would resolve to the public
    origin and the leak would read clean.
    """
    out = []
    for h, cand_off in winnow(kgram_hashes(data, cfg.k), cfg.w):
        for origin_id, src_off in conn.execute(
            "SELECT origin_id, offset FROM fingerprints WHERE fingerprint = ?",
            (h.to_bytes(8, "big"),),
        ):
            out.append((origin_id, src_off, cand_off))
    return out


def confirm(source_bytes: bytes, src_offset: int,
            data: bytes, cand_offset: int) -> tuple[int, int]:
    """Byte-exact confirmation. Returns (match_start_in_data, match_length).

    Expands the anchor both ways for as long as the bytes agree, then reports the
    true span — the fingerprint only located it.
    """
    lo_s, lo_d = src_offset, cand_offset
    while lo_s > 0 and lo_d > 0 and source_bytes[lo_s - 1] == data[lo_d - 1]:
        lo_s -= 1
        lo_d -= 1
    hi_s, hi_d = src_offset, cand_offset
    while (hi_s < len(source_bytes) and hi_d < len(data)
           and source_bytes[hi_s] == data[hi_d]):
        hi_s += 1
        hi_d += 1
    return lo_d, hi_d - lo_d
