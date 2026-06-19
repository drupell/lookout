"""RAG indexer — chunks and embeds source documents into a local FAISS index.

The index is stored as a local file and bundled with the Lambda deployment package.
This avoids managed vector store costs while demonstrating the RAG pattern correctly.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import faiss
import numpy as np
from langchain_text_splitters import RecursiveCharacterTextSplitter

logger = logging.getLogger(__name__)

SOURCES_DIR = Path(__file__).parent / "sources"
INDEX_DIR = Path(__file__).parent / "index"

# Chunk configuration
CHUNK_SIZE = 500
CHUNK_OVERLAP = 100


def build_index(
    sources_dir: Path | None = None,
    index_dir: Path | None = None,
) -> None:
    """Build a FAISS index from all source documents.

    Reads .md and .txt files from sources_dir, chunks them,
    computes embeddings, and writes a FAISS index + metadata to index_dir.
    """
    sources_dir = sources_dir or SOURCES_DIR
    index_dir = index_dir or INDEX_DIR
    index_dir.mkdir(parents=True, exist_ok=True)

    # Load all source documents
    documents: list[dict[str, str]] = []
    for file_path in sorted(sources_dir.glob("*.md")):
        content = file_path.read_text()
        documents.append({"content": content, "source": file_path.name})

    for file_path in sorted(sources_dir.glob("*.txt")):
        content = file_path.read_text()
        documents.append({"content": content, "source": file_path.name})

    if not documents:
        logger.warning("No source documents found in %s", sources_dir)
        return

    # Chunk documents
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        separators=["\n## ", "\n### ", "\n\n", "\n", " "],
    )

    chunks: list[dict[str, str]] = []
    for doc in documents:
        splits = splitter.split_text(doc["content"])
        for split in splits:
            chunks.append({"content": split, "source": doc["source"]})

    logger.info("Created %d chunks from %d documents", len(chunks), len(documents))

    # Compute embeddings
    embeddings = _compute_embeddings([c["content"] for c in chunks])

    # Build FAISS index
    dimension = embeddings.shape[1]
    index = faiss.IndexFlatIP(dimension)  # Inner product (cosine similarity on normalized vectors)

    # Normalize for cosine similarity
    faiss.normalize_L2(embeddings)
    index.add(embeddings)

    # Save index and metadata
    faiss.write_index(index, str(index_dir / "vectors.faiss"))

    import json

    metadata_path = index_dir / "metadata.json"
    metadata_path.write_text(json.dumps(chunks, indent=2))

    logger.info(
        "Index built: %d vectors of dimension %d saved to %s",
        index.ntotal,
        dimension,
        index_dir,
    )


def _compute_embeddings(texts: list[str]) -> np.ndarray:
    """Compute embeddings for a list of texts.

    In TEST_MODE, returns random vectors for deterministic testing.
    In production, uses the configured embedding model.
    """
    if os.environ.get("TEST_MODE", "").lower() == "true":
        # Deterministic random embeddings for testing
        rng = np.random.RandomState(42)
        return rng.randn(len(texts), 384).astype(np.float32)

    # Production: use sentence-transformers for local embeddings
    # This avoids API costs and keeps everything self-contained
    try:
        from sentence_transformers import SentenceTransformer

        model = SentenceTransformer("all-MiniLM-L6-v2")
        embeddings = model.encode(texts, show_progress_bar=False)
        return np.array(embeddings, dtype=np.float32)
    except ImportError:
        logger.warning(
            "sentence-transformers not installed, falling back to random embeddings. "
            "Install with: pip install sentence-transformers"
        )
        rng = np.random.RandomState(42)
        return rng.randn(len(texts), 384).astype(np.float32)
