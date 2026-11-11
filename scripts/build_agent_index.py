"""Explicitly build a corpus dense index using the configured Qwen server."""
from __future__ import annotations

import argparse
import json
import os
import sys

import _bootstrap  # noqa: F401
from civic_link.agent.index import build_dense_index
from civic_link.agent.search import QwenEmbeddingClient
from civic_link.common import ROOT


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(ROOT / "data/corpus/civic.sqlite"))
    parser.add_argument("--output", required=True, help="New .npz index file; existing files are not overwritten")
    parser.add_argument("--endpoint", default=os.environ.get("QWEN_EMBEDDING_ENDPOINT"), help="Full embeddings API URL")
    parser.add_argument("--model", default=os.environ.get("QWEN_EMBEDDING_MODEL", "Qwen3-VL-Embedding-8B"))
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--query-instruction", default=os.environ.get("QWEN_QUERY_INSTRUCTION", ""))
    parser.add_argument("--encoder-revision", "--embedding-revision", default=os.environ.get("QWEN_EMBEDDING_REVISION", "unspecified"), help="Set when the serving model revision is known; keep identical at query time")
    args = parser.parse_args()
    if not args.endpoint:
        parser.error("set QWEN_EMBEDDING_ENDPOINT or --endpoint")
    client = QwenEmbeddingClient(args.endpoint, args.model,
                                 api_key=os.environ.get("QWEN_EMBEDDING_API_KEY"),
                                 timeout=args.timeout, query_instruction=args.query_instruction,
                                 encoder_revision=args.encoder_revision)
    try:
        def progress(done, total):
            if done == total or done // 256 != (done - args.batch_size) // 256:
                print(f'Embedded {done}/{total} chunks', file=sys.stderr, flush=True)
        metadata = build_dense_index(args.db, args.output, client, batch_size=args.batch_size, progress=progress)
    except (ValueError, RuntimeError, OSError) as exc:
        parser.exit(1, f"Index build failed: {exc}\n")
    print(json.dumps(metadata, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
