"""Offline contract tests: no Qwen requests or BGE model loading."""
from __future__ import annotations

import json
import sqlite3

import numpy as np
import pytest

from civic_link.agent.index import DenseIndex, build_dense_index, load_corpus
from civic_link.agent.search import (HybridSearchTool, LocalBGEReranker,
                                     QwenEmbeddingClient, SearchConfig)


class FakeEmbedder:
    model = "test-embedding"
    fingerprint = "test-encoder-v1"

    def __init__(self):
        self.calls = []

    def embed(self, texts, *, query=False):
        self.calls.append((list(texts), query))
        # Different query surface forms point to the same semantic target.
        return [[0., 1.] if "beta" in text or "meaning-b" in text else [1., 0.]
                for text in texts]


class FakeReranker:
    def __init__(self):
        self.calls = []

    def score(self, query, passages):
        self.calls.append((query, list(passages)))
        preferred = "beta" if "beta" in query or "meaning-b" in query else "alpha"
        return [5. if preferred in text else -2. for text in passages]


@pytest.fixture
def corpus(tmp_path):
    path = tmp_path / "corpus.sqlite"
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE documents(document_id TEXT PRIMARY KEY, source_url TEXT);
            CREATE TABLE chunks(chunk_id TEXT PRIMARY KEY, document_id TEXT, title TEXT, text TEXT);
        """)
        db.executemany("INSERT INTO documents VALUES (?, ?)", [
            ("da", "https://source.test/a"), ("db", "https://source.test/b"),
            ("dc", "https://source.test/c"),
        ])
        db.executemany("INSERT INTO chunks VALUES (?, ?, ?, ?)", [
            ("a", "da", "alpha guide", "alpha required documents and complete conditions"),
            ("b", "db", "beta guide", "beta requires a proxy signature"),
            ("c", "dc", "gamma guide", "gamma is unrelated"),
        ])
    return path


def ready_tool(corpus, tmp_path, **config):
    embedder, reranker = FakeEmbedder(), FakeReranker()
    path = tmp_path / "dense.npz"
    build_dense_index(corpus, path, embedder, batch_size=2)
    tool = HybridSearchTool(corpus, path, embedder, reranker,
                            SearchConfig(**config), tokenizer=str.split)
    return tool, embedder, reranker, path


def test_new_queries_retrieve_and_rerank_actual_corpus_each_time(corpus, tmp_path):
    tool, embedder, reranker, _ = ready_tool(corpus, tmp_path, candidate_k=3, result_k=1)
    first = tool.run({"query": "alpha documents"})
    second = tool.run({"query": "meaning-b"})
    assert first["status"] == second["status"] == "ok"
    assert first["evidence"][0]["id"] == "a"
    assert second["evidence"][0]["id"] == "b"
    assert first["evidence"][0]["source"] == "https://source.test/a"
    assert first["evidence"][0]["text"] == "alpha required documents and complete conditions"
    assert [call[0] for call in embedder.calls if call[1]] == [["alpha documents"], ["meaning-b"]]
    assert [call[0] for call in reranker.calls] == ["alpha documents", "meaning-b"]
    assert first["metadata"]["not_returned_count"] == 2
    assert first["relations"] == []


def test_dense_retrieves_when_new_query_has_no_lexical_overlap(corpus, tmp_path):
    tool, _, reranker, _ = ready_tool(corpus, tmp_path, candidate_k=1, result_k=1)
    result = tool.run({"query": "meaning-b"})
    assert result["status"] == "ok"
    assert [row["id"] for row in result["evidence"]] == ["b"]
    assert len(reranker.calls[0][1]) == 1


def test_bm25_can_contribute_when_dense_points_elsewhere(corpus, tmp_path):
    tool, embedder, _, _ = ready_tool(corpus, tmp_path, candidate_k=2, result_k=2)
    original = embedder.embed
    embedder.embed = lambda texts, query=False: [[0., 1.]] if query else original(texts)
    result = tool.run({"query": "alpha"})
    assert result["status"] == "ok"
    assert {row["id"] for row in result["evidence"]} == {"a", "b"}
    assert result["evidence"][0]["id"] == "a"  # BGE judges the actual query.


def test_corpus_change_rejects_stale_index_before_query_call(corpus, tmp_path):
    tool, embedder, reranker, _ = ready_tool(corpus, tmp_path)
    with sqlite3.connect(corpus) as db:
        db.execute("UPDATE chunks SET text='changed body' WHERE chunk_id='a'")
    result = tool.run({"query": "alpha"})
    assert result["status"] == "error"
    assert "metadata does not match" in result["error"]["message"]
    assert not any(query for _, query in embedder.calls)
    assert reranker.calls == []


def test_encoder_change_rejects_existing_index(corpus, tmp_path):
    tool, embedder, _, _ = ready_tool(corpus, tmp_path)
    embedder.fingerprint = "changed-config"
    result = tool.run({"query": "alpha"})
    assert result["status"] == "error"
    assert result["evidence"] == []


def test_query_dimension_mismatch_is_error_not_lexical_fallback(corpus, tmp_path):
    tool, embedder, reranker, _ = ready_tool(corpus, tmp_path)
    embedder.embed = lambda texts, query=False: [[1., 0., 0.] for _ in texts]
    result = tool.run({"query": "alpha"})
    assert result["status"] == "error"
    assert "dimension" in result["error"]["message"]
    assert reranker.calls == []


@pytest.mark.parametrize("component", ["embedder", "reranker"])
def test_unavailable_component_is_not_a_successful_fallback(corpus, tmp_path, component):
    tool, embedder, reranker, _ = ready_tool(corpus, tmp_path)
    def fail(*args, **kwargs):
        raise RuntimeError("unavailable test component")
    if component == "embedder":
        embedder.embed = fail
    else:
        reranker.score = fail
    result = tool.run({"query": "alpha"})
    assert result["status"] == "error"
    assert result["evidence"] == []
    assert result["error"]["code"] == "search_unavailable"


@pytest.mark.parametrize("args", [{}, {"query": ""}, {"query": "a", "other": 1}, {"query": 4}])
def test_bad_requests_fail_without_touching_resources(tmp_path, args):
    tool = HybridSearchTool(tmp_path / "missing.db", tmp_path / "missing.npz", None, None)
    result = tool.run(args)
    assert result["error"]["code"] == "invalid_arguments"
    assert not (tmp_path / "missing.db").exists()


def test_index_build_refuses_overwrite_before_embedding(corpus, tmp_path):
    _, _, _, path = ready_tool(corpus, tmp_path)
    client = FakeEmbedder()
    original = path.read_bytes()
    with pytest.raises(FileExistsError):
        build_dense_index(corpus, path, client)
    assert client.calls == []
    assert path.read_bytes() == original


def test_index_detects_vector_corruption(corpus, tmp_path):
    _, embedder, _, path = ready_tool(corpus, tmp_path)
    with np.load(path) as original:
        fields = {name: original[name] for name in original.files}
    fields["vectors"] = fields["vectors"][[1, 0, 2]]
    altered = tmp_path / "altered.npz"
    np.savez_compressed(altered, **fields)
    with pytest.raises(ValueError, match="checksum"):
        DenseIndex.load(altered, load_corpus(corpus), embedder)


def test_embedding_http_client_orders_response_and_tracks_template(monkeypatch):
    requests = []
    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self, *args):
            return json.dumps({"data": [{"index": 1, "embedding": [0., 2.]},
                                        {"index": 0, "embedding": [3., 0.]}]}).encode()
    def open_request(request, timeout):
        requests.append(json.loads(request.data))
        return Response()
    monkeypatch.setattr("urllib.request.urlopen", open_request)
    client = QwenEmbeddingClient("https://embedding.test/v1/embeddings", query_instruction="find support")
    assert client.embed(["first", "second"], query=True) == [[1., 0.], [0., 1.]]
    assert requests[0]["input"] == ["Instruct: find support\nQuery: first", "Instruct: find support\nQuery: second"]
    plain = QwenEmbeddingClient("https://embedding.test/v1/embeddings")
    assert plain.fingerprint != client.fingerprint


def test_embedding_client_rejects_duplicate_indices(monkeypatch):
    import io
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: io.BytesIO(json.dumps({
        "data": [{"index": 0, "embedding": [1., 0.]}, {"index": 0, "embedding": [0., 1.]}],
    }).encode()))
    client = QwenEmbeddingClient("https://embedding.test/v1/embeddings")
    with pytest.raises(ValueError, match="duplicated"):
        client.embed(["first", "second"])


def test_missing_bge_model_is_explicit_and_does_not_download(tmp_path):
    reranker = LocalBGEReranker(tmp_path / "not-provisioned")
    with pytest.raises(FileNotFoundError, match="provision"):
        reranker.score("question", ["passage"])
