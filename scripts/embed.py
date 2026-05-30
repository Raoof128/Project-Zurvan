import os
import hashlib
import struct

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

def get_embedding(text: str) -> dict:
    provider = os.environ.get("ZURVAN_EMBED_PROVIDER", "mock").lower()
    model_name = os.environ.get("ZURVAN_EMBED_MODEL", "mock_model")
    
    if provider == "sentence_transformers":
        try:
            from sentence_transformers import SentenceTransformer
            model = SentenceTransformer(model_name)
            vec = model.encode(text).tolist()
            dim = len(vec)
            return {
                "provider": provider,
                "model": model_name,
                "dimension": dim,
                "vector": vec
            }
        except Exception as e:
            print(f"Warning: Failed to load sentence_transformers '{model_name}' ({e}). Falling back to mock embeddings.")
            provider = "mock"
            
    # Default to mock
    vec = generate_mock_embedding(text)
    return {
        "provider": "mock",
        "model": "deterministic_hash",
        "dimension": len(vec),
        "vector": vec
    }
