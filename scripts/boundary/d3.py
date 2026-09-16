"""D3 — known-public origin resolver.

Subtraction happens at the MATCHED SPAN level, never by exempting a whole source
(Global Constraint 6). Exempting a source permits laundering: drop protected text
into a source marked public and everything it contains becomes quotable.

Measured justification (spec §8): against 11.16 MB of real sources and 1.69 MB of
tracked code, 98 spans still collide at T=128, and the samples are the project's
own ingested documentation. Without D3, D1's first real run blocks a commit for
quoting the repository's own README.
"""
import hashlib

# Global Constraint 6: which (class, sink) pairs may ever be exempted by public
# origin. INSTRUMENT -> RETRIEVAL_CORPUS is deliberately absent.
# Only pairs the policy actually blocks can be exempted; listing others implies
# a check that never fires. INSTRUMENT is absent by design (Global Constraint 6):
# an evaluation item whose words also occur publicly still matches its own answer.
_EXEMPTIBLE = {("PRIVATE", "PUBLIC_ARTIFACT")}


def _digest(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def accede(root, conn, document: bytes, provenance: str) -> str:
    """Admit one approved public DOCUMENT, storing its bytes as a blob.

    The previous draft accessioned an exact span digest, which made this an
    exact-span whitelist rather than a public corpus: an approved public document
    did NOT exempt a legitimate substring of itself, so a correctly-quoted public
    sentence still blocked. Reproduced before rewriting.

    `provenance` is metadata, not a trust boundary — "not empty" proves nothing
    about approval. The caller is responsible for only acceding reviewed material
    (Global Constraint 7); this records WHAT was acceded and WHY so that a later
    audit can challenge it.
    """
    if not provenance or not provenance.strip():
        raise ValueError(
            "accession provenance is required; the public corpus is never seeded "
            "from 'everything currently tracked' (Global Constraint 7)")
    from scripts.boundary import store
    blob_digest = store.put_blob(root, document)
    accession_id = _digest(f"{blob_digest}\x00{provenance}".encode())
    conn.execute("INSERT OR REPLACE INTO public_origins VALUES (?,?,?)",
                 (accession_id, blob_digest, provenance))
    conn.commit()
    return accession_id


def is_exempt(root, conn, span_bytes: bytes, source_class: str, sink: str) -> bool:
    """True when this exact span occurs INSIDE an approved public document.

    Containment, not digest equality. A public document exempts its own
    substrings; it does not exempt anything longer that merely starts with one
    (the partial-overlap escape, which the residual test pins).
    """
    if (source_class, sink) not in _EXEMPTIBLE:
        return False
    if len(span_bytes) == 0:
        return False
    for doc in _public_docs(root, conn):
        if span_bytes in doc:
            return True
    return False


_DOC_CACHE: dict = {}


def _public_docs(root, conn):
    """Approved public documents, loaded once per (root, corpus-state).

    Previously re-read every blob from disk for EVERY candidate span, which is
    linear in corpus size per span. Keyed on the set of accessioned digests so a
    newly acceded document invalidates the cache immediately.
    """
    from scripts.boundary import store
    digests = tuple(sorted(r[0] for r in conn.execute("SELECT blob_digest FROM public_origins")))
    key = (str(root), digests)
    if key not in _DOC_CACHE:
        _DOC_CACHE.clear()          # bounded: one corpus state at a time
        _DOC_CACHE[key] = [store.get_blob(root, d) for d in digests]
    return _DOC_CACHE[key]
