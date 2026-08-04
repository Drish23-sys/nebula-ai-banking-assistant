"""
backend/rag/ingest.py

Ingests Banking Policy FAQ markdown files from data/knowledge_base/ into
ChromaDB, using BAAI/bge-small-en-v1.5 (384-dim, ONNX, CPU) as the
embedding model (PRD §2.4).

Chunking strategy: split on markdown "## " section headers, since each
policy document is already organized into self-contained numbered
sections (e.g. "Section 4.2"). This keeps each chunk semantically
coherent for retrieval and lets us cite "Section X.Y" directly, matching
the citation style used in the PRD's sample transcripts (§6).

Run directly to (re)build the collection from scratch:
    python backend/rag/ingest.py
"""

import glob
import os
import re
import sys

import chromadb
from fastembed import TextEmbedding

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from backend.config import (  # noqa: E402
    CHROMA_COLLECTION_NAME,
    CHROMA_PERSIST_DIR,
    EMBEDDING_MODEL_NAME,
    KNOWLEDGE_BASE_DIR,
)

# fastembed's bundled BAAI/bge-small-en-v1.5 ONNX model. Matches PRD §2.4
# (384-dim, quantized ONNX runtime, CPU-bound regardless of GPU vendor).
FASTEMBED_MODEL_NAME = "BAAI/bge-small-en-v1.5"


def load_and_chunk_documents(kb_dir: str) -> list[dict]:
    """
    Reads every .md file in kb_dir and splits it into chunks on '## '
    section headers. Returns a list of dicts: {id, text, source, section}.
    """
    chunks = []
    md_files = sorted(glob.glob(os.path.join(kb_dir, "*.md")))

    for filepath in md_files:
        source_name = os.path.basename(filepath)
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()

        # Split on "## " headers, keeping the header text with its section.
        sections = re.split(r"\n(?=## )", content)

        for i, section in enumerate(sections):
            section = section.strip()
            if not section:
                continue
            header_match = re.match(r"#{1,2}\s*(.+)", section)
            section_title = header_match.group(1).strip() if header_match else f"Chunk {i}"

            # Skip the top-level H1 title chunk (it's just the document
            # heading with no body text — nothing useful to retrieve).
            body_after_header = re.sub(r"^#{1,2}\s*.+\n?", "", section, count=1).strip()
            if not body_after_header:
                continue

            chunks.append(
                {
                    "id": f"{source_name}::{i}",
                    "text": section,
                    "source": source_name,
                    "section": section_title,
                }
            )

    return chunks


def build_collection(
    kb_dir: str = KNOWLEDGE_BASE_DIR,
    persist_dir: str = CHROMA_PERSIST_DIR,
    collection_name: str = CHROMA_COLLECTION_NAME,
    reset: bool = True,
) -> chromadb.Collection:
    os.makedirs(persist_dir, exist_ok=True)

    client = chromadb.PersistentClient(path=persist_dir)

    if reset:
        try:
            client.delete_collection(collection_name)
        except Exception:
            pass

    collection = client.get_or_create_collection(
        name=collection_name,
        metadata={"hnsw:space": "cosine", "embedding_model": EMBEDDING_MODEL_NAME},
    )

    chunks = load_and_chunk_documents(kb_dir)
    if not chunks:
        print(f"No markdown files found in {kb_dir} — nothing to ingest.")
        return collection

    print(f"Loaded {len(chunks)} chunks from {kb_dir}")

    embedder = TextEmbedding(model_name=FASTEMBED_MODEL_NAME)
    texts = [c["text"] for c in chunks]
    embeddings = list(embedder.embed(texts))

    collection.add(
        ids=[c["id"] for c in chunks],
        documents=texts,
        embeddings=[e.tolist() for e in embeddings],
        metadatas=[{"source": c["source"], "section": c["section"]} for c in chunks],
    )

    print(f"Ingested {len(chunks)} chunks into ChromaDB collection '{collection_name}' "
          f"at {persist_dir}")
    return collection


def query_collection(query: str, top_k: int = 3, persist_dir: str = CHROMA_PERSIST_DIR,
                      collection_name: str = CHROMA_COLLECTION_NAME):
    """Quick sanity-check retrieval helper — mirrors what rag_node will do in Day 2."""
    client = chromadb.PersistentClient(path=persist_dir)
    collection = client.get_collection(collection_name)

    embedder = TextEmbedding(model_name=FASTEMBED_MODEL_NAME)
    query_embedding = list(embedder.embed([query]))[0].tolist()

    results = collection.query(query_embeddings=[query_embedding], n_results=top_k)
    return results


if __name__ == "__main__":
    build_collection()

    print("\n--- Sanity check retrieval ---")
    test_query = "What is the fee for an international wire transfer over $10,000?"
    results = query_collection(test_query)
    print(f"Query: {test_query}\n")
    for doc, meta, dist in zip(
        results["documents"][0], results["metadatas"][0], results["distances"][0]
    ):
        similarity = 1 - dist  # cosine distance -> similarity
        print(f"[{meta['source']} / {meta['section']}] (similarity={similarity:.3f})")
        print(f"  {doc[:150].strip()}...\n")
