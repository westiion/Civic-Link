"""Compare final BGE/Graph weighted RRF on the frozen Week 7 candidate pool."""
import argparse
import csv
import json
from fractions import Fraction
from pathlib import Path

from week7_hypothesis_support import BASELINE, QUESTIONS, ROOT, SOURCE, direct_order, load_frozen
from civic_link.common import read_json, read_jsonl
from civic_link.reranking.policies import rank_metrics


def weighted_order(bge, graph, graph_percent):
    if not graph_percent:
        return bge
    scores = {}
    for base_rank, chunk in enumerate(bge, 1):
        score = Fraction(100 - graph_percent, 60 + base_rank)
        if chunk in graph:
            score += Fraction(graph_percent, 60 + graph[chunk])
        scores[chunk] = score
    # bge is already in baseline order; stable sorting retains it on exact ties.
    return sorted(bge, key=scores.__getitem__, reverse=True)


def evaluate(output, percentages):
    _, scores, _ = load_frozen()
    type1 = set(read_json(ROOT / "week7/failure_analysis/results/type1-fields/result.json")["selection"])
    questions = {r["question_id"]: r for r in read_jsonl(QUESTIONS) if r["retrieval_metric_eligible"]}
    original = {r["query_id"]: r["metrics"] for r in read_jsonl(BASELINE.parent / "per_question.jsonl")}
    rows = []
    seen = set()
    for trace in read_jsonl(SOURCE / "h3.jsonl"):
        q = trace["query_id"]
        if q not in questions:
            continue
        if q in seen:
            raise ValueError(f"Duplicate query {q}")
        seen.add(q)
        bge = direct_order(scores[q])
        retained = [c for c in trace["candidates"] if c["fusion_rank"] is not None]
        assert set(bge) == {c["chunk_id"] for c in retained}
        graph = {c["chunk_id"]:c["graph_rank"] for c in retained if c["graph_rank"] is not None and c["graph_rank"] <= 100}
        gold = [group["acceptable_chunk_ids"] for group in questions[q]["evidence_requirements"]]
        for pct in percentages:
            metrics = rank_metrics(gold, weighted_order(bge, graph, pct))
            row = dict(query_id=q, type1=int(q in type1), graph_percent=pct, pool_size=len(bge),
                       group_ranks=json.dumps(metrics["group_ranks"], separators=(",",":")))
            for k in ("10","100"):
                for field in ("all_evidence","recall","mrr"):
                    row[f"{field}_{k}"] = metrics[k][field]
                    if pct == 0:
                        assert row[f"{field}_{k}"] == original[q][k][field]
            rows.append(row)
    assert seen == set(questions) and len(seen) == 488 and len(type1) == 14 and type1 <= seen
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w",encoding="utf8",newline="") as stream:
        writer = csv.DictWriter(stream,fieldnames=rows[0],lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--graph-percent",nargs="+",type=int,default=[0,5,10,15,20,30,40])
    args=parser.parse_args()
    if any(v < 0 or v > 100 for v in args.graph_percent):
        parser.error("Graph percentages must be between 0 and 100.")
    evaluate(args.output, sorted({0, *args.graph_percent}))
