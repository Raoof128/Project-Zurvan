"""Zurvan Boundary — provenance-aware egress and contamination control."""
from scripts.boundary import store, d1, d2, d3, policy, sink  # noqa: F401  submodules
from scripts.boundary.store import (                        # noqa: F401
    Config, StaleStateError, StateIntegrityError, init,
)
from scripts.boundary.policy import PolicyError             # noqa: F401
from scripts.boundary.scan import (                         # noqa: F401
    BuildResult, Match, Verdict, build, release, scan, scan_blob, scan_bytes,
)


def config(root):
    """Config for a store, by root. `store.config` takes an open connection."""
    from contextlib import closing
    with closing(store.open_db(root)) as conn:
        return store.read_config(conn)


def register_literal(root, literal, source_class, forbidden_sinks=None, origin_id="manual"):
    from contextlib import closing
    with closing(store.open_db(root)) as conn:
        return d2.register(conn, literal, source_class, origin_id)


def accede_public(root, span, reason):
    from contextlib import closing
    data = span.encode("utf-8") if isinstance(span, str) else span
    with closing(store.open_db(root)) as conn:
        return d3.accede(root, conn, data, provenance=reason)
