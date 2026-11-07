import hashlib
import json
import os
import struct
from dataclasses import asdict, dataclass

# Loaded SentenceTransformer models, keyed by model name. Loading a model
# takes seconds; embedding a query must not (the MCP server embeds per call).
_MODEL_CACHE: dict = {}


class EmbeddingUnavailable(RuntimeError):
    """A REQUESTED real provider could not load or encode.

    Audit 2026-10-22 F4: this used to be caught and answered with
    deterministic hash vectors labelled a success, which the hybrid caller then
    scored against a MiniLM index. A missing or corrupt model is a failure the
    caller must see; mock vectors live only behind explicit `provider="mock"`.
    """


class EmbeddingSpaceMismatch(RuntimeError):
    """The vector returned is not in the space the caller required."""


class EmbeddingIdentityCorrupt(EmbeddingSpaceMismatch):
    """An index's stored `embedding_identity` stamp cannot be read.

    A subclass of `EmbeddingSpaceMismatch` on purpose: the space the index holds
    cannot be established, so the CLI and the MCP tools already answer it the way
    they answer any other space they cannot vouch for. A rebuild treats it as
    "nothing in the prior index is reusable" instead.
    """


@dataclass(frozen=True)
class EmbeddingIdentity:
    """What a vector space IS. A model name alone is not an identity: the
    tokenizer length, pooling and normalisation decide whether two vectors are
    comparable. Optional fields are None for indexes stamped before they were
    recorded, and are compared only when both sides have them."""
    provider: str
    model: str
    dimension: int
    max_seq_length: int | None = None
    pooling: str | None = None
    normalize: bool | None = None

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)

    @classmethod
    def from_json(cls, s: str) -> "EmbeddingIdentity":
        try:
            ident = cls(**json.loads(s))
            if not (isinstance(ident.provider, str) and isinstance(ident.model, str)
                    and isinstance(ident.dimension, int) and not isinstance(ident.dimension, bool)):
                raise TypeError("provider/model must be strings and dimension an integer")
        except (ValueError, TypeError) as exc:
            raise EmbeddingIdentityCorrupt(
                f"the index's embedding_identity stamp is unreadable "
                f"({type(exc).__name__}: {exc})"
            ) from exc
        return ident

    def same_space_as(self, other: "EmbeddingIdentity") -> bool:
        """Whether vectors of the two identities are comparable: provider, model
        and dimension always; the optional fields only where BOTH sides record
        them (the contract `require_space` applies to a query vector). Vector
        reuse during a rebuild asks the same question of a stored vector."""
        if (self.provider, self.model, self.dimension) != (other.provider, other.model, other.dimension):
            return False
        return all(
            getattr(self, f) is None or getattr(other, f) is None or getattr(self, f) == getattr(other, f)
            for f in ("max_seq_length", "pooling", "normalize")
        )


MOCK_IDENTITY = EmbeddingIdentity("mock", "deterministic_hash", 384)


def generate_mock_embedding(text: str, dim: int = 384) -> list[float]:
    """
    Deterministic mock embedding based on SHA256.
    Not semantically meaningful, but deterministic for testing.
    """
    h = hashlib.sha256(text.encode('utf-8')).digest()
    vec = []
    for i in range(dim):
        val = struct.unpack('<B', h[i % len(h):i % len(h) + 1])[0]
        vec.append((val / 255.0) * 2.0 - 1.0) # -1 to 1
    return vec


def _load_sentence_transformer(model: str):
    st_model = _MODEL_CACHE.get(model)
    if st_model is None:
        try:
            from sentence_transformers import SentenceTransformer
            st_model = SentenceTransformer(model)
        except Exception as e:
            raise EmbeddingUnavailable(
                f"sentence_transformers model {model!r} could not be loaded: {e}"
            ) from e
        _MODEL_CACHE[model] = st_model
    return st_model


def _st_identity(st_model, model: str, dimension: int) -> EmbeddingIdentity:
    """Read pooling / normalisation / sequence length off the loaded modules;
    anything we cannot read stays None rather than being guessed."""
    pooling = None
    normalize = None
    try:
        for module in st_model:
            name = type(module).__name__
            if name == "Pooling":
                modes = [k for k, v in vars(module).items()
                         if k.startswith("pooling_mode_") and v is True]
                if modes:
                    pooling = modes[0][len("pooling_mode_"):].removesuffix("_tokens")
            elif name == "Normalize":
                normalize = True
        if pooling is not None and normalize is None:
            normalize = False
    except Exception:
        pass
    max_seq = getattr(st_model, "max_seq_length", None)
    return EmbeddingIdentity("sentence_transformers", model, dimension,
                             int(max_seq) if max_seq is not None else None, pooling, normalize)


def get_embedding(text: str, provider: str | None = None, model: str | None = None) -> dict:
    """Embed `text` with the requested provider/model.

    Explicit `provider`/`model` arguments override the environment — callers
    that must stay consistent with an existing index (query-time embedding)
    pass the provider/model recorded IN that index rather than trusting env.

    Provider unset means `mock` (the canonical rebuild refuses that space, so
    the default cannot reach the production index). Provider
    `sentence_transformers` that cannot load raises `EmbeddingUnavailable`;
    it never falls back.
    """
    if provider is None:
        provider = os.environ.get("ZURVAN_EMBED_PROVIDER", "mock").lower()
    if model is None:
        model = os.environ.get("ZURVAN_EMBED_MODEL", "mock_model")

    if provider == "sentence_transformers":
        if model in ("mock_model", "deterministic_hash"):
            # Provider switched but no model named — use the small local default.
            model = "all-MiniLM-L6-v2"
        st_model = _load_sentence_transformer(model)
        try:
            vec = st_model.encode(text).tolist()
        except Exception as e:
            raise EmbeddingUnavailable(
                f"sentence_transformers model {model!r} failed to encode: {e}"
            ) from e
        return {
            "provider": provider,
            "model": model,
            "dimension": len(vec),
            "vector": vec,
            "identity": _st_identity(st_model, model, len(vec)),
        }

    vec = generate_mock_embedding(text)
    return {
        "provider": "mock",
        "model": "deterministic_hash",
        "dimension": len(vec),
        "vector": vec,
        "identity": MOCK_IDENTITY,
    }


def require_space(result: dict, expected: EmbeddingIdentity) -> None:
    """Raise `EmbeddingSpaceMismatch` unless `result` is a vector in
    `expected`'s space. provider/model/dimension are always compared; the
    optional fields only where `expected` pins them and `result` reports them."""
    got = result.get("identity") or EmbeddingIdentity(
        result["provider"], result["model"], result["dimension"])
    for field in ("provider", "model", "dimension"):
        if getattr(got, field) != getattr(expected, field):
            raise EmbeddingSpaceMismatch(
                f"query embedding is {got.provider}/{got.model}/{got.dimension}; "
                f"the index holds {expected.provider}/{expected.model}/{expected.dimension}"
            )
    for field in ("max_seq_length", "pooling", "normalize"):
        e, g = getattr(expected, field), getattr(got, field)
        if e is not None and g is not None and e != g:
            raise EmbeddingSpaceMismatch(
                f"query embedding {field}={g!r} differs from the index's {field}={e!r}"
            )


def read_index_identity(cursor) -> "EmbeddingIdentity | None":
    """The space an index holds, as an `EmbeddingIdentity`, or None for an index
    with no embeddings. Prefers the `embedding_identity` stamp in `index_meta`
    (the full identity, written since F4); an older index yields
    provider/model/dimension from its first embedding row. A stamp that is
    present but unreadable raises `EmbeddingIdentityCorrupt`: falling back to
    the row would silently drop the pinned fields the stamp exists to carry."""
    import sqlite3
    try:
        row = cursor.execute(
            "SELECT value FROM index_meta WHERE key = 'embedding_identity'").fetchone()
    except sqlite3.Error:
        row = None
    if row:
        return EmbeddingIdentity.from_json(row[0])
    try:
        row = cursor.execute("SELECT provider, model, dimension FROM embeddings LIMIT 1").fetchone()
    except sqlite3.Error:
        return None
    return EmbeddingIdentity(row[0], row[1], int(row[2])) if row else None
