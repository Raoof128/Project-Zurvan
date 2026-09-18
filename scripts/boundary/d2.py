"""D2 — salient-literal registry.

Named "literal", not "identifier": a complete evaluation item is neither short
nor an identifier, and it belongs in this detector because no span rule can reach
a string below T.

Cleartext lives ONLY here, in the local store (Global Constraint 10/13). Tracked
metadata carries the hash. A SHA-256 cannot locate an arbitrary case-insensitive
substring, so a searchable representation must exist locally — that is a
deliberate trade, not an oversight.
"""
import hashlib
import hmac
import secrets


def _registry_key(conn) -> bytes:
    """Per-store HMAC key, generated once at first use and never exported."""
    row = conn.execute("SELECT value FROM meta WHERE key='literal_hmac_key'").fetchone()
    if row is None:
        key = secrets.token_hex(32)
        conn.execute("INSERT INTO meta VALUES ('literal_hmac_key', ?)", (key,))
        conn.commit()
    else:
        key = row[0]
    return bytes.fromhex(key)


def _fold(s: str) -> str:
    # Global Constraint 9. On 2026-09-15 a redaction pass covering lower and
    # upper case missed a title-cased occurrence, which survived into a later
    # commit.
    #
    # casefold() is required rather than lower() because `"ß".lower()` is still
    # `"ß"` while `"ß".casefold()` is `"ss"` — so lower() cannot match "Straße"
    # against "STRASSE". (An earlier comment here also claimed lower() fails on
    # "SS"; it does not — `"SS".lower()` is `"ss"`. The asymmetry is entirely on
    # the ß side.)
    return s.casefold()


def register(conn, literal: str, source_class: str, origin_id: str) -> str:
    """Register one literal FOR ONE ORIGIN. Returns the opaque literal_id.

    The previous draft made the casefolded literal the primary key with INSERT OR
    REPLACE, so registering the same folded string for a second origin DELETED
    the first. Two projects sharing a codename, or the same string protected as
    both PRIVATE and INSTRUMENT, silently lost one registration — and with it,
    one class of block.

    `literal_id` is an HMAC over (literal, origin), not a bare SHA-256 of the
    literal: a 5-byte project name has far too little entropy to survive a bare
    digest appearing in a Verdict that reaches a CI log.
    """
    if not literal or not literal.strip():
        raise ValueError("refusing to register an empty literal: it matches everything")
    if source_class not in ("PRIVATE", "INSTRUMENT"):
        raise ValueError(f"unknown source class {source_class!r}")
    literal_id = hmac.new(_registry_key(conn),
                          f"{origin_id}\x00{literal}".encode("utf-8"),
                          hashlib.sha256).hexdigest()
    conn.execute("INSERT OR REPLACE INTO literals VALUES (?,?,?,?)",
                 (literal_id, _fold(literal), source_class, origin_id))
    conn.commit()
    return literal_id


def load_literals(conn) -> tuple:
    """The whole registry, read once.

    A scan session holds this for a whole batch rather than re-reading the table
    per candidate. The registry is small and immutable for the life of a scan;
    the folded candidate text is the part that actually varies.
    """
    return tuple(conn.execute(
        "SELECT literal_id, literal_casefold, source_class, origin_id FROM literals"))


def match_loaded(literals, text: str):
    """[(literal_id, source_class, origin_id)] — every matching origin, not one."""
    folded = _fold(text)
    return [(literal_id, source_class, origin_id)
            for literal_id, lit, source_class, origin_id in literals
            if lit in folded]


def match(conn, text: str):
    return match_loaded(load_literals(conn), text)
