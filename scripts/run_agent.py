"""Run one Qwen-led retrieval session, or inspect its first input without a server."""
from __future__ import annotations

import _bootstrap
import argparse
from dataclasses import asdict
import json
import math
import os
from pathlib import Path
import sys

from civic_link.common import ROOT, write_json, write_jsonl
from civic_link.agent import AgentConfig, RetrievalAgent
from civic_link.agent.client import QwenClient
from civic_link.agent.prompts import corpus_scope


class LazyTool:
    def __init__(self, factory):
        self.factory, self.instance = factory, None

    def run(self, arguments):
        if self.instance is None:
            self.instance = self.factory()
        return self.instance.run(arguments)


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--question', required=True, help='original user question (never a benchmark gold row)')
    p.add_argument('--db', type=Path, default=ROOT/'data/corpus/civic.sqlite')
    p.add_argument('--endpoint', default=os.environ.get('QWEN_ENDPOINT'))
    p.add_argument('--model', default=os.environ.get('QWEN_MODEL', 'Qwen3.8-Flash-Next'))
    p.add_argument('--embedding-endpoint', default=os.environ.get('QWEN_EMBEDDING_ENDPOINT'))
    p.add_argument('--embedding-model', default=os.environ.get('QWEN_EMBEDDING_MODEL', 'Qwen3-VL-Embedding-8B'))
    p.add_argument('--embedding-revision', default=os.environ.get('QWEN_EMBEDDING_REVISION', 'unspecified'))
    p.add_argument('--query-instruction', default=os.environ.get('QWEN_QUERY_INSTRUCTION', ''))
    p.add_argument('--index', type=Path, default=ROOT/'outputs/agent-index/corpus.npz')
    p.add_argument('--bge-model', default=os.environ.get('BGE_MODEL_PATH'))
    p.add_argument('--bge-device', default='cpu')
    p.add_argument('--bge-max-length', type=int, default=1024)
    p.add_argument('--candidate-k', type=int, default=100)
    p.add_argument('--result-k', type=int, default=10)
    p.add_argument('--graph-direction', choices=('outgoing', 'incoming', 'both'), default='outgoing')
    p.add_argument('--graph-max-relations', type=int, default=20)
    p.add_argument('--graph-max-evidence', type=int, default=50)
    p.add_argument('--max-model-calls', type=int, default=8)
    p.add_argument('--max-tool-calls', type=int, default=6)
    p.add_argument('--max-invalid-responses', type=int, default=2)
    p.add_argument('--max-evidence-chars', type=int, default=60000)
    p.add_argument('--timeout', type=float, default=120)
    p.add_argument('--max-output-tokens', type=int, default=2000)
    p.add_argument('--json-mode', action='store_true', help='only enable after checking server support')
    p.add_argument('--show-input', action='store_true', help='print first messages; no model or search call')
    p.add_argument('--output-dir', type=Path, help='new directory for result, trace and run configuration')
    return p


def main(argv=None):
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    p = parser()
    args = p.parse_args(argv)
    if not args.db.is_file():
        p.error('corpus DB missing; run scripts/restore_data.py restore first')
    if args.output_dir and args.output_dir.exists():
        p.error('output directory already exists; use a new path')
    try:
        config = AgentConfig(args.max_model_calls, args.max_tool_calls,
                             args.max_invalid_responses, args.max_evidence_chars)
        from civic_link.agent.search import SearchConfig
        SearchConfig(candidate_k=args.candidate_k, result_k=args.result_k).validate()
        if min(args.graph_max_relations, args.graph_max_evidence, args.max_output_tokens) < 1:
            raise ValueError('graph limits and output tokens must be positive')
        if not math.isfinite(args.timeout) or args.timeout <= 0 or args.bge_max_length < 8:
            raise ValueError('invalid timeout or BGE input limit')
    except ValueError as exc:
        p.error(str(exc))
    descriptions = {
        'search': {'arguments': {'query': 'nonempty search text'},
                   'method': 'BM25 + Dense, RRF then BGE',
                   'candidate_k': args.candidate_k, 'result_k': args.result_k},
        'graph': {'arguments': {'target': 'actual node ID or stored name', 'relation': 'one supported relation'},
                  'relations': ['GROUNDED_BY', 'REFERENCES', 'REQUIRES', 'HANDLED_BY', 'PART_OF'],
                  'relation_meanings': {
                      'REQUIRES': 'Service → Document: 제출서류 종류. Document는 검색 원문 파일을 뜻하지 않음. 조건은 qualifiers 확인.',
                      'HANDLED_BY': 'Service → Agency: 처리·접수기관 관계. 개인 접수 결과를 뜻하지 않음.',
                      'GROUNDED_BY': 'Service → Article: 연결된 조문. 명칭 언급도 포함하므로 자격·적용의 증명이 아님.',
                      'PART_OF': 'Article → Law: 조문이 속한 법령.',
                      'REFERENCES': 'Article → Article: 조문 간 인용 관계. 본문과 적용 조건을 함께 확인.',
                  },
                  'direction': args.graph_direction, 'hops': 1,
                  'max_relations': args.graph_max_relations, 'max_evidence': args.graph_max_evidence},
    }

    def search_factory():
        from civic_link.agent.search import HybridSearchTool, SearchConfig, QwenEmbeddingClient, LocalBGEReranker
        if not args.embedding_endpoint or not args.bge_model:
            raise ValueError('search requires QWEN_EMBEDDING_ENDPOINT and BGE_MODEL_PATH (or matching CLI options)')
        embedder = QwenEmbeddingClient(args.embedding_endpoint, args.embedding_model,
                                       api_key=os.environ.get('QWEN_EMBEDDING_API_KEY'),
                                       timeout=args.timeout, query_instruction=args.query_instruction,
                                       encoder_revision=args.embedding_revision)
        reranker = LocalBGEReranker(args.bge_model, device=args.bge_device, max_length=args.bge_max_length)
        return HybridSearchTool(args.db, args.index, embedder, reranker,
                                config=SearchConfig(candidate_k=args.candidate_k, result_k=args.result_k))

    def graph_factory():
        from civic_link.agent.graph import GraphTool
        return GraphTool.from_files(db=args.db, direction=args.graph_direction,
                                    max_relations=args.graph_max_relations,
                                    max_evidence=args.graph_max_evidence)

    tools = {'search': LazyTool(search_factory), 'graph': LazyTool(graph_factory)}
    client = None
    if not args.show_input:
        if not args.endpoint:
            p.error('set QWEN_ENDPOINT or --endpoint to the generation server chat/completions URL')
        client = QwenClient(args.endpoint, args.model, api_key=os.environ.get('QWEN_API_KEY'),
                            timeout=args.timeout, max_tokens=args.max_output_tokens, json_mode=args.json_mode)
    agent = RetrievalAgent(client, tools, scope=corpus_scope(args.db),
                           tool_descriptions=descriptions, config=config)
    if args.show_input:
        print(json.dumps({'messages': agent.initial_messages(args.question)}, ensure_ascii=False, indent=2))
        return 0
    result = agent.run(args.question)
    print(result['rendered_response'])
    if args.output_dir:
        events = result.pop('events')
        write_json(args.output_dir/'result.json', result)
        write_jsonl(args.output_dir/'trace.jsonl', events)
        write_json(args.output_dir/'config.json', {
            'model': args.model, 'embedding_model': args.embedding_model,
            'embedding_revision': args.embedding_revision, 'query_instruction': args.query_instruction,
            'agent': asdict(config), 'tools': descriptions,
            'bge_device': args.bge_device, 'bge_max_length': args.bge_max_length,
            'timeout': args.timeout, 'max_output_tokens': args.max_output_tokens,
            'json_mode': args.json_mode,
            'validation_scope': 'format and source identity only; semantic grounding not independently verified',
        })
    return 0 if result['status'] == 'responded' else 2


if __name__ == '__main__':
    raise SystemExit(main())
