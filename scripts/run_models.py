from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

from civic_data import ROOT, connect, digest, now, read_jsonl, write_json, write_jsonl
from evaluate import evaluate
from graph_construct import load_graph
from graph_retrieval import DEFAULTS as GRAPH_DEFAULTS
from graph_retrieval import GraphRetriever
from retrieval import Dense, EmbeddingClient, load_chunks, run

TEXT_METHODS = ('bm25', 'dense', 'hybrid')
GRAPH_METHODS = ('graph_h0', 'graph_h1', 'graph_h2', 'graph_h3')
ALL_METHODS = TEXT_METHODS + GRAPH_METHODS


class QueryCache:
    def __init__(self, client, path):
        self.client = client
        self.path = Path(path)
        self.cached = {}
        self.calls = 0
        self.reused = 0
        if self.path.exists():
            for row in read_jsonl(self.path):
                if row['encoder_fingerprint'] != client.fingerprint or row['model'] != client.model:
                    raise ValueError('query embedding cache does not match the configured encoder')
                self.cached[row['formatted_query_sha256']] = row['vector']

    def __getattr__(self, name):
        return getattr(self.client, name)

    def encode(self, texts, kind='document'):
        if kind != 'query':
            return self.client.encode(texts, kind)
        vectors = []
        for text in texts:
            formatted = self.client.format_input(text, 'query')
            key = hashlib.sha256(formatted.encode()).hexdigest()
            if key not in self.cached:
                started = time.perf_counter()
                vector = self.client.encode([text], 'query')[0]
                row = {
                    'formatted_query_sha256': key,
                    'query_text_sha256': hashlib.sha256(text.encode()).hexdigest(),
                    'model': self.model,
                    'encoder_fingerprint': self.fingerprint,
                    'dimension': len(vector),
                    'vector': vector,
                    'embedding_latency_ms': (time.perf_counter() - started) * 1000,
                }
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open('a', encoding='utf-8') as handle:
                    handle.write(json.dumps(row, ensure_ascii=False) + '\n')
                self.cached[key] = vector
                self.calls += 1
            else:
                self.reused += 1
            vectors.append(self.cached[key])
        return vectors


def file_hash(path):
    return digest(Path(path).read_bytes())


def validate_inputs(queries, questions):
    expected = [{'query_id': row['question_id'], 'question': row['question']} for row in questions]
    if queries != expected:
        raise ValueError('queries and questions are not the same ordered question set')


def score(questions, predictions, known_chunk_ids):
    report, cases = evaluate(questions, predictions, known_chunk_ids)
    report['status_counts'] = dict(Counter(row['retrieval_status_at_10'] for row in cases))
    return report, cases


def output_dir(value):
    if value:
        return Path(value).resolve()
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    return ROOT / 'results' / 'week4_run' / stamp


def parse_args():
    parser = argparse.ArgumentParser(description='Run and evaluate Civic-Link retrieval methods.')
    parser.add_argument('--methods', nargs='+', choices=ALL_METHODS, default=list(ALL_METHODS))
    parser.add_argument('--queries', type=Path, default=ROOT / 'data/queries.jsonl')
    parser.add_argument('--questions', type=Path, default=ROOT / 'data/questions.jsonl')
    parser.add_argument('--db', type=Path, default=ROOT / 'data/processed/civic.sqlite')
    parser.add_argument('--graph', type=Path, default=ROOT / 'data/processed/graph.json')
    parser.add_argument('--output-dir', type=Path)
    parser.add_argument('--top-k', type=int, default=10)
    parser.add_argument('--candidate-k', type=int, default=100)
    parser.add_argument('--embedding-url', default=os.environ.get('CIVIC_EMBEDDING_URL'))
    parser.add_argument('--embedding-model', default=os.environ.get('CIVIC_EMBEDDING_MODEL', 'Qwen3-VL-Embedding-8B'))
    parser.add_argument('--embedding-api-key', default=os.environ.get('CIVIC_EMBEDDING_API_KEY'))
    parser.add_argument('--embedding-format', choices=('plain', 'qwen3-vl'), default='qwen3-vl')
    parser.add_argument('--embedding-timeout', type=int, default=120)
    parser.add_argument('--query-prefix', default='')
    parser.add_argument('--document-prefix', default='')
    parser.add_argument('--query-cache', type=Path)
    return parser.parse_args()


def main():
    args = parse_args()
    if args.top_k < 1 or args.candidate_k < args.top_k:
        raise ValueError('require candidate_k >= top_k >= 1')
    methods = list(dict.fromkeys(args.methods))
    destination = output_dir(args.output_dir)
    if destination.exists() and any(destination.iterdir()):
        raise ValueError(f'output directory is not empty: {destination}')
    destination.mkdir(parents=True, exist_ok=True)
    queries = read_jsonl(args.queries)
    questions = read_jsonl(args.questions)
    validate_inputs(queries, questions)
    with connect(args.db, True) as connection:
        chunks = {row['chunk_id']: dict(row) for row in connection.execute('SELECT * FROM chunks')}
        metadata = {row['key']: json.loads(row['value']) for row in connection.execute('SELECT * FROM metadata')}
    sources = {
        'queries': file_hash(args.queries),
        'questions': file_hash(args.questions),
        'database': file_hash(args.db),
    }
    manifest = {
        'status': 'running',
        'started_at': now(),
        'methods_requested': methods,
        'query_count': len(queries),
        'corpus_hash': metadata['corpus_hash'],
        'source_sha256': sources,
        'generation': None,
        'methods': {},
    }
    write_json(destination / 'run_manifest.json', manifest)
    client = None
    cache = None
    if any(method in ('dense', 'hybrid') for method in methods):
        base = EmbeddingClient(
            args.embedding_url,
            args.embedding_model,
            args.embedding_api_key,
            args.query_prefix,
            args.document_prefix,
            args.embedding_timeout,
            args.embedding_format,
        )
        cache_path = args.query_cache or ROOT / 'data/processed' / f'query_embeddings.{base.fingerprint[:12]}.jsonl'
        cache = QueryCache(base, cache_path)
        client = cache
        with connect(args.db, True) as connection:
            dense = Dense(connection, load_chunks(connection), client)
        manifest['document_embedding_cache'] = {
            'model': client.model,
            'encoder_fingerprint': client.fingerprint,
            'dimension': dense.dim,
            'chunks': len(dense.docs),
            'backend': dense.backend,
            'query_cache': str(cache_path),
        }
        write_json(destination / 'run_manifest.json', manifest)
    graph_retriever = None
    if any(method in GRAPH_METHODS for method in methods):
        graph, evidence = load_graph(args.graph, args.db)
        graph_retriever = GraphRetriever(graph, args.db, evidence)
        sources['graph'] = file_hash(args.graph)
        sources['graph_evidence'] = file_hash(args.graph.with_suffix('.evidence.json'))
    try:
        for method in methods:
            print(json.dumps({'phase': 'retrieval', 'method': method}, ensure_ascii=False), flush=True)
            if method in TEXT_METHODS:
                predictions, settings = run(
                    args.db,
                    queries,
                    method,
                    args.candidate_k if method == 'hybrid' else args.top_k,
                    args.candidate_k,
                    client if method != 'bm25' else None,
                )
                if method == 'hybrid':
                    # Export this retrieval's deeper candidates; evaluate the same top_k as before.
                    candidate_path = destination / 'hybrid_candidates.jsonl'
                    write_jsonl(candidate_path, predictions)
                    candidate_settings = dict(settings, queries_sha256=sources['queries'],
                                              predictions_sha256=file_hash(candidate_path))
                    write_json(candidate_path.with_suffix('.run.json'), candidate_settings)
                    manifest['hybrid_candidates'] = {'path': candidate_path.name,
                        'top_k': args.candidate_k, 'predictions_sha256': candidate_settings['predictions_sha256']}
                    predictions = [{**row, 'hits': row['hits'][:args.top_k]} for row in predictions]
                    settings = dict(settings, top_k=args.top_k)
            else:
                hops = int(method[-1])
                graph_config = {
                    'top_k': args.top_k,
                    'seed_k': GRAPH_DEFAULTS['seed_k'],
                    'max_hops': hops,
                    'max_nodes': GRAPH_DEFAULTS['max_nodes'],
                    'seed_threshold': GRAPH_DEFAULTS['seed_threshold'],
                }
                predictions, timings = graph_retriever.run(queries, **graph_config)
                settings = {
                    'method': method,
                    'query_count': len(queries),
                    'config': graph_config,
                    'query_latency_ms': timings,
                    'graph_sha256': sources['graph'],
                    'evidence_sha256': sources['graph_evidence'],
                    'created_at': now(),
                    'generation': None,
                }
            prediction_path = destination / f'{method}.predictions.jsonl'
            write_jsonl(prediction_path, predictions)
            settings['queries_sha256'] = sources['queries']
            settings['predictions_sha256'] = file_hash(prediction_path)
            write_json(destination / f'{method}.run.json', settings)
            report, cases = score(questions, predictions, set(chunks))
            write_json(destination / f'{method}.metrics.json', report)
            write_jsonl(destination / f'{method}.cases.jsonl', cases)
            manifest['methods'][method] = {
                'predictions_sha256': settings['predictions_sha256'],
                'overall': report['retrieval']['overall'],
                'status_counts': report['status_counts'],
            }
            write_json(destination / 'run_manifest.json', manifest)
            print(json.dumps({'method': method, **manifest['methods'][method]}, ensure_ascii=False), flush=True)
    except Exception as error:
        manifest.update(status='failed', failed_at=now(), error_type=type(error).__name__, error=str(error))
        write_json(destination / 'run_manifest.json', manifest)
        raise
    manifest.update(status='completed', completed_at=now())
    if cache:
        manifest['query_embeddings'] = {'new_requests': cache.calls, 'reused': cache.reused, 'cached': len(cache.cached)}
    write_json(destination / 'run_manifest.json', manifest)
    print(json.dumps({'status': 'completed', 'output_dir': str(destination)}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
