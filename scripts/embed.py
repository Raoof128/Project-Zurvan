import os
import hashlib
import struct

# Loaded SentenceTransformer models, keyed by model name. Loading a model
# takes seconds; embedding a query must not (the MCP server embeds per call).
_MODEL_CACHE: dict = {}


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


def get_embedding(text: str, provider: str | None = None, model: str | None = None) -> dict:
    """Embed `text` with the requested provider/model.

    Explicit `provider`/`model` arguments override the environment — callers
    that must stay consistent with an existing index (query-time embedding)
    pass the provider/model recorded IN that index rather than trusting env.
    """
    if provider is None:
        provider = os.environ.get("ZURVAN_EMBED_PROVIDER", "mock").lower()
    if model is None:
        model = os.environ.get("ZURVAN_EMBED_MODEL", "mock_model")

    if provider == "sentence_transformers":
        try:
            if model in ("mock_model", "deterministic_hash"):
                # Provider switched but no model named — use the small local default.
                model = "all-MiniLM-L6-v2"
            st_model = _MODEL_CACHE.get(model)
            if st_model is None:
                from sentence_transformers import SentenceTransformer
                st_model = SentenceTransformer(model)
                _MODEL_CACHE[model] = st_model
            vec = st_model.encode(text).tolist()
            return {
                "provider": provider,
                "model": model,
                "dimension": len(vec),
                "vector": vec
            }
        except Exception as e:
            print(f"Warning: Failed to load sentence_transformers '{model}' ({e}). Falling back to mock embeddings.")
            provider = "mock"

    # Default to mock
    vec = generate_mock_embedding(text)
    return {
        "provider": "mock",
        "model": "deterministic_hash",
        "dimension": len(vec),
        "vector": vec
    }
