"""Live BM25 + Dense retrieval followed by BGE relevance reranking.

No frozen question-to-ranking cache is used. Network/model work is lazy and
unavailable components fail explicitly rather than changing the search method.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
import hashlib
import json
import math
from pathlib import Path
import sqlite3
from typing import Any, Callable
import urllib.error
import urllib.parse
import urllib.request

import numpy as np

from civic_link.agent.index import DenseIndex, load_corpus, normalized_vectors, passage


@dataclass(frozen=True)
class SearchConfig:
    candidate_k: int = 100
    result_k: int = 10
    rrf_constant: int = 60
    bm25_k1: float = 1.2
    bm25_b: float = .75

    def validate(self) -> None:
        for name in ("candidate_k", "result_k", "rrf_constant"):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if self.result_k > self.candidate_k:
            raise ValueError("result_k cannot exceed candidate_k")
        if not math.isfinite(self.bm25_k1) or self.bm25_k1 <= 0:
            raise ValueError("bm25_k1 must be positive")
        if not math.isfinite(self.bm25_b) or not 0 <= self.bm25_b <= 1:
            raise ValueError("bm25_b must be in [0, 1]")


class QwenEmbeddingClient:
    """Client for an explicitly configured OpenAI-compatible embeddings URL."""

    def __init__(self, endpoint: str, model: str = "Qwen3-VL-Embedding-8B", *,
                 api_key: str | None = None, timeout: float = 60,
                 query_instruction: str = "", encoder_revision: str = "unspecified"):
        parsed = urllib.parse.urlparse(endpoint)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("embedding endpoint must be an HTTP(S) URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("embedding endpoint must not contain credentials/query/fragment")
        if not model.strip() or not encoder_revision.strip() or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("invalid embedding model/revision/timeout")
        self.endpoint, self.model = endpoint, model
        self.api_key, self.timeout = api_key, timeout
        self.query_instruction = query_instruction
        self.fingerprint = hashlib.sha256(json.dumps({
            "model": model, "encoder_revision": encoder_revision,
            "query_instruction": query_instruction, "document_input": "plain-text",
        }, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def embed(self, texts: list[str], *, query: bool = False) -> list[list[float]]:
        if not texts or any(not isinstance(text, str) or not text.strip() for text in texts):
            raise ValueError("embedding inputs must be non-empty strings")
        inputs = ([f"Instruct: {self.query_instruction}\nQuery: {text}" for text in texts]
                  if query and self.query_instruction else texts)
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key
        request = urllib.request.Request(self.endpoint, data=json.dumps({
            "model": self.model, "input": inputs,
        }, ensure_ascii=False).encode("utf-8"), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"embedding request failed (HTTP {exc.code})") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise RuntimeError("embedding server could not be reached") from exc
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list) or len(data) != len(texts):
            raise ValueError("embedding response count does not match request")
        if any(not isinstance(row, dict) or type(row.get("index")) is not int for row in data):
            raise ValueError("embedding response lacks valid input indices")
        if sorted(row["index"] for row in data) != list(range(len(texts))):
            raise ValueError("embedding response indices are missing or duplicated")
        vectors = [row.get("embedding") for row in sorted(data, key=lambda row: row["index"])]
        return normalized_vectors(vectors, rows=len(texts)).tolist()


class LocalBGEReranker:
    """Explicit local BGE model; never downloads weights or switches devices."""

    def __init__(self, model_path: str | Path, *, device: str = "cpu",
                 batch_size: int = 8, max_length: int = 1024):
        if type(batch_size) is not int or batch_size < 1 or type(max_length) is not int or max_length < 8:
            raise ValueError("invalid BGE batch size/input limit")
        self.model_path, self.device = Path(model_path), device
        self.batch_size, self.max_length = batch_size, max_length
        self._model = self._tokenizer = None

    def _load(self) -> None:
        if self._model is not None:
            return
        if not self.model_path.is_dir():
            raise FileNotFoundError("local BGE model directory is missing; provision it explicitly")
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
        tokenizer = AutoTokenizer.from_pretrained(self.model_path, local_files_only=True,
                                                  trust_remote_code=False)
        model = AutoModelForSequenceClassification.from_pretrained(
            self.model_path, local_files_only=True, trust_remote_code=False,
            use_safetensors=True, dtype=torch.float32,
        ).to(self.device).eval()
        self._tokenizer, self._model = tokenizer, model

    def score(self, query: str, passages: list[str]) -> list[float]:
        self._load()
        import torch
        tokenizer, model = self._tokenizer, self._model
        if len(tokenizer.encode(query, add_special_tokens=False)) + tokenizer.num_special_tokens_to_add(pair=True) >= self.max_length:
            raise ValueError("query exceeds the configured reranker input limit")
        scores = []
        with torch.inference_mode():
            for offset in range(0, len(passages), self.batch_size):
                batch = passages[offset:offset + self.batch_size]
                encoded = tokenizer([query] * len(batch), batch, padding=True,
                                    truncation="only_second", max_length=self.max_length,
                                    return_tensors="pt")
                logits = model(**{key: value.to(self.device) for key, value in encoded.items()}).logits
                values = logits.float().reshape(-1).cpu().tolist()
                if len(values) != len(batch) or not all(math.isfinite(v) for v in values):
                    raise ValueError("reranker returned invalid relevance scores")
                scores.extend(values)
        return scores


class HybridSearchTool:
    def __init__(self, db_path: str | Path, index_path: str | Path, embedder: Any,
                 reranker: Any, config: SearchConfig | None = None, *,
                 tokenizer: Callable[[str], list[str]] | None = None):
        self.db_path, self.index_path = Path(db_path), Path(index_path)
        self.embedder, self.reranker = embedder, reranker
        self.config = config or SearchConfig()
        self.config.validate()
        self.tokenizer = tokenizer
        self._chunks = self._index = None

    def _prepare(self) -> None:
        if self._chunks is not None:
            return
        if self.tokenizer is None:
            from civic_link.tokenization import tokenize
            self.tokenizer = tokenize
        chunks = load_corpus(self.db_path)
        index = DenseIndex.load(self.index_path, chunks, self.embedder)
        counts = [Counter(self.tokenizer(passage(chunk))) for chunk in chunks]
        lengths = np.asarray([sum(count.values()) for count in counts], dtype=float)
        postings = {}
        for position, count in enumerate(counts):
            for token, frequency in count.items():
                postings.setdefault(token, []).append((position, frequency))
        self._counts, self._lengths, self._postings = counts, lengths, postings
        self._avg_length = max(float(lengths.mean()), 1.)
        self._chunks, self._index = chunks, index

    def _bm25(self, query: str) -> np.ndarray:
        cfg = self.config
        scores = np.zeros(len(self._chunks), dtype=float)
        for token in sorted(set(self.tokenizer(query))):
            entries = self._postings.get(token, [])
            if not entries:
                continue
            df = len(entries)
            idf = math.log(1 + (len(self._chunks) - df + .5) / (df + .5))
            for position, frequency in entries:
                denominator = frequency + cfg.bm25_k1 * (
                    1 - cfg.bm25_b + cfg.bm25_b * self._lengths[position] / self._avg_length)
                scores[position] += idf * frequency * (cfg.bm25_k1 + 1) / denominator
        return scores

    def run(self, arguments: dict) -> dict:
        result = {"tool": "search", "status": "error", "evidence": [], "relations": []}
        if not isinstance(arguments, dict) or set(arguments) != {"query"} or not isinstance(arguments.get("query"), str) or not arguments["query"].strip():
            result["error"] = {"code": "invalid_arguments", "message": "search requires only a non-empty query string"}
            return result
        query = arguments["query"]
        try:
            self._prepare()
            cfg, chunks, index = self.config, self._chunks, self._index
            query_vector = normalized_vectors(self.embedder.embed([query], query=True), rows=1)
            if query_vector.shape[1] != index.vectors.shape[1]:
                raise ValueError("query embedding dimension does not match dense index")
            dense_scores = index.vectors @ query_vector[0]
            lexical_scores = self._bm25(query)
            dense = sorted(range(len(chunks)), key=lambda i: (-float(dense_scores[i]), chunks[i]["id"]))[:cfg.candidate_k]
            lexical = sorted((i for i in range(len(chunks)) if lexical_scores[i] > 0),
                             key=lambda i: (-float(lexical_scores[i]), chunks[i]["id"]))[:cfg.candidate_k]
            fused = {}
            for ranking in (lexical, dense):
                for rank, position in enumerate(ranking, 1):
                    fused[position] = fused.get(position, 0.) + 1 / (cfg.rrf_constant + rank)
            candidates = sorted(fused, key=lambda i: (-fused[i], chunks[i]["id"]))[:cfg.candidate_k]
            raw_scores = self.reranker.score(query, [passage(chunks[i]) for i in candidates])
            if len(raw_scores) != len(candidates) or any(type(score) not in (int, float) or not math.isfinite(score) for score in raw_scores):
                raise ValueError("reranker scores do not match candidate pool")
            ranked = sorted(zip(candidates, raw_scores), key=lambda pair: (-pair[1], chunks[pair[0]]["id"]))
            evidence = []
            for rank, (position, score) in enumerate(ranked[:cfg.result_k], 1):
                evidence.append({**chunks[position], "rank": rank,
                                 "metadata": {"bge_relevance": score, "hybrid_rrf": fused[position]}})
            result.update(status="ok" if evidence else "no_results", evidence=evidence,
                          metadata={"query": query, "corpus_chunks": len(chunks),
                                    "candidate_count": len(candidates), "returned_count": len(evidence),
                                    "not_returned_count": max(0, len(candidates) - len(evidence)),
                                    "candidate_ids": [chunks[position]["id"] for position, _ in ranked],
                                    "config": asdict(cfg),
                                    "corpus_fingerprint": index.metadata["corpus_fingerprint"],
                                    "encoder_fingerprint": self.embedder.fingerprint})
            return result
        except (ValueError, OSError, RuntimeError, ImportError, sqlite3.DatabaseError) as exc:
            result["error"] = {"code": "search_unavailable", "message": str(exc)}
            return result
