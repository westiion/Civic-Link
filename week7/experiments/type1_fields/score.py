"""Offline CPU measurement of question/passage pairs for the Type 1 field experiment."""
import argparse
import hashlib
import json
import math
import os
import sys
import time
from pathlib import Path


REVISION = "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e"
ASSETS = {
    "config.json": "13dcd6c31d9fec9d1d8e158702072f62d7fa7d312a64b9fe057bec9a08cfe41a",
    "sentencepiece.bpe.model": "cfc8146abe2a0488e9e2a0c56de7952f7c11ab059eca145a0a727afce0db2865",
    "special_tokens_map.json": "8c785abebea9ae3257b61681b4e6fd8365ceafde980c21970d001e834cf10835",
    "tokenizer_config.json": "7e4c1cc848840aeccdd763458c18dd525eb0f795c992e00ebe9c28554e7db2d4",
    "tokenizer.json": "69564b696052886ed0ac63fa393e928384e0f8caada38c1f4864a9bfbf379c15",
}
WEIGHTS = {
    "d9e3e081faff1eefb84019509b2f5558fd74c1a05a2c7db22f74174fcedb5286",
    "04959d987094b10a2c6f15bf6208dc9f5e43a7734a454f76daaeaebe5c808010",
}


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def write_json(path, record):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(record, indent=2, allow_nan=False) + "\n", encoding="utf8")
    temporary.replace(path)


def inputs(path):
    rows = {}
    for line in path.read_bytes().splitlines():
        r = json.loads(line)
        key = r["query_id"], r["passage_id"]
        if key in rows or not all(isinstance(s, str) and s.strip() for s in (*key, r["question"], r["passage"])):
            raise ValueError(f"Invalid or duplicate input {key!r}")
        rows[key] = r["question"], r["passage"]
    if not rows:
        raise ValueError("Empty inputs")
    return rows


def read_log(path):
    scores = {}
    if path.exists():
        with path.open("r+b") as f:
            while True:
                start = f.tell()
                line = f.readline()
                if not line:
                    break
                if not line.endswith(b"\n"):
                    f.truncate(start)
                    break
                r = json.loads(line)
                key = r["query_id"], r["passage_id"]
                if key in scores or not math.isfinite(r["score"]):
                    raise ValueError(f"Invalid checkpoint entry {key}")
                scores[key] = r["score"]
    return scores


def parse_args():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--inputs", required=True, type=Path)
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--model-dir", type=Path, default=os.environ.get("CIVIC_BGE_MODEL_DIR"))
    p.add_argument("--packages", type=Path, help="Optional dependency directory to prepend")
    p.add_argument("--extra-site", type=Path, action="append", default=[])
    p.add_argument("--batch-size", type=int, default=8)
    p.add_argument("--threads", type=int, default=4)
    p.add_argument("--max-batches", type=int, help="Stop after this many new batches, retaining scores")
    cfg = p.parse_args()
    if not cfg.model_dir or min(cfg.threads, cfg.batch_size) < 1 or (cfg.max_batches is not None and cfg.max_batches < 1):
        p.error("Supply a model and positive process settings")
    return cfg


def run(cfg):
    import torch
    import transformers
    from transformers import AutoTokenizer, AutoModelForSequenceClassification

    torch.set_num_threads(cfg.threads)
    torch.set_num_interop_threads(1)
    data = inputs(cfg.inputs)
    assets = {p: digest(cfg.model_dir / p) for p in ASSETS}
    weight_hash = digest(cfg.model_dir / "model.safetensors")
    if assets != ASSETS or weight_hash not in WEIGHTS:
        raise ValueError("Checkpoint does not match the official pinned snapshot or the verified FP16 storage conversion.")
    expected = {
        "model": "BAAI/bge-reranker-v2-m3", "revision": REVISION, "weight_sha256": weight_hash,
        "assets_sha256": assets, "inputs_sha256": digest(cfg.inputs), "input_count": len(data),
        "max_length": 512, "truncation": "only_second", "device": "cpu", "dtype": "float32",
        "attention": "sdpa", "torch": str(torch.__version__), "transformers": transformers.__version__,
        "threads": cfg.threads, "batch_size": cfg.batch_size, "scoring": "raw_logit",
    }
    meta, log = cfg.out / "provenance.json", cfg.out / "scores.jsonl"
    if meta.exists():
        if json.loads(meta.read_bytes()) != expected:
            raise ValueError("Checkpoint provenance differs; use a separate output directory")
    else:
        if log.exists():
            raise ValueError("Scores without matching provenance")
        write_json(meta, expected)
    scores = read_log(log)
    if scores.keys() - data.keys():
        raise ValueError("Unexpected scored IDs")
    if scores.keys() == data.keys():
        print(f"All {len(data)} pairs already scored.", flush=True)
        return

    tok = AutoTokenizer.from_pretrained(str(cfg.model_dir), local_files_only=True, use_fast=True, trust_remote_code=False)
    keys = sorted(data)
    encoded = tok([data[k][0] for k in keys], [data[k][1] for k in keys],
                  truncation="only_second", max_length=512, padding=False)
    examples = {key: {col: seq[j] for col, seq in encoded.items()} for j, key in enumerate(keys)}
    lens = {key: len(e["input_ids"]) for key, e in examples.items()}
    keys.sort(key=lambda k: (lens[k], k))
    batches = [keys[j:j + cfg.batch_size] for j in range(0, len(keys), cfg.batch_size)]
    # Representative prefix gives an early throughput estimate. Batch members do not change on restart.
    last = len(batches) - 1
    priority = list(dict.fromkeys(round(last * fraction) for fraction in (0.5, 0.1, 0.9, 0.25, 0.75, 1)))
    order = priority + [i for i in range(len(batches)) if i not in priority]
    remaining = [batches[i] for i in order if any(k not in scores for k in batches[i])]
    total_units = sum(len(b) * max(lens[k] for k in b) for b in remaining)
    stats = {
        "input_count": len(keys), "tokens": sum(lens.values()), "max_tokens": max(lens.values()),
        "min_tokens": min(lens.values()), "mean_tokens": sum(lens.values()) / len(keys),
        "batches": len(batches), "padded_tokens": sum(len(b) * max(lens[k] for k in b) for b in batches),
    }
    write_json(cfg.out / "input_stats.json", stats)
    print(f"Loading verified offline model: {len(scores)}/{len(keys)} scored, " +
          f"{stats['mean_tokens']:.1f} tokens/pair, max {stats['max_tokens']}.", flush=True)
    model = AutoModelForSequenceClassification.from_pretrained(
        str(cfg.model_dir), local_files_only=True, trust_remote_code=False, use_safetensors=True,
        torch_dtype=torch.float32, attn_implementation="sdpa", low_cpu_mem_usage=True,
    ).eval()
    param = next(model.parameters())
    assert param.device.type == "cpu" and param.dtype == torch.float32 and model.config._attn_implementation == "sdpa"
    started = time.monotonic()
    work = 0
    timings = []
    with torch.inference_mode(), log.open("ab") as stream:
        for i, batch in enumerate(remaining):
            tick = time.monotonic()
            tensors = tok.pad([examples[k] for k in batch], padding=True, return_tensors="pt")
            logits = model(**tensors, return_dict=True).logits
            if tuple(logits.shape) != (len(batch), 1):
                raise ValueError(f"Unexpected logit shape {tuple(logits.shape)}")
            for key, score in zip(batch, logits[:, 0].float().tolist()):
                if not math.isfinite(score):
                    raise ValueError(f"Nonfinite logit for {key}")
                if key not in scores:
                    stream.write((json.dumps({"query_id": key[0], "passage_id": key[1], "score": score},
                                            allow_nan=False) + "\n").encode())
                    scores[key] = score
            stream.flush()
            os.fsync(stream.fileno())
            size = len(batch) * max(lens[k] for k in batch)
            work += size
            elapsed = time.monotonic() - started
            eta = elapsed * (total_units - work) / work
            timings.append({"pairs": len(batch), "padded_tokens": size, "seconds": time.monotonic() - tick})
            if i < 6 or (i + 1) % 10 == 0 or len(scores) == len(keys):
                progress = {"scored": len(scores), "total": len(keys), "elapsed_s": elapsed, "eta_s": eta,
                            "latest_batch": timings[-1]}
                write_json(cfg.out / "progress.json", progress)
                print(f"Scored {len(scores)}/{len(keys)}; elapsed {elapsed/60:.1f}m; ETA {eta/60:.1f}m", flush=True)
            if cfg.max_batches and i + 1 >= cfg.max_batches:
                break
    stats = {"pairs_scored_this_session": sum(x["pairs"] for x in timings), "batches": timings,
             "elapsed_seconds": time.monotonic() - started, "completed": len(scores) == len(keys)}
    write_json(cfg.out / "last_session.json", stats)
    if not cfg.max_batches:
        assert scores.keys() == data.keys()


if __name__ == "__main__":
    args = parse_args()
    if args.packages:
        sys.path.insert(0, str(args.packages))
    sys.path.extend(str(p) for p in args.extra_site)
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", TOKENIZERS_PARALLELISM="false",
                      USE_TF="0", USE_FLAX="0", OMP_NUM_THREADS=str(args.threads))
    args.out.mkdir(parents=True, exist_ok=True)
    from filelock import FileLock
    with FileLock(str(args.out / ".run.lock"), timeout=0):
        run(args)
