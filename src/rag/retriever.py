"""RAG retriever — semantic search over the indexed document chunks.

Returns top-k chunks with source attribution for injection into scoring prompts.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

import faiss
import numpy as np

logger = logging.getLogger(__name__)

INDEX_DIR = Path(__file__).parent / "index"


def retrieve(
    query: str,
    top_k: int = 5,
    index_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Retrieve the top-k most relevant chunks for a given query.

    Returns a list of dicts matching RAGChunk schema:
    {content, source_document, relevance_score}
    """
    index_dir = index_dir or INDEX_DIR

    index_path = index_dir / "vectors.faiss"
    metadata_path = index_dir / "metadata.json"

    if not index_path.exists() or not metadata_path.exists():
        logger.warning("RAG index not found at %s — returning empty results", index_dir)
        return []

    # Load index and metadata
    index = faiss.read_index(str(index_path))
    metadata: list[dict[str, str]] = json.loads(metadata_path.read_text())

    # Compute query embedding
    query_embedding = _compute_query_embedding(query)

    # Normalize for cosine similarity
    faiss.normalize_L2(query_embedding)

    # Search
    k = min(top_k, index.ntotal)
    if k == 0:
        return []

    scores, indices = index.search(query_embedding, k)

    results: list[dict[str, Any]] = []
    for score, idx in zip(scores[0], indices[0], strict=False):
        if idx < 0 or idx >= len(metadata):
            continue
        chunk = metadata[idx]
        results.append(
            {
                "content": chunk["content"],
                "source_document": chunk["source"],
                "relevance_score": float(max(0.0, min(1.0, (score + 1) / 2))),  # normalize to 0-1
            }
        )

    return results


def _compute_query_embedding(query: str) -> np.ndarray:
    """Compute embedding for a single query string."""
    if os.environ.get("TEST_MODE", "").lower() == "true":
        rng = np.random.RandomState(hash(query) % 2**31)
        embedding = rng.randn(1, 384).astype(np.float32)
        return embedding

    try:
        from sentence_transformers import SentenceTransformer

        model = SentenceTransformer("all-MiniLM-L6-v2")
        embedding = model.encode([query], show_progress_bar=False)
        return np.array(embedding, dtype=np.float32)
    except ImportError:
        logger.warning("sentence-transformers not installed, using random query embedding")
        rng = np.random.RandomState(hash(query) % 2**31)
        return rng.randn(1, 384).astype(np.float32)
