"""Compare direct BGE rankings at 512 and 1024 tokens on the six affected Week 7 questions.

Run from the repository with a persistent, local bge-reranker-v2-m3 checkpoint.
Set CIVIC_BGE_MODEL_DIR or pass --model-dir. No network or model installation is performed.
"""

import argparse
import csv
import gzip
import hashlib
import json
import math
import os
import sqlite3
import sys
from collections import defaultdict
from contextlib import closing
from pathlib import Path
from statistics import mean


MODEL = "BAAI/bge-reranker-v2-m3"
REVISION = "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e"
TOKENIZER_SHA = "69564b696052886ed0ac63fa393e928384e0f8caada38c1f4864a9bfbf379c15"
MODEL_CONFIG_HASHES = {
    "config.json": "13dcd6c31d9fec9d1d8e158702072f62d7fa7d312a64b9fe057bec9a08cfe41a",
    "sentencepiece.bpe.model": "cfc8146abe2a0488e9e2a0c56de7952f7c11ab059eca145a0a727afce0db2865",
    "special_tokens_map.json": "8c785abebea9ae3257b61681b4e6fd8365ceafde980c21970d001e834cf10835",
    "tokenizer_config.json": "7e4c1cc848840aeccdd763458c18dd525eb0f795c992e00ebe9c28554e7db2d4",
}
WEIGHTS = {
    "d9e3e081faff1eefb84019509b2f5558fd74c1a05a2c7db22f74174fcedb5286": "official-fp32",
    "04959d987094b10a2c6f15bf6208dc9f5e43a7734a454f76daaeaebe5c808010": "verified-fp16-conversion",
}
# (one-based evidence group containing the previously invisible quote, baseline candidate count, long count)
CASES = {
    "W7-LA-024": (1, 112, 12),
    "W7V2-LA010": (2, 102, 19),
    "W7V2-LA016": (1, 116, 19),
    "W7V2-MS007": (2, 120, 15),
    "W7V2-MS037": (1, 107, 15),
    "W7V2-MS044": (1, 103, 24),
}
DIRECT = Path("week7/results/experiment2_bge_direct_20261101")
BGE = Path("week7/results/experiment2_20261031/bge.jsonl.gz")
BENCH = Path("data/benchmark/questions.jsonl")
DB = Path("data/corpus/civic.sqlite")


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def write_json(path, value):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n", encoding="utf8")
    tmp.replace(path)


def rank(scores):
    assert all(math.isfinite(v) for v in scores.values())
    return sorted(scores, key=lambda c: (-scores[c], c))


def rows(path, field):
    reader = gzip.open if path.suffix == ".gz" else open
    with reader(path, "rb") as stream:
        return {r[field]: r for r in map(json.loads, stream) if r[field] in CASES}


def summarize(order, groups):
    pos = {cid: i for i, cid in enumerate(order, 1)}
    first = [min((pos[c] for c in g["acceptable_chunk_ids"] if c in pos), default=None) for g in groups]
    metrics = {}
    for k in (10, 100):
        hits = [r for r in first if r is not None and r <= k]
        metrics[str(k)] = dict(
            found=len(hits),
            total=len(first),
            recall=len(hits) / len(first),
            hit=int(bool(hits)),
            all_evidence=int(len(hits) == len(first)),
            mrr=1 / min(hits) if hits else 0,
        )
    return dict(group_ranks=first, metrics=metrics)


def inputs(repo, tokenizer_file):
    from tokenizers import Tokenizer

    if digest(tokenizer_file) != TOKENIZER_SHA:
        raise ValueError("Tokenizer does not match the frozen revision")
    tokenizer = Tokenizer.from_file(str(tokenizer_file))
    tokenizer.no_padding()
    tokenizer.no_truncation()
    identity = json.loads((repo / BGE.parent / "bge.run.json").read_bytes())["identity"]
    expected = dict(
        model=MODEL,
        revision=REVISION,
        max_length=512,
        truncation="only_second",
        passage="title + newline + text",
        device="mps",
        dtype="float16",
    )
    if {field: identity[field] for field in expected} != expected:
        raise ValueError("The baseline must be the frozen Week 7 BGE run.")
    bench = rows(repo / BENCH, "question_id")
    source = rows(repo / BGE, "query_id")
    reference = rows(repo / DIRECT / "per_question.jsonl", "query_id")
    frozen = {q: {} for q in CASES}
    with (repo / DIRECT / "rankings.csv").open(encoding="utf8", newline="") as stream:
        for r in csv.DictReader(stream):
            if r["query_id"] in frozen:
                frozen[r["query_id"]][r["chunk_id"]] = (int(r["final_rank"]), float(r["bge_raw_score"]))
    texts, sizes, cpu_controls, target_ids, plan = {}, {}, set(), {}, {}
    with closing(sqlite3.connect((repo / DB).resolve().as_uri() + "?mode=ro", uri=True)) as db:
        for q, (target_group, n, long_n) in CASES.items():
            question = bench[q]["question"]
            groups = bench[q]["evidence_requirements"]
            records = frozen[q]
            order = rank({c: s for c, (r, s) in records.items()})
            assert len(order) == n and [records[c][0] for c in order] == list(range(1, n + 1))
            assert set(order) == set(source[q]["ranked_chunk_ids"])
            assert {r["chunk_id"]: r["score"] for r in source[q]["bge_scores"]} == {
                c: score for c, (_, score) in records.items()
            }
            base = summarize(order, groups)
            for k, met in base["metrics"].items():
                assert {f: met[f] for f in reference[q]["metrics"][k]} == reference[q]["metrics"][k]
            raw = {
                cid: (t, b, st)
                for cid, t, b, st in db.execute(
                    "select chunk_id,title,text,start_char from chunks where chunk_id in ("
                    + ",".join("?" for _ in order)
                    + ")",
                    order,
                )
            }
            assert set(raw) == set(order)
            pairs = [(question, raw[c][0] + "\n" + raw[c][1]) for c in order]
            full = tokenizer.encode_batch(pairs)
            lengths = [len(e.ids) for e in full]
            stats = source[q]["input_stats"]
            assert (n, sum(v > 512 for v in lengths), max(lengths), sum(lengths)) == (
                stats["pairs"],
                stats["truncated_pairs"],
                stats["original_max_tokens"],
                stats["original_total_tokens"],
            )
            assert sum(v > 512 for v in lengths) == long_n
            for c, pair, length in zip(order, pairs, lengths):
                texts[q, c] = pair
                sizes[q, c] = length
            target = groups[target_group - 1]
            targets = set(order) & set(target["acceptable_chunk_ids"])
            assert len(targets) == 1
            cid = targets.pop()
            target_ids[q] = cid
            ends = {}
            for k in (512, 1024):
                tokenizer.enable_truncation(max_length=k, strategy="only_second", direction="right")
                e = tokenizer.encode(*texts[q, cid])
                ends[k] = (
                    max(b for (a, b), i in zip(e.offsets, e.sequence_ids) if i == 1) - len(raw[cid][0]) - 1
                )
            tokenizer.no_truncation()
            start, end = target["start_char"] - raw[cid][2], target["end_char"] - raw[cid][2]
            assert raw[cid][1][start:end] == target["quote"] and ends[512] <= start < end <= ends[1024]
            # Paired 512 controls include all edited inputs. Extra controls locate CPU/MPS variation next to cutoffs.
            cpu_controls.update((q, c) for c in order if sizes[q, c] > 512)
            cpu_controls.update((q, order[i - 1]) for i in (1, 9, 10, 11, 99, 100, 101))
            cpu_controls.update((q, c) for g in groups for c in g["acceptable_chunk_ids"] if c in records)
            plan[q] = dict(
                candidates=n,
                changed_1024=long_n,
                over_1024=sum(z > 1024 for z in lengths),
                baseline=base,
                target_group=target_group,
                target_id=cid,
                quote_start=start,
                quote_end=end,
                visible_chars_512=ends[512],
                visible_chars_1024=ends[1024],
            )
    return bench, frozen, texts, sizes, cpu_controls, target_ids, plan


def read_log(path):
    scores = {}
    if path.is_file():
        with path.open("r+b") as f:
            while True:
                offset = f.tell()
                line = f.readline()
                if not line:
                    break
                if not line.endswith(b"\n"):
                    f.truncate(offset)
                    break
                r = json.loads(line)
                key = (r["query_id"], r["chunk_id"], r["max_length"])
                if key in scores or not math.isfinite(r["score"]):
                    raise ValueError("invalid score checkpoint entry")
                scores[key] = r["score"]
    return scores


def measure(cfg, tokfile, texts, sizes, controls):
    import torch
    import transformers
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    torch.set_num_threads(cfg.threads)
    torch.set_num_interop_threads(1)
    weight_hash = digest(cfg.model_dir / "model.safetensors")
    if weight_hash not in WEIGHTS:
        raise ValueError("Expected the pinned official checkpoint or the verified local FP16 conversion.")
    config_hashes = {name: digest(cfg.model_dir / name) for name in MODEL_CONFIG_HASHES}
    if config_hashes != MODEL_CONFIG_HASHES:
        raise ValueError("Model configuration/tokenizer files differ from the pinned snapshot.")
    provenance = {
        "model": MODEL,
        "revision": REVISION,
        "weight_sha256": weight_hash,
        "weight_storage": WEIGHTS[weight_hash],
        "tokenizer_sha256": digest(tokfile),
        "runtime_files": config_hashes,
        "case_inputs_sha256": hashlib.sha256(
            json.dumps(sorted((q, c, *p) for (q, c), p in texts.items()), ensure_ascii=False).encode()
        ).hexdigest(),
        "frozen_rankings_sha256": digest(cfg.repo / DIRECT / "rankings.csv"),
        "benchmark_sha256": digest(cfg.repo / BENCH),
        "torch": str(torch.__version__),
        "transformers": transformers.__version__,
        "compute": "cpu-fp32",
        "attention": "sdpa",
        "sort": "-raw_score,chunk_id",
        "truncation": "only_second",
        "passage": "title + newline + text",
        "lengths": [512, 1024],
        "threads": cfg.threads,
        "batch_size": cfg.batch_size,
    }
    meta = cfg.out / "provenance.json"
    log = cfg.out / "scores.jsonl"
    if meta.exists():
        if json.loads(meta.read_bytes()) != provenance:
            raise ValueError("Score checkpoint provenance differs.")
    else:
        if log.exists():
            raise ValueError("Scores without provenance")
        write_json(meta, provenance)
    scores = read_log(log)
    wanted = {(q, c, 1024) for (q, c), n in sizes.items() if n > 512} | {(q, c, 512) for q, c in controls}
    assert set(scores) <= wanted
    if set(scores) == wanted:
        print("Using complete score checkpoint", flush=True)
        return scores
    tok = AutoTokenizer.from_pretrained(
        str(cfg.model_dir), local_files_only=True, use_fast=True, trust_remote_code=False
    )
    # Build each truncation separately so 1024 always comes from the original full pair.
    examples = {}
    for k in (512, 1024):
        keys = sorted(i for i in wanted if i[2] == k)
        encoded = tok(
            [texts[q, c][0] for q, c, _ in keys],
            [texts[q, c][1] for q, c, _ in keys],
            padding=False,
            truncation="only_second",
            max_length=k,
        )
        for j, key in enumerate(keys):
            examples[key] = {field: items[j] for field, items in encoded.items()}
            assert len(examples[key]["input_ids"]) == min(sizes[key[:2]], k)
    model = AutoModelForSequenceClassification.from_pretrained(
        str(cfg.model_dir),
        local_files_only=True,
        trust_remote_code=False,
        use_safetensors=True,
        torch_dtype=torch.float32,
        attn_implementation="sdpa",
        low_cpu_mem_usage=True,
    ).eval()
    p = next(model.parameters())
    assert p.device.type == "cpu" and p.dtype == torch.float32 and model.config._attn_implementation == "sdpa"
    # Batch boundaries remain identical when resuming a partially completed run.
    keys = sorted(wanted, key=lambda k: (len(examples[k]["input_ids"]), k))
    print(f"Measuring {len(wanted) - len(scores)}/{len(wanted)} inputs", flush=True)
    with torch.inference_mode(), log.open("ab") as stream:
        for j in range(0, len(keys), cfg.batch_size):
            batch = keys[j : j + cfg.batch_size]
            if all(k in scores for k in batch):
                continue
            x = tok.pad([examples[k] for k in batch], padding=True, return_tensors="pt")
            val = model(**x, return_dict=True).logits
            assert tuple(val.shape) == (len(batch), 1)
            for (q, c, k), score in zip(batch, val[:, 0].float().tolist()):
                assert math.isfinite(score)
                if (q, c, k) not in scores:
                    stream.write(
                        (
                            json.dumps(
                                {"query_id": q, "chunk_id": c, "max_length": k, "score": score},
                                allow_nan=False,
                            )
                            + "\n"
                        ).encode()
                    )
                    scores[q, c, k] = score
            stream.flush()
            os.fsync(stream.fileno())
            if j % (cfg.batch_size * 5) == 0 or len(scores) == len(wanted):
                print(f"Scored {len(scores)}/{len(wanted)}", flush=True)
    assert set(scores) == wanted
    return scores


def finish(cfg, bench, frozen, sizes, controls, targets, plan, scores):
    result = {"cases": {}, "aggregate": {}}
    path = cfg.out / "rankings.csv"
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf8", newline="") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(
            (
                "query_id",
                "chunk_id",
                "input_tokens",
                "baseline_score",
                "baseline_rank",
                "cpu512_control",
                "score_1024",
                "rank_1024",
                "score_source",
                "evidence_groups",
            )
        )
        for q in CASES:
            groups = bench[q]["evidence_requirements"]
            base = {c: s for c, (r, s) in frozen[q].items()}
            old_rank = rank(base)
            new = {c: scores[q, c, 1024] if sizes[q, c] > 512 else base[c] for c in base}
            order = rank(new)
            # A sensitivity check isolates input-length differences from CPU/MPS baseline drift.
            delta_rank = rank(
                {
                    c: base[c] + scores[q, c, 1024] - scores[q, c, 512] if sizes[q, c] > 512 else base[c]
                    for c in base
                }
            )
            control = {c: scores[q, c, 512] for z, c in controls if z == q}
            errors = [abs(s - base[c]) for c, s in control.items()]
            cpu_base = rank({c: control.get(c, s) for c, s in base.items()})
            new_pos = {c: r for r, c in enumerate(order, 1)}
            result["cases"][q] = {
                **plan[q],
                "updated": summarize(order, groups),
                "delta_sensitivity": summarize(delta_rank, groups),
                "controls": {
                    "count": len(control),
                    "max_abs_error": max(errors),
                    "mean_abs_error": mean(errors),
                    "hybrid_cpu_baseline_top10_same": cpu_base[:10] == old_rank[:10],
                    "neighbours_at_target": [
                        {"rank": r, "cid": order[r - 1], "score": new[order[r - 1]]}
                        for r in (new_pos[targets[q]] - 1, new_pos[targets[q]], new_pos[targets[q]] + 1)
                        if 1 <= r <= len(order)
                    ],
                },
                "baseline_target_rank": frozen[q][targets[q]][0],
                "updated_target_rank": new_pos[targets[q]],
                "delta_sensitivity_target_rank": delta_rank.index(targets[q]) + 1,
                "baseline_target_score": base[targets[q]],
                "cpu512_target_score": scores[q, targets[q], 512],
                "updated_target_score": new[targets[q]],
            }
            for i, c in enumerate(order, 1):
                writer.writerow(
                    (
                        q,
                        c,
                        sizes[q, c],
                        base[c],
                        frozen[q][c][0],
                        control.get(c, ""),
                        new[c],
                        i,
                        "cpu_1024" if sizes[q, c] > 512 else "frozen_unchanged",
                        "|".join(str(j) for j, g in enumerate(groups, 1) if c in g["acceptable_chunk_ids"]),
                    )
                )
    tmp.replace(path)
    for key in ("baseline", "updated", "delta_sensitivity"):
        by = defaultdict(list)
        for v in result["cases"].values():
            for k, met in v[key]["metrics"].items():
                by[k].append(met)
        result["aggregate"][key] = {
            k: {
                f: (mean(m[f] for m in items) if f in ("recall", "mrr") else sum(m[f] for m in items))
                for f in items[0]
            }
            for k, items in by.items()
        }
    write_json(cfg.out / "result.json", result)
    for q, v in result["cases"].items():
        print(
            q,
            v["baseline"]["group_ranks"],
            "->",
            v["updated"]["group_ranks"],
            "complete@10",
            v["updated"]["metrics"]["10"]["all_evidence"],
            flush=True,
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--model-dir", type=Path, default=os.environ.get("CIVIC_BGE_MODEL_DIR"))
    parser.add_argument(
        "--tokenizer-file", type=Path, help="Pinned tokenizer JSON for --plan-only without weights"
    )
    parser.add_argument("--output-dir", type=Path, required=True, dest="out")
    parser.add_argument(
        "--packages", type=Path, help="Optional isolated dependency directory, prepended to sys.path"
    )
    parser.add_argument(
        "--extra-site",
        type=Path,
        action="append",
        default=[],
        help="Additional package directory, appended to sys.path",
    )
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--plan-only", action="store_true")
    cfg = parser.parse_args()
    if min(cfg.threads, cfg.batch_size) < 1:
        parser.error("threads and batch size must be positive")
    if not cfg.model_dir and not (cfg.plan_only and cfg.tokenizer_file):
        parser.error("Supply --model-dir or CIVIC_BGE_MODEL_DIR")
    cfg.repo = cfg.repo.resolve()
    cfg.out.mkdir(parents=True, exist_ok=True)
    if cfg.packages:
        sys.path.insert(0, str(cfg.packages))
    sys.path.extend(str(p) for p in cfg.extra_site)
    os.environ.update(
        HF_HUB_OFFLINE="1",
        TRANSFORMERS_OFFLINE="1",
        TOKENIZERS_PARALLELISM="false",
        OMP_NUM_THREADS=str(cfg.threads),
    )
    tokfile = cfg.tokenizer_file if cfg.plan_only and cfg.tokenizer_file else cfg.model_dir / "tokenizer.json"
    bench, frozen, texts, sizes, controls, targets, plan = inputs(cfg.repo, tokfile)
    print(
        "Candidates",
        len(texts),
        "changed",
        sum(n > 512 for n in sizes.values()),
        "controls",
        len(controls),
        flush=True,
    )
    from filelock import FileLock

    with FileLock(str(cfg.out / ".run.lock"), timeout=0):
        write_json(cfg.out / "plan.json", plan)
        if cfg.plan_only:
            return
        scores = measure(cfg, tokfile, texts, sizes, controls)
        finish(cfg, bench, frozen, sizes, controls, targets, plan, scores)


if __name__ == "__main__":
    main()
