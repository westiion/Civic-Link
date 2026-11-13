"""Run or resume paired single-search / iterative-search / iterative-graph experiments."""
from __future__ import annotations
import _bootstrap
from dataclasses import asdict, dataclass
import json
import math
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
from civic_link.agent.parallel import RequestLimited, cuda_runtime, run_parallel_comparison, validate_devices


@dataclass
class Components:
    """Picklable specification; each spawned process owns its clients and model."""
    args: object
    scope: dict
    descriptions: dict
    config: AgentConfig
    search_config: SearchConfig

    def __call__(self, device, semaphore):
        args = self.args
        if semaphore is not None:
            import torch
            torch.cuda.set_device(device)
        embedder = QwenEmbeddingClient(args.embedding_endpoint, args.embedding_model,
            api_key=os.environ.get('QWEN_EMBEDDING_API_KEY'), timeout=args.timeout,
            query_instruction=args.query_instruction, encoder_revision=args.embedding_revision)
        if semaphore is not None:
            embedder = RequestLimited(embedder, semaphore)
        reranker = LocalBGEReranker(args.bge_model, device=device, max_length=args.bge_max_length,
                                    batch_size=args.bge_batch_size, threads=args.bge_threads)
        search = HybridSearchTool(args.db, args.index, embedder, reranker, config=self.search_config)
        graph = LazyTool(lambda: GraphTool.from_files(db=args.db, max_body_chars=args.graph_max_body_chars))
        if semaphore is not None:
            # A bad CUDA wheel, missing weights or a mismatched index must fail the
            # worker before they can be cached as 1,000 initial search errors.
            reranker.score('실행 점검', ['자료 확인 ' * args.bge_max_length] * args.bge_batch_size)
            search._prepare()
        def factory(condition):
            client = QwenClient(args.endpoint, args.model, api_key=os.environ.get('QWEN_API_KEY'),
                timeout=args.timeout, max_tokens=args.max_output_tokens, json_mode=args.json_mode,
                enable_thinking=args.enable_thinking)
            if semaphore is not None:
                client = RequestLimited(client, semaphore)
            return RetrievalAgent(client, {'search': search, 'graph': graph}, scope=self.scope,
                tool_descriptions=self.descriptions, config=self.config, allowed_tools=CONDITIONS[condition])
        return search, factory


def check_apis(args):
    """Two small requests, outside measured results; no URLs/keys are printed."""
    from civic_link.agent.index import DenseIndex, load_corpus
    chat = QwenClient(args.endpoint, args.model, api_key=os.environ.get('QWEN_API_KEY'),
        timeout=args.timeout, max_tokens=64, json_mode=args.json_mode)
    value = json.loads(chat.complete([dict(role='user', content='Return only this JSON object: {"ok": true}')]))
    if not isinstance(value, dict) or value.get('ok') is not True:
        raise ValueError('generation API check did not return the expected JSON')
    embedder = QwenEmbeddingClient(args.embedding_endpoint, args.embedding_model,
        api_key=os.environ.get('QWEN_EMBEDDING_API_KEY'), timeout=args.timeout,
        query_instruction=args.query_instruction, encoder_revision=args.embedding_revision)
    vector = embedder.embed(['연결 점검'], query=True)[0]
    index = DenseIndex.load(args.index, load_corpus(args.db), embedder)
    if len(vector) != index.metadata['dimension']:
        raise ValueError('embedding API dimension differs from the saved index')


def main(argv=None):
    p = parser(question_required=False)
    p.description = __doc__
    p.add_argument('--queries', type=Path, required=True)
    p.add_argument('--conditions', nargs='+', choices=tuple(CONDITIONS), default=list(CONDITIONS))
    p.add_argument('--limit', type=int, help='fixed prefix size for a smoke run; omit for the full selected query file')
    p.add_argument('--bge-devices', nargs='+', help='one worker per device, e.g. cuda:0 cuda:1 cuda:2 cuda:3')
    p.add_argument('--workers-per-gpu', type=int, default=1, help='independent question workers per device; each owns a BGE model')
    p.add_argument('--api-concurrency', type=int, help='shared maximum in-flight generation/embedding calls; defaults to worker count')
    args = p.parse_args(argv)
    if args.question or args.show_input or not args.output_dir:
        p.error('comparison needs --queries and --output-dir; --question/--show-input are single-session options')
    if not args.endpoint or not args.embedding_endpoint or not args.bge_model:
        p.error('configure QWEN_ENDPOINT, QWEN_EMBEDDING_ENDPOINT and BGE_MODEL_PATH (or CLI equivalents)')
    if args.graph_direction != 'outgoing':
        p.error('the frozen comparison protocol uses outgoing one-hop Graph traversal')
    if min(args.graph_max_body_chars,args.max_output_tokens,args.bge_batch_size,args.bge_threads) < 1:
        p.error('body and output limits must be positive')
    if not math.isfinite(args.timeout) or args.timeout <= 0 or args.bge_max_length < 8:
        p.error('invalid timeout or BGE input limit')
    if args.bge_devices:
        try:
            validate_devices(args.bge_devices)
        except ValueError as exc:
            p.error(str(exc))
        if args.bge_device != 'cpu':
            p.error('use --bge-devices alone for parallel execution, without --bge-device')
        if args.workers_per_gpu < 1:
            p.error('--workers-per-gpu must be positive')
        if args.api_concurrency is not None and not 1 <= args.api_concurrency <= len(args.bge_devices) * args.workers_per_gpu:
            p.error('--api-concurrency must be between 1 and the total worker count')
    elif args.api_concurrency is not None:
        p.error('--api-concurrency requires --bge-devices')
    elif args.workers_per_gpu != 1:
        p.error('--workers-per-gpu requires --bge-devices')
    config = AgentConfig(args.max_model_calls, args.max_tool_calls, args.max_invalid_responses, args.max_evidence_chars)
    if config.max_tool_calls < 1:
        p.error('the shared initial search needs at least one tool call')
    queries = load_queries(args.queries, args.limit)
    search_config = SearchConfig(candidate_k=args.candidate_k, result_k=args.result_k)
    search_config.validate()
    scope = corpus_scope(args.db)
    descriptions = describe_tools(candidate_k=args.candidate_k, result_k=args.result_k,
                                  graph_max_body_chars=args.graph_max_body_chars)
    components = Components(args, scope, descriptions, config, search_config)
    assets = {'corpus': args.db, 'dense_index': args.index, 'graph': ROOT/'data/graph/graph.json',
              'graph_evidence': ROOT/'data/graph/graph.evidence.json'}
    files = list((ROOT/'src/civic_link/agent').glob('*.py')) + [Path(__file__), ROOT/'scripts/run_agent.py']
    model_files = sorted(pth for pth in Path(args.bge_model).rglob('*') if pth.is_file())
    if not model_files:
        p.error('BGE model directory is empty or missing')
    settings = dict(model=args.model, agent=asdict(config), tools=descriptions,
        embedding_model=args.embedding_model, embedding_revision=args.embedding_revision,
        query_instruction=args.query_instruction, max_output_tokens=args.max_output_tokens,
        timeout=args.timeout, json_mode=args.json_mode, bge_device='per_worker' if args.bge_devices else args.bge_device,
        bge_max_length=args.bge_max_length, temperature=0,
        bge_batch_size=args.bge_batch_size, bge_threads=args.bge_threads, bge_dtype='float32',
        enable_thinking=args.enable_thinking,
        endpoint_identity=identity([args.endpoint, args.embedding_endpoint]),
        assets={name:file_hash(path) for name,path in assets.items()},
        code={path.relative_to(ROOT).as_posix():file_hash(path) for path in files},
        bge_files={path.relative_to(args.bge_model).as_posix():file_hash(path) for path in model_files})
    if args.bge_devices:
        settings['runtime'] = cuda_runtime(args.bge_devices)
        print('CUDA runtime verified; checking generation/embedding APIs...', flush=True)
        check_apis(args)
        print('API checks passed; starting GPU workers...', flush=True)
        run_parallel_comparison(queries, args.output_dir, args.conditions, settings, components,
            args.bge_devices, api_concurrency=args.api_concurrency, workers_per_gpu=args.workers_per_gpu,
            progress=lambda message: print(message, flush=True))
    else:
        search, factory = components(args.bge_device, None)
        run_comparison(queries, args.output_dir, args.conditions, settings, search, factory)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
