"""Versioned dense indexes for new queries against the stored corpus.

Index creation is an explicit command, never an import-time/network side effect.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np

from civic_link.common import connect

INDEX_VERSION = 1
PASSAGE_FORMAT = "chunk-title-newline-text-v1"


def passage(chunk: dict) -> str:
    return chunk["title"] + "\n" + chunk["text"]


def load_corpus(db_path: str | Path) -> list[dict]:
    """Read source-backed chunks without creating or modifying a database."""
    with closing(connect(db_path, readonly=True)) as db:
        rows = db.execute(
            "SELECT c.chunk_id AS id, c.document_id, c.title, c.text, "
            "d.source_url AS source FROM chunks c "
            "JOIN documents d ON d.document_id=c.document_id ORDER BY c.chunk_id"
        ).fetchall()
    chunks = [dict(row) for row in rows]
    if not chunks:
        raise ValueError("corpus contains no chunks")
    if len({row["id"] for row in chunks}) != len(chunks):
        raise ValueError("corpus contains duplicate chunk IDs")
    for row in chunks:
        if any(not isinstance(row[key], str) or not row[key].strip()
               for key in ("id", "document_id", "title", "text", "source")):
            raise ValueError("corpus chunk is missing text or provenance")
    return chunks


def corpus_fingerprint(chunks: list[dict]) -> str:
    payload = [{key: row[key] for key in ("id", "document_id", "title", "text", "source")}
               for row in chunks]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def normalized_vectors(value: Any, *, rows: int | None = None) -> np.ndarray:
    array = np.asarray(value, dtype=np.float32)
    if array.ndim != 2 or not all(array.shape) or (rows is not None and len(array) != rows):
        raise ValueError("invalid embedding matrix shape")
    if not np.isfinite(array).all():
        raise ValueError("embedding matrix has non-finite values")
    norms = np.linalg.norm(array, axis=1, keepdims=True)
    if not np.isfinite(norms).all() or (norms <= 0).any():
        raise ValueError("embedding matrix contains invalid or zero vectors")
    return np.ascontiguousarray(array / norms, dtype=np.float32)


@dataclass(frozen=True)
class DenseIndex:
    chunk_ids: tuple[str, ...]
    vectors: np.ndarray
    metadata: dict

    @classmethod
    def load(cls, path: str | Path, chunks: list[dict], embedder: Any) -> "DenseIndex":
        with np.load(path, allow_pickle=False) as archive:
            if set(archive.files) != {"chunk_ids", "vectors", "metadata"}:
                raise ValueError("unexpected dense index contents")
            ids = tuple(str(value) for value in archive["chunk_ids"].tolist())
            vectors = np.asarray(archive["vectors"], dtype=np.float32)
            metadata = json.loads(str(archive["metadata"].item()))
        expected = {
            "version": INDEX_VERSION,
            "passage_format": PASSAGE_FORMAT,
            "corpus_fingerprint": corpus_fingerprint(chunks),
            "embedding_model": embedder.model,
            "encoder_fingerprint": embedder.fingerprint,
        }
        if any(metadata.get(key) != value for key, value in expected.items()):
            raise ValueError("dense index corpus/model/encoder metadata does not match; rebuild explicitly")
        if ids != tuple(row["id"] for row in chunks):
            raise ValueError("dense index chunk IDs do not match corpus order")
        if vectors.ndim != 2 or vectors.shape != (len(ids), metadata.get("dimension")):
            raise ValueError("dense index vector dimensions do not match metadata")
        if not np.isfinite(vectors).all() or not np.allclose(np.linalg.norm(vectors, axis=1), 1, atol=1e-5):
            raise ValueError("dense index contains invalid/non-unit vectors")
        if hashlib.sha256(vectors.tobytes()).hexdigest() != metadata.get("vectors_sha256"):
            raise ValueError("dense index vector checksum mismatch")
        return cls(ids, vectors, metadata)


def build_dense_index(db_path: str | Path, output: str | Path, embedder: Any,
                      *, batch_size: int = 16, progress=None) -> dict:
    """Embed all chunks with one configured encoder, refusing output overwrite."""
    if type(batch_size) is not int or batch_size < 1:
        raise ValueError("batch_size must be a positive integer")
    target = Path(output)
    if target.exists():
        raise FileExistsError("dense index already exists; choose a new output path")
    chunks = load_corpus(db_path)
    batches = []
    dimension = None
    for start in range(0, len(chunks), batch_size):
        texts = [passage(chunk) for chunk in chunks[start:start + batch_size]]
        batch = normalized_vectors(embedder.embed(texts, query=False), rows=len(texts))
        if dimension is not None and batch.shape[1] != dimension:
            raise ValueError("embedding dimension changed between batches")
        dimension = batch.shape[1]
        batches.append(batch)
        if progress is not None:
            progress(min(start + batch_size, len(chunks)), len(chunks))
    vectors = np.concatenate(batches)
    metadata = {
        "version": INDEX_VERSION,
        "passage_format": PASSAGE_FORMAT,
        "corpus_fingerprint": corpus_fingerprint(chunks),
        "embedding_model": embedder.model,
        "encoder_fingerprint": embedder.fingerprint,
        "dimension": int(vectors.shape[1]),
        "chunk_count": len(chunks),
        "vectors_sha256": hashlib.sha256(vectors.tobytes()).hexdigest(),
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("xb") as stream:
        np.savez_compressed(stream, chunk_ids=np.asarray([row["id"] for row in chunks]),
                            vectors=vectors, metadata=json.dumps(metadata, ensure_ascii=False))
    return metadata
