"""Run or resume paired single-search / iterative-search / iterative-graph experiments."""
from __future__ import annotations
import _bootstrap
from dataclasses import asdict
import os
from pathlib import Path

from run_agent import parser, LazyTool
from civic_link.common import ROOT
from civic_link.agent import AgentConfig, RetrievalAgent
from civic_link.agent.client import QwenClient
from civic_link.agent.graph import GraphTool
from civic_link.agent.prompts import corpus_scope, describe_tools
from civic_link.agent.search import HybridSearchTool, SearchConfig, QwenEmbeddingClient, LocalBGEReranker
from civic_link.agent.experiment import CONDITIONS, load_queries, run_comparison, file_hash, identity


def main(argv=None):
    p = parser(question_required=False)
    p.description = __doc__
    p.add_argument('--queries', type=Path, required=True)
    p.add_argument('--conditions', nargs='+', choices=tuple(CONDITIONS), default=list(CONDITIONS))
    p.add_argument('--limit', type=int, help='fixed prefix size for a smoke run; omit for the full selected query file')
    args = p.parse_args(argv)
    if args.question or args.show_input or not args.output_dir:
        p.error('comparison needs --queries and --output-dir; --question/--show-input are single-session options')
    if not args.endpoint or not args.embedding_endpoint or not args.bge_model:
        p.error('configure QWEN_ENDPOINT, QWEN_EMBEDDING_ENDPOINT and BGE_MODEL_PATH (or CLI equivalents)')
    if args.graph_direction != 'outgoing':
        p.error('the frozen comparison protocol uses outgoing one-hop Graph traversal')
    if min(args.graph_max_body_chars,args.max_output_tokens,args.bge_batch_size,args.bge_threads) < 1:
        p.error('body and output limits must be positive')
    config = AgentConfig(args.max_model_calls, args.max_tool_calls, args.max_invalid_responses, args.max_evidence_chars)
    if config.max_tool_calls < 1:
        p.error('the shared initial search needs at least one tool call')
    queries = load_queries(args.queries, args.limit)
    search_config = SearchConfig(candidate_k=args.candidate_k, result_k=args.result_k)
    search_config.validate()
    embedder = QwenEmbeddingClient(args.embedding_endpoint, args.embedding_model,
        api_key=os.environ.get('QWEN_EMBEDDING_API_KEY'), timeout=args.timeout,
        query_instruction=args.query_instruction, encoder_revision=args.embedding_revision)
    reranker = LocalBGEReranker(args.bge_model, device=args.bge_device, max_length=args.bge_max_length,
                                batch_size=args.bge_batch_size, threads=args.bge_threads)
    search = HybridSearchTool(args.db, args.index, embedder, reranker, config=search_config)
    graph = LazyTool(lambda: GraphTool.from_files(db=args.db, max_body_chars=args.graph_max_body_chars))
    scope = corpus_scope(args.db)
    descriptions = describe_tools(candidate_k=args.candidate_k, result_k=args.result_k,
                                  graph_max_body_chars=args.graph_max_body_chars)
    def factory(condition):
        client = QwenClient(args.endpoint, args.model, api_key=os.environ.get('QWEN_API_KEY'),
            timeout=args.timeout, max_tokens=args.max_output_tokens, json_mode=args.json_mode,
            enable_thinking=args.enable_thinking)
        return RetrievalAgent(client, {'search': search, 'graph': graph}, scope=scope,
                              tool_descriptions=descriptions, config=config, allowed_tools=CONDITIONS[condition])
    assets = {'corpus': args.db, 'dense_index': args.index, 'graph': ROOT/'data/graph/graph.json',
              'graph_evidence': ROOT/'data/graph/graph.evidence.json'}
    files = list((ROOT/'src/civic_link/agent').glob('*.py')) + [Path(__file__), ROOT/'scripts/run_agent.py']
    model_files = sorted(pth for pth in Path(args.bge_model).rglob('*') if pth.is_file())
    if not model_files:
        p.error('BGE model directory is empty or missing')
    settings = dict(model=args.model, agent=asdict(config), tools=descriptions,
        embedding_model=args.embedding_model, embedding_revision=args.embedding_revision,
        query_instruction=args.query_instruction, max_output_tokens=args.max_output_tokens,
        timeout=args.timeout, json_mode=args.json_mode, bge_device=args.bge_device,
        bge_max_length=args.bge_max_length, temperature=0,
        bge_batch_size=args.bge_batch_size, bge_threads=args.bge_threads, bge_dtype='float32',
        enable_thinking=args.enable_thinking,
        endpoint_identity=identity([args.endpoint, args.embedding_endpoint]),
        assets={name:file_hash(path) for name,path in assets.items()},
        code={path.relative_to(ROOT).as_posix():file_hash(path) for path in files},
        bge_files={path.relative_to(args.bge_model).as_posix():file_hash(path) for path in model_files})
    run_comparison(queries, args.output_dir, args.conditions, settings, search, factory)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
