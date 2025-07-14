import asyncio
from sentence_transformers import SentenceTransformer
import numpy as np

_model = None
_model_lock = asyncio.Lock()


async def get_model() -> SentenceTransformer:
    """Load and return the SentenceTransformer model singleton asynchronously."""
    global _model
    async with _model_lock:
        if _model is None:
            loop = asyncio.get_running_loop()
            _model = await loop.run_in_executor(None, SentenceTransformer, 'all-MiniLM-L6-v2')
    return _model


async def generate_embedding(text: str) -> list[float]:
    model = await get_model()
    loop = asyncio.get_running_loop()
    embedding = await loop.run_in_executor(None, lambda: model.encode(text, convert_to_numpy=True))
    norm_embedding = embedding / np.linalg.norm(embedding)
    return norm_embedding.tolist()
